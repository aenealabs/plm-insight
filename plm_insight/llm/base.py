"""Provider interface shared by the live and replay LLM backends."""

import hashlib
from typing import Protocol

from plm_insight.models import CompliancePacket, DuplicateReview, ReturnAnalysis


class LLMError(Exception):
    """A provider could not produce a usable result; the message is user-facing."""


class LLMProvider(Protocol):
    name: str

    def review_duplicates(self, input_text: str) -> DuplicateReview: ...

    def compliance_packet(self, input_text: str) -> CompliancePacket: ...

    def return_themes(self, input_text: str) -> ReturnAnalysis: ...


def input_key(text: str) -> str:
    """Stable key for an input, insensitive to whitespace and case differences."""
    normalized = " ".join(text.split()).lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]
