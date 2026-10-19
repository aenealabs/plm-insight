"""FastAPI web app: data quality scorecard, steward and Trade Compliance queues, change list."""

import csv
import io
import json
import re
from collections import Counter
from datetime import date
from urllib.parse import urlencode, urlparse

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from mission_control import STATIC_PATH, install, install_help
from sqlalchemy.engine import Engine
from sqlmodel import Session

from plm_insight import audit_search, eco_routes
from plm_insight.auth import (
    AuthSettings,
    User,
    load_auth_settings,
    user_from_claims,
    user_from_session,
    user_to_session,
)
from plm_insight.config import PACKAGE_DIR, Settings, load_settings
from plm_insight.db import audit, make_engine
from plm_insight.eco import EcoStore
from plm_insight.eco_ai import EcoAiStore
from plm_insight.help_reference import reference
from plm_insight.llm import LLMError, LLMProvider, build_provider
from plm_insight.quality import (
    ACCEPTED,
    OPEN,
    REJECTED,
    ComplianceRequired,
    QualityError,
    QualityStore,
)
from plm_insight.review import KINDS, ReviewStore, checked, review_input
from plm_insight.rules import RULE_NAMES, RULES
from plm_insight.suite import SuiteClient, SuiteError, SuiteSettings, load_suite_settings

USER_COOKIE = "pi_user"
# Each screen's help button opens the guide section about it (checked at start-up).
PAGE_HELP = {
    "scorecard.html": "scorecard#reading-the-scorecard",
    "issues.html": "findings#working-the-queue",
    "issue_detail.html": "findings#deciding",
    "part.html": "findings#parts",
    "changes.html": "change-list#what-is-in-it",
    "data.html": "data#part-master-and-bom",
    "ecos.html": "ecos#measures",
    "eco_detail.html": "ecos#where-is-my-eco",
    "audit.html": "audit#searching",
    "restricted.html": "overview#who-does-what",
}
PAGE_SIZE = 50
ITEM_KEYS = re.compile(r"ISS-\d+|\d{3}-\d{6}", re.IGNORECASE)


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else many or one + 's'}"


