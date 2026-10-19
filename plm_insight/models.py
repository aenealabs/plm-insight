"""Structured output the model returns. Every claim cites part numbers and attribute values,
which code checks against the export before anyone sees them."""

from typing import Literal

from pydantic import BaseModel, Field

FieldName = Literal["description", "part_type", "business_unit", "material", "size", "uom",
                    "weight_kg", "make_buy", "lifecycle", "export_class", "superseded_by",
                    "created", "created_by", "used_in"]


class CitedValue(BaseModel):
    part_number: str = Field(description="A part number exactly as given.")
    field: FieldName = Field(description="The attribute cited.")
    value: str = Field(description="The value exactly as given for that part and field "
                       "('' if empty).")


class Point(BaseModel):
    text: str = Field(description="One sentence.")
    cites: list[CitedValue] = Field(description="The values that support the sentence.")


class DuplicateReview(BaseModel):
    verdict: Literal["same part", "different parts", "needs engineering check"]
    keep: str = Field(description="Part number to keep if they are the same part, else ''.")
    reasons: list[Point] = Field(description="2-4 points: why they are or are not the same "
                                 "part, and why that part should be kept.")
    before_merging: list[str] = Field(description="Checks a person should make before "
                                      "merging (e.g. confirm a supplier or a drawing). Can be "
                                      "empty.")


class CompliancePacket(BaseModel):
    summary: str = Field(description="Two sentences: what the part is and why it needs a "
                         "classification decision. Never suggest a classification.")
    evidence: list[Point] = Field(description="2-5 points Trade Compliance would want: what "
                                  "the part is, where it is used, what similar parts are.")
    questions: list[str] = Field(description="Questions for engineering that would help Trade "
                                 "Compliance decide (design origin, specially designed, etc.).")


class Quote(BaseModel):
    eco: str = Field(description="The ECO id exactly as given.")
    quote: str = Field(description="Words copied exactly from that ECO's return comment.")


class ReturnTheme(BaseModel):
    name: str = Field(description="Short name, e.g. 'Affected parts missing'.")
    description: str = Field(description="One sentence: what reviewers keep sending back.")
    eco_ids: list[str] = Field(description="The ECO id of every return line in this theme, "
                               "exactly as given.")
    quotes: list[Quote] = Field(description="Two or three representative quotes.")
    prevention: str = Field(description="One control in the PLM change process that would "
                            "stop this before review, e.g. a required field or a check at "
                            "submit.")


class ReturnAnalysis(BaseModel):
    headline: str = Field(description="One sentence: the main reason ECOs come back. No "
                          "numbers; code counts.")
    themes: list[ReturnTheme] = Field(description="4-7 themes, largest first. Every return "
                                      "line belongs to exactly one theme.")
