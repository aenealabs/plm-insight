"""Routes for ECO analytics: the dashboard, one ECO's history, AI return themes, imports."""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from plm_insight.db import audit
from plm_insight.eco import ECO_RULES, STAGES, EcoStore, returns_input, returns_window
from plm_insight.eco_ai import EcoAiStore, check
from plm_insight.llm import LLMError, LLMProvider
from plm_insight.models import ReturnAnalysis
from plm_insight.quality import QualityError
from plm_insight.suite import SuiteClient, SuiteError


@dataclass
class Deps:
    render: Callable[..., HTMLResponse]
    restricted: Callable[[Request, str], HTMLResponse | None]
    actor: Callable[[Request], str]
    user_name: Callable[[Request], str]
    provider: LLMProvider
    ai_label: str
    ecos: EcoStore
    ai: EcoAiStore
    suite_client: SuiteClient
    engine: object


def window_label(returns) -> str:
    if not returns:
        return "no returns"
    first, last = returns[0].left, returns[-1].left
    ecos = len({r.eco for r in returns})
    return f"{first:%b %Y} to {last:%b %Y}, {len(returns)} returns on {ecos} ECOs"


def register(app: FastAPI, d: Deps) -> None:

    def themes_for(ds):
        ecos, events = d.ecos.rows()
        returns = returns_window(events, ecos, ds.as_of)
        latest = d.ai.latest(ds.id)
        if latest is None:
            return returns, None
        return returns, check(ReturnAnalysis.model_validate_json(latest.output), returns,
                              latest)

    def dashboard(request: Request, q: str = "", message: str = "", notice: str = "",
                  status_code: int = 200) -> HTMLResponse:
        ds = d.ecos.dataset()
        a = d.ecos.analytics()
        returns, themes = themes_for(ds) if ds else ([], None)
        return d.render(request, "ecos.html", ds=ds, a=a, q=q, results=d.ecos.search(q),
                        stages=STAGES, eco_rules=ECO_RULES, themes=themes,
                        window=window_label(returns), message=message, notice=notice,
                        status_code=status_code)

    @app.get("/ecos", response_class=HTMLResponse)
    def ecos_page(request: Request, q: str = ""):
        return dashboard(request, q.strip()[:80])

    @app.get("/ecos/{key}", response_class=HTMLResponse)
    def eco_page(request: Request, key: str):
        eco, events = d.ecos.eco(key)
        a = d.ecos.analytics()
        ds = d.ecos.dataset()
        return d.render(request, "eco_detail.html", eco=eco, key=key, events=events,
                        a=a, as_of=ds.as_of if ds else None,
                        status_code=200 if eco else 404)

    @app.post("/ecos/why", response_class=HTMLResponse)
    def why_returned(request: Request):
        if (denied := d.restricted(request, "ECO return analysis")) is not None:
            return denied
        ds = d.ecos.dataset()
        if ds is None:
            return dashboard(request, message="Load an ECO history first.")
        ecos, events = d.ecos.rows()
        returns = returns_window(events, ecos, ds.as_of)
        if not returns:
            return dashboard(request, message="No ECOs were returned in the last six months.")
        text = returns_input(returns, ecos)
        try:
            result = d.provider.return_themes(text)
        except LLMError as e:
            return dashboard(request, message=str(e))
        d.ai.save(ds.id, window_label(returns), text, result, d.actor(request), d.ai_label)
        return RedirectResponse("/ecos#why", status_code=303)

    @app.post("/ecos/themes/{index}/send", response_class=HTMLResponse)
    def send_theme(request: Request, index: int):
        if (denied := d.restricted(request, "send to Backlog Pilot")) is not None:
            return denied
        ds = d.ecos.dataset()
        _, themes = themes_for(ds) if ds else ([], None)
        if themes is None or not 0 <= index < len(themes.themes):
            return dashboard(request, message="That theme is no longer current.")
        t = themes.themes[index]
        quotes = "\n".join(f'- {eco}: "{q}"' for eco, q, ok in t.quotes if ok)
        period = themes.record.window.split(",")[0]
        text = (f"{t.count} of {themes.total} ECO returns in {period} were for this reason "
                f"({t.share}%): {t.theme.description}\n\n"
                f"Proposed prevention: {t.theme.prevention}\n\nReviewer comments:\n{quotes}")
        try:
            item = d.suite_client.send(f"Stop ECO returns: {t.theme.name}", text,
                                       f"ECO-THEME-{index + 1}", "/ecos#why",
                                       d.user_name(request))
        except SuiteError as e:
            return dashboard(request, message=str(e))
        with Session(d.engine) as s:
            audit(s, d.actor(request), "eco_theme_sent_to_backlog",
                  f"{t.theme.name}: {t.count} ECOs; inbox item {item}")
            s.commit()
        return dashboard(request, notice=f"Sent '{t.theme.name}' to Backlog Pilot's intake "
                                         "inbox.")

    @app.post("/data/ecos", response_class=HTMLResponse)
    async def import_ecos(request: Request, ecos: UploadFile, events: UploadFile,
                          name: str = Form("")):
        if (denied := d.restricted(request, "data import")) is not None:
            return denied
        try:
            d.ecos.load((await ecos.read()).decode("utf-8-sig"),
                        (await events.read()).decode("utf-8-sig"),
                        name.strip() or ecos.filename or "Imported ECO history", None,
                        d.actor(request))
        except UnicodeDecodeError:
            return dashboard(request, message="Upload UTF-8 CSV files.", status_code=422)
        except (QualityError, ValueError) as e:
            return dashboard(request, message=str(e), status_code=422)
        return RedirectResponse("/ecos", status_code=303)
