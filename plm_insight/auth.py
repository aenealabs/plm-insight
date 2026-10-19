"""Who is using the app, and whether they may make decisions.

Two modes:
- demo (default): pick a fictional user from the app bar; no sign-in.
- oidc: sign in with Microsoft Entra ID (or any OpenID Connect provider). The user's name and
  email come from the ID token; approval rights come from an app role in the token's `roles`
  claim (Entra app roles), optionally from an allow-list of approver emails.

Anyone signed in can browse findings and the scorecard. Data steward decisions (accepting or
rejecting a proposed fix, importing an export) need the approver role. Export classification
decisions need the Trade Compliance role, which approvers do not have by default.
"""

import os
from dataclasses import dataclass

APPROVER_ROLE_DEFAULT = "PLMInsight.Approver"
COMPLIANCE_ROLE_DEFAULT = "PLMInsight.TradeCompliance"


@dataclass(frozen=True)
class User:
    id: str
    name: str
    role: str  # job title shown in the app bar and recorded as the actor
    approver: bool
    email: str = ""
    compliance: bool = False  # may decide export classification reviews

    @property
    def actor(self) -> str:
        return f"{self.name} ({self.role})" if self.role else self.name


@dataclass(frozen=True)
class AuthSettings:
    mode: str  # demo | oidc
    metadata_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    session_secret: str = ""
    approver_role: str = APPROVER_ROLE_DEFAULT
    approver_emails: frozenset[str] = frozenset()
    https_only: bool = True
    compliance_role: str = COMPLIANCE_ROLE_DEFAULT


def load_auth_settings() -> AuthSettings:
    mode = os.getenv("PI_AUTH", "demo").strip().lower()
    if mode != "oidc":
        return AuthSettings("demo")
    missing = [v for v in ("PI_OIDC_METADATA_URL", "PI_OIDC_CLIENT_ID", "PI_OIDC_CLIENT_SECRET",
                           "PI_SESSION_SECRET") if not os.getenv(v)]
    if missing:
        raise RuntimeError(f"PI_AUTH=oidc needs {', '.join(missing)}")
    emails = {e.strip().lower() for e in os.getenv("PI_APPROVER_EMAILS", "").split(",")
              if e.strip()}
    return AuthSettings(
        "oidc", os.environ["PI_OIDC_METADATA_URL"], os.environ["PI_OIDC_CLIENT_ID"],
        os.environ["PI_OIDC_CLIENT_SECRET"], os.environ["PI_SESSION_SECRET"],
        os.getenv("PI_APPROVER_ROLE", APPROVER_ROLE_DEFAULT), frozenset(emails),
        os.getenv("PI_SESSION_HTTPS_ONLY", "true").lower() != "false",
        os.getenv("PI_COMPLIANCE_ROLE", COMPLIANCE_ROLE_DEFAULT))


def user_from_claims(claims: dict, settings: AuthSettings) -> User:
    """Map verified ID-token claims to a user. Entra puts app roles in `roles`."""
    email = (claims.get("email") or claims.get("preferred_username") or "").lower()
    roles = claims.get("roles") or []
    approver = settings.approver_role in roles or email in settings.approver_emails
    compliance = settings.compliance_role in roles
    title = "Trade Compliance" if compliance else "Approver" if approver else "Contributor"
    return User(id=claims.get("oid") or claims["sub"], name=claims.get("name") or email,
                role=title, approver=approver or compliance, email=email,
                compliance=compliance)


def user_to_session(user: User) -> dict:
    return {"id": user.id, "name": user.name, "role": user.role, "approver": user.approver,
            "email": user.email, "compliance": user.compliance}


def user_from_session(data: dict | None) -> User | None:
    if not data:
        return None
    return User(data["id"], data["name"], data["role"], bool(data["approver"]),
                data.get("email", ""))
