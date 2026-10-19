"""Run the first demo findings through the live model and save responses for offline replay.

Usage: python -m plm_insight.record_demos [duplicates] [compliance] [returns]   (default: all)
Records AI reviews for a fixed set of findings in the seeded demo export (see demo_issues),
which are the ones the offline demo can show, and the ECO return themes for the demo ECO
history. Credentials come from the environment (API
key, `ant auth login`, or workload identity federation in CI).
"""

import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from plm_insight.config import Settings, load_settings
from plm_insight.db import make_engine
from plm_insight.eco import EcoStore, returns_input, returns_window
from plm_insight.llm import LLMError
from plm_insight.llm.anthropic_provider import AnthropicProvider
from plm_insight.llm.replay import record
from plm_insight.quality import QualityStore
from plm_insight.review import review_input

DEMO_COUNT = {"duplicates": 10, "compliance": 6, "returns": 1}
RULE = {"duplicates": "DQ-DUP", "compliance": "DQ-EXP"}


def demo_issues(store: QualityStore, kind: str) -> list:
    """The findings the offline demo can review. For compliance, half are parts in the
    controlled business unit and half are other units' parts used in controlled assemblies."""
    issues = store.issues(rule=RULE[kind])
    if kind == "duplicates":
        return issues[:DEMO_COUNT[kind]]
    half = DEMO_COUNT[kind] // 2
    in_unit = [i for i in issues if not i.evidence_data["controlled_assemblies"]]
    used_in = [i for i in issues if i.evidence_data["controlled_assemblies"]]
    return in_unit[:half] + used_in[:half]


def demo_inputs(settings: Settings, kind: str) -> list[tuple[str, str]]:
    """(issue key, input text) for the first findings of a kind in the seeded export."""
    if kind == "returns":
        with tempfile.TemporaryDirectory() as tmp:
            engine = make_engine(str(Path(tmp) / "demo.db"))
            store = EcoStore(engine)
            store.seed_if_empty(settings.dataset_dir, "seed")
            ecos, events = store.rows()
            text = returns_input(returns_window(events, ecos, store.dataset().as_of), ecos)
            engine.dispose()
        return [("ECO returns", text)]
    with tempfile.TemporaryDirectory() as tmp:
        engine = make_engine(str(Path(tmp) / "demo.db"))
        store = QualityStore(engine)
        store.seed_if_empty(settings.dataset_dir, "seed")
        issues = demo_issues(store, kind)
        out = [(i.key, review_input(store, i)) for i in issues]
        engine.dispose()
    return out


def main() -> int:
    settings = replace(load_settings(), llm_provider="anthropic")
    provider = AnthropicProvider(settings.model)
    kinds = [k for k in sys.argv[1:] if k in DEMO_COUNT] or list(DEMO_COUNT)
    failures = 0
    for kind in kinds:
        for key, text in demo_inputs(settings, kind):
            try:
                result = (provider.review_duplicates(text) if kind == "duplicates"
                          else provider.compliance_packet(text) if kind == "compliance"
                          else provider.return_themes(text))
            except LLMError as e:
                failures += 1
                print(f"FAIL {kind} {key}: {e}", file=sys.stderr)
                continue
            path = record(settings.replay_dir, kind, text, result)
            print(f"ok   {kind} {key} -> {path.name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