def _highlight(value: str, terms: list[str]) -> Markup:
    escaped = str(escape(value))
    if not terms:
        return Markup(escaped)
    pattern = re.compile("|".join(re.escape(str(escape(t))) for t in terms), re.IGNORECASE)
    return Markup(pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", escaped))


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def create_app(settings: Settings | None = None, engine: Engine | None = None,
               auth: AuthSettings | None = None, provider: LLMProvider | None = None,
               suite: SuiteSettings | None = None, suite_client: SuiteClient | None = None
               ) -> FastAPI:
    settings = settings or load_settings()
    provider = provider or build_provider(settings)
    suite = suite or load_suite_settings()
    suite_client = suite_client or SuiteClient(suite)
    ai_label = ("AI review (recorded demo)" if provider.name == "replay"
                else f"AI review ({settings.model})")
    engine = engine or make_engine(settings.db_path)
    auth = auth or load_auth_settings()
    store = QualityStore(engine)
    store.seed_if_empty(settings.dataset_dir, "Demo export (seed)")
    reviews = ReviewStore(engine)
    eco_store = EcoStore(engine)
    eco_store.seed_if_empty(settings.dataset_dir, "Demo export (seed)")
    raw_users = json.loads(settings.users_path.read_text())["users"]
    users = [User(u["id"], u["name"], u["role"], u.get("approver", False),
                  compliance=u.get("compliance", False)) for u in raw_users]

    app = FastAPI(title="PLM Insight")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy",
                                    "camera=(), microphone=(), geolocation=()")
        return response

    if auth.mode == "oidc":
        _enable_oidc(app, auth)
    app.state.engine = engine
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    templates.env.globals.update(users=users, auth_mode=auth.mode, rule_names=RULE_NAMES,
                                 rules=RULES, provider_name=provider.name,
                                 suite_connected=suite.connected)
    install(app, templates, "PLM Insight", suite.apps)
    templates.env.filters.update(
        plural=plural, highlight_terms=_highlight, from_json=json.loads,
        dict_without=lambda params, key: {k: v for k, v in params.items() if k != key})

    def current_user(request: Request) -> User:
        if auth.mode == "oidc":
            user = user_from_session(request.session.get("user"))
            return user or User("anonymous", "Signed out", "", False)
        wanted = request.cookies.get(USER_COOKIE)
        return next((u for u in users if u.id == wanted), users[0])

    def actor(request: Request) -> str:
        return current_user(request).actor

    def render(request: Request, name: str, status_code: int = 200, **context) -> HTMLResponse:
        return templates.TemplateResponse(
            request, name, {"nav": name, "user": current_user(request), **context},
            status_code=status_code)

    install_help(app, templates, render, PACKAGE_DIR / "help", reference=reference,
                 page_help=PAGE_HELP)

    def restricted(request: Request, what: str) -> HTMLResponse | None:
        """Decisions and imports need a data steward (approver)."""
        if current_user(request).approver:
            return None
        with Session(engine) as s:
            audit(s, actor(request), "access_denied", what)
            s.commit()
        return render(request, "restricted.html", status_code=403, what=what)

    # ---------- scorecard ----------

    @app.get("/", response_class=HTMLResponse)
    def scorecard(request: Request, message: str = "", notice: str = ""):
        card = store.scorecard()
        ds = card.get("dataset")
        detection = ds.detection_data if ds else None
        return render(request, "scorecard.html", card=card, trend=store.trend(),
                      detection=detection, message=message, notice=notice)

    # ---------- issues ----------

    @app.get("/issues", response_class=HTMLResponse)
    def issue_list(request: Request, rule: str = "", unit: str = "", status: str = OPEN,
                   queue: str = "steward", page: int = 1):
        status = status if status in (OPEN, ACCEPTED, REJECTED, "") else OPEN
        queue = queue if queue in ("steward", "compliance") else "steward"
        rows = store.issues(rule=rule, unit=unit, status=status, queue=queue)
        page = max(page, 1)
        params = {"rule": rule, "unit": unit, "status": status, "queue": queue}
        ds = store.dataset()
        units = sorted({u["unit"] for u in store.scorecard().get("units", [])})
        return render(request, "issues.html", issues=rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE],
                      total=len(rows), page=page, page_size=PAGE_SIZE, params=params,
                      query_string=lambda **kw: urlencode(params | kw), units=units,
                      queue=queue, dataset=ds)

    @app.get("/compliance")
    def compliance_queue():
        return RedirectResponse("/issues?queue=compliance", status_code=303)

    def issue_view(request: Request, issue_id: int, message: str = "", notice: str = "",
                   status_code: int = 200) -> HTMLResponse:
        issue = store.issue(issue_id)
        parts = store.parts(issue.part_list, issue.dataset_id)
        usage = {pn: store.where_used(pn, issue.dataset_id) for pn in issue.part_list}
        latest = reviews.latest(issue.id) if issue.rule in KINDS else None
        return render(request, "issue_detail.html", issue=issue, parts=parts, usage=usage,
                      evidence=issue.evidence_data, fix=issue.fix_data, message=message,
                      notice=notice, status_code=status_code, ai_kind=KINDS.get(issue.rule),
                      ai=checked(latest, issue) if latest else None)

    @app.get("/issues/{issue_id}", response_class=HTMLResponse)
    def issue_page(request: Request, issue_id: int, done: str = ""):
        try:
            return issue_view(request, issue_id, notice=done[:80])
        except QualityError as e:
            return scorecard(request, message=str(e))

    @app.post("/issues/{issue_id}/decide", response_class=HTMLResponse)
    def decide(request: Request, issue_id: int, decision: str = Form(...),
               value: str = Form(""), reason: str = Form(""), change_reason: str = Form("")):
        if (denied := restricted(request, "data quality decision")) is not None:
            return denied
        user = current_user(request)
        try:
            issue = store.decide(issue_id, decision, actor(request), compliance=user.compliance,
                                 reason=change_reason if decision == ACCEPTED else reason,
                                 value=value)
        except ComplianceRequired as e:
            with Session(engine) as s:
                audit(s, actor(request), "access_denied", f"issue {issue_id}: {e}")
                s.commit()
            return issue_view(request, issue_id, str(e), status_code=403)
        except QualityError as e:
            return issue_view(request, issue_id, str(e), status_code=422)
        nxt = next((i for i in store.issues(rule=issue.rule, status=OPEN,
                                            queue=issue.queue)), None)
        notice = f"{issue.key} {issue.status}."
        if nxt is not None:
            return RedirectResponse(f"/issues/{nxt.id}?{urlencode({'done': notice})}",
                                    status_code=303)
        return RedirectResponse(f"/issues/{issue.id}", status_code=303)

    @app.post("/issues/{issue_id}/ai", response_class=HTMLResponse)
    def ai_review(request: Request, issue_id: int):
        if (denied := restricted(request, "AI review")) is not None:
            return denied
        try:
            issue = store.issue(issue_id)
        except QualityError as e:
            return scorecard(request, message=str(e))
        if issue.rule not in KINDS:
            return issue_view(request, issue_id, "AI review is available for duplicates and "
                                                 "export classification reviews.")
        if issue.queue == "compliance" and not current_user(request).compliance:
            with Session(engine) as s:
                audit(s, actor(request), "access_denied", f"{issue.key}: review packet")
                s.commit()
            return issue_view(request, issue_id, "Only Trade Compliance can request a review "
                                                 "packet.", status_code=403)
        text = review_input(store, issue)
        try:
            output = (provider.review_duplicates(text) if issue.rule == "DQ-DUP"
                      else provider.compliance_packet(text))
        except LLMError as e:
            return issue_view(request, issue_id, str(e))
        reviews.save(issue, text, output, actor(request), ai_label)
        return RedirectResponse(f"/issues/{issue_id}#ai", status_code=303)

    @app.post("/rules/{rule}/send", response_class=HTMLResponse)
    def send_to_backlog(request: Request, rule: str):
        """A rule that keeps firing is a missing control in PLM: hand the prevention work to
        Backlog Pilot's intake inbox."""
        if (denied := restricted(request, "send to Backlog Pilot")) is not None:
            return denied
        card = store.scorecard()
        row = next((r for r in card.get("rules", []) if r["rule"] == rule), None)
        if row is None:
            return scorecard(request, message="Unknown rule.")
        ds = card["dataset"]
        units = [u for u in store.issues(rule=rule, status="")]
        by_unit = Counter(i.business_unit for i in units)
        text = (f"PLM Insight rule {rule} ({row['name']}) found {len(units)} cases in the "
                f"export '{ds.name}' ({ds.parts:,} parts): "
                + ", ".join(f"{u} {n}" for u, n in by_unit.most_common())
                + f". Rule: {row['text']}. {row['accepted']} fixes accepted and "
                  f"{row['rejected']} findings rejected so far. Cleanup alone will not stop new "
                  "cases; the request is a control in PLM that prevents them at creation or "
                  "release.")
        try:
            item_id = suite_client.send(f"Prevent: {row['name'].lower()} in PLM", text, rule,
                                        f"/?rule={rule}", current_user(request).name)
        except SuiteError as e:
            return scorecard(request, message=str(e))
        with Session(engine) as s:
            audit(s, actor(request), "issue_sent_to_backlog",
                  f"{rule} {row['name']}: inbox item {item_id}")
            s.commit()
        return scorecard(request, notice=f"Sent {rule} to Backlog Pilot's intake inbox.")

    eco_routes.register(app, eco_routes.Deps(
        render=render, restricted=restricted, actor=actor,
        user_name=lambda r: current_user(r).name, provider=provider, ai_label=ai_label,
        ecos=eco_store, ai=EcoAiStore(engine), suite_client=suite_client, engine=engine))

    # ---------- parts ----------

    @app.get("/parts/{number}", response_class=HTMLResponse)
    def part_page(request: Request, number: str):
        ds = store.dataset()
        part = store.parts([number], ds.id).get(number) if ds else None
        return render(request, "part.html", number=number, part=part,
                      used_in=store.where_used(number, ds.id) if ds else [],
                      children=store.children(number, ds.id) if ds else [],
                      issues=store.issues_for_part(number, ds.id) if ds else [],
                      status_code=200 if part else 404)

    # ---------- change list ----------

    @app.get("/changes", response_class=HTMLResponse)
    def changes(request: Request):
        return render(request, "changes.html", rows=store.change_list(),
                      dataset=store.dataset())

    @app.get("/changes.csv")
    def changes_csv(request: Request):
        if (denied := restricted(request, "change list export")) is not None:
            return denied
        rows = store.change_list()
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=["issue", "rule", "object", "action", "old",
                                                 "new", "decided_by", "decided_at"])
        writer.writeheader()
        writer.writerows(rows)
        with Session(engine) as s:
            audit(s, actor(request), "changelist_exported", f"{len(rows)} rows")
            s.commit()
        return Response(out.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": 'attachment; filename="plm-change-list.csv"'})

    # ---------- data ----------

    @app.get("/data", response_class=HTMLResponse)
    def data_page(request: Request, message: str = "", status_code: int = 200):
        return render(request, "data.html", datasets=store.datasets(),
                      eco_datasets=eco_store.datasets(), message=message,
                      status_code=status_code)

    @app.post("/data", response_class=HTMLResponse)
    async def import_data(request: Request, parts: UploadFile, bom: UploadFile,
                          name: str = Form(""), controlled: str = Form("")):
        if (denied := restricted(request, "data import")) is not None:
            return denied
        try:
            units = [u.strip() for u in controlled.split(",") if u.strip()]
            store.load((await parts.read()).decode("utf-8-sig"),
                       (await bom.read()).decode("utf-8-sig"),
                       name.strip() or parts.filename or "Imported export", units,
                       actor(request))
        except UnicodeDecodeError:
            return data_page(request, "Upload UTF-8 CSV files.", 422)
        except QualityError as e:
            return data_page(request, str(e), 422)
        return RedirectResponse("/", status_code=303)

    # ---------- audit, user ----------

    @app.get("/audit", response_class=HTMLResponse)
    def audit_log(request: Request, q: str = "", actor_filter: str = "", category: str = "",
                  item: str = "", since: str = "", until: str = "", page: int = 1):
        if (denied := restricted(request, "audit log")) is not None:
            return denied
        page_size = 50
        query = audit_search.AuditQuery(
            text=q, actor=actor_filter, categories=[category] if category else [],
            item_keys=[k.upper() for k in ITEM_KEYS.findall(item)],
            since=_parse_date(since), until=_parse_date(until))
        results = audit_search.search(engine, query, limit=page_size,
                                      offset=(max(page, 1) - 1) * page_size)
        return render(request, "audit.html", events=results.events, total=results.total,
                      page=max(page, 1), page_size=page_size, query=query, ask="",
                      plan=None, answer=None, cited=[], unverified=[], summary={},
                      message="", questions=[], people=audit_search.actors(engine),
                      categories=audit_search.CATEGORY_LABELS,
                      terms=[t for t in re.findall(r"\w+", query.text) if len(t) > 1])

    @app.post("/user")
    def switch_user(request: Request, user_id: str = Form(...)):
        back = urlparse(request.headers.get("referer", "")).path
        if not back.startswith("/") or back.startswith("//"):
            back = "/"
        response = RedirectResponse(back, status_code=303)
        if any(u.id == user_id for u in users):
            response.set_cookie(USER_COOKIE, user_id, httponly=True, samesite="lax")
        return response

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


PUBLIC_PATHS = ("/static", STATIC_PATH, "/login", "/auth/callback", "/logout", "/healthz")


def _enable_oidc(app: FastAPI, auth: AuthSettings) -> None:
    """Sign-in with Microsoft Entra ID (or any OpenID Connect provider)."""
    from authlib.integrations.starlette_client import OAuth
    from starlette.middleware.sessions import SessionMiddleware

    oauth = OAuth()
    oauth.register("oidc", server_metadata_url=auth.metadata_url, client_id=auth.client_id,
                   client_secret=auth.client_secret,
                   client_kwargs={"scope": "openid profile email"})

    @app.middleware("http")
    async def require_sign_in(request: Request, call_next):
        if request.url.path.startswith(PUBLIC_PATHS) or request.session.get("user"):
            return await call_next(request)
        if request.method == "GET" and "hx-request" not in request.headers:
            request.session["next"] = request.url.path  # a sign-in prompt, not a refusal
            return RedirectResponse("/login", status_code=303)
        with Session(request.app.state.engine) as s:  # an action tried without signing in
            audit(s, "not signed in", "access_denied",
                  f"{request.method} {request.url.path} from "
                  f"{request.client.host if request.client else 'unknown'}")
            s.commit()
        return HTMLResponse("Sign in required.", status_code=401)

    app.add_middleware(SessionMiddleware, secret_key=auth.session_secret, same_site="lax",
                       https_only=auth.https_only, max_age=8 * 3600)

    @app.get("/login")
    async def login(request: Request):
        return await oauth.oidc.authorize_redirect(request, request.url_for("auth_callback"))

    @app.get("/auth/callback", name="auth_callback")
    async def auth_callback(request: Request):
        try:
            token = await oauth.oidc.authorize_access_token(request)  # verifies the ID token
            user = user_from_claims(token["userinfo"], auth)
        except Exception as e:  # any failure (state, token, claims) is refused and audited
            with Session(request.app.state.engine) as s:
                audit(s, "not signed in", "sign_in_failed",
                      f"{type(e).__name__} from "
                      f"{request.client.host if request.client else 'unknown'}")
                s.commit()
            return HTMLResponse('Sign-in failed. <a href="/login">Try again</a>.',
                                status_code=401)
        request.session["user"] = user_to_session(user)
        with Session(request.app.state.engine) as s:
            audit(s, user.actor, "signed_in", user.email or user.id)
            s.commit()
        return RedirectResponse(request.session.pop("next", "/"), status_code=303)

    @app.get("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)


def main() -> None:
    import uvicorn

    uvicorn.run("plm_insight.app:create_app", factory=True, host="127.0.0.1", port=8020)


if __name__ == "__main__":
    main()
