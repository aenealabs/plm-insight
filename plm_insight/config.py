"""Runtime settings, read from environment variables (and an optional .env file)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_DIR = PACKAGE_DIR.parent
DATA_DIR = REPO_DIR / "data"


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    db_path: str
    dataset_dir: Path
    users_path: Path
    llm_provider: str = "replay"
    model: str = "claude-opus-5-5"
    record: bool = False
    replay_dir: Path = DATA_DIR / "replays"


def load_settings() -> Settings:
    load_dotenv(REPO_DIR / ".env")
    return Settings(
        db_path=os.getenv("PI_DB_PATH", str(REPO_DIR / "plm_insight.db")),
        dataset_dir=DATA_DIR / "demo",
        users_path=DATA_DIR / "users.json",
        llm_provider=os.getenv("PI_LLM_PROVIDER", "replay").strip().lower(),
        model=os.getenv("PI_MODEL", "claude-opus-5-5"),
        record=_flag("PI_RECORD"),
    )
