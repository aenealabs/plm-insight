"""Offline provider: serves recorded responses so demos never depend on the network."""

import json
from pathlib import Path

from pydantic import BaseModel

from plm_insight.llm.base import LLMError, input_key
from plm_insight.models import CompliancePacket, DuplicateReview, ReturnAnalysis

KINDS = {"duplicates": DuplicateReview, "compliance": CompliancePacket,
         "returns": ReturnAnalysis}


class ReplayProvider:
    name = "replay"

    def __init__(self, replay_dir: Path):
        self.replay_dir = replay_dir

    def _load(self, kind: str, input_text: str):
        path = replay_path(self.replay_dir, kind, input_text)
        if not path.exists():
            raise LLMError("Offline demo mode has recorded AI reviews for the demo data only "
                           "(the first items in each queue, and the demo ECO history). Switch "
                           "to live AI mode for anything else.")
        return KINDS[kind].model_validate(json.loads(path.read_text())["output"])

    def review_duplicates(self, input_text: str) -> DuplicateReview:
        return self._load("duplicates", input_text)

    def compliance_packet(self, input_text: str) -> CompliancePacket:
        return self._load("compliance", input_text)

    def return_themes(self, input_text: str) -> ReturnAnalysis:
        return self._load("returns", input_text)


def replay_path(replay_dir: Path, kind: str, input_text: str) -> Path:
    return replay_dir / kind / f"{input_key(input_text)}.json"


def record(replay_dir: Path, kind: str, input_text: str, output: BaseModel) -> Path:
    path = replay_path(replay_dir, kind, input_text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"input": input_text, "output": output.model_dump()},
                               indent=2) + "\n")
    return path
