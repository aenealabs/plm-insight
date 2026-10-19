"""Mission Control: links to the other apps, and the server-to-server call that hands
prevention work to Backlog Pilot's intake inbox (shared token in demo; Entra app-to-app
tokens in production)."""

import os
from dataclasses import dataclass

import httpx


class SuiteError(Exception):
    """Backlog Pilot could not be reached or refused the request; user-facing."""


@dataclass(frozen=True)
class SuiteSettings:
    backlog_url: str = ""
    leadership_url: str = ""
    token: str = ""
    public_url: str = ""  # where this app runs, for links back from Backlog Pilot

    @property
    def connected(self) -> bool:
        return bool(self.backlog_url and self.token)

    @property
    def apps(self) -> list[tuple[str, str]]:
        return [(name, url) for name, url in (("Backlog Pilot", self.backlog_url),
                                              ("Leadership Pilot", self.leadership_url)) if url]


def load_suite_settings() -> SuiteSettings:
    return SuiteSettings(os.getenv("PI_BACKLOG_URL", "").rstrip("/"),
                         os.getenv("PI_LEADERSHIP_URL", "").rstrip("/"),
                         os.getenv("PI_SUITE_TOKEN", ""),
                         os.getenv("PI_PUBLIC_URL", "http://127.0.0.1:8020").rstrip("/"))


class SuiteClient:
    def __init__(self, settings: SuiteSettings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.http = httpx.Client(base_url=settings.backlog_url or "http://unset", timeout=15.0,
                                 headers={"Authorization": f"Bearer {settings.token}"},
                                 transport=transport)

    def send(self, title: str, text: str, ref: str, path: str, submitted_by: str) -> int:
        if not self.settings.connected:
            raise SuiteError("Backlog Pilot is not connected. Set PI_BACKLOG_URL and "
                             "PI_SUITE_TOKEN.")
        body = {"title": title, "text": text, "source_ref": ref,
                "source_url": f"{self.settings.public_url}{path}", "stakeholders": [],
                "submitted_by": submitted_by,
                "source_app": "PLM Insight"}
        try:
            response = self.http.post("/api/inbox", json=body)
        except httpx.HTTPError as e:
            raise SuiteError(f"Could not reach Backlog Pilot: {e}") from e
        if not response.is_success:
            raise SuiteError(f"Backlog Pilot returned HTTP {response.status_code}.")
        return response.json()["id"]
