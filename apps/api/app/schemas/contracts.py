from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PdfLocator(StrictModel):
    kind: Literal["pdf"]
    page: int = Field(ge=1)
    bbox: list[float] | None = None


class OfficeLocator(StrictModel):
    kind: Literal["docx", "converted_doc", "legacy_doc_text"]
    paragraph_index: int | None = Field(default=None, ge=0)
    table_index: int | None = Field(default=None, ge=0)
    row: int | None = Field(default=None, ge=0)
    column: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def position(self):
        if self.paragraph_index is None and self.table_index is None:
            raise ValueError("Office locator needs paragraph or table position")
        return self


class XlsxLocator(StrictModel):
    kind: Literal["xlsx"]
    sheet: str
    cell: str
    merged_range: str | None = None
    image_key: str | None = None


class TxtLocator(StrictModel):
    kind: Literal["txt"]
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


Locator = Annotated[PdfLocator | OfficeLocator | XlsxLocator | TxtLocator, Field(discriminator="kind")]


class SourceRef(StrictModel):
    block_id: str
    quote: str = Field(min_length=1, max_length=2000)


class Scope(StrictModel):
    platforms: list[str] = Field(min_length=1)
    phases: list[str] = Field(min_length=1)
    asset_kinds: list[Literal["script", "video", "cover", "caption", "image_post"]] = Field(min_length=1)


class Predicate(StrictModel):
    kind: Literal[
        "text_exact",
        "text_semantic",
        "presence",
        "absence",
        "time_window",
        "metadata",
        "requires_evidence",
        "human_judgment",
    ]
    instruction: str = Field(min_length=1)
    terms: list[str] = []
    window_ms: list[int] | None = None
    manual_required: bool = False


class Rule(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    type: Literal["fact", "must", "should", "example", "exception", "out_of_scope"]
    severity: Literal["blocker", "major", "minor", "advisory"]
    scope: Scope
    predicate: Predicate
    source_refs: list[SourceRef] = Field(min_length=1)
    exceptions: list[str] = []
    relations: list[str] = []
    confirmation_status: Literal["pending", "confirmed", "excluded"] = "pending"
    confirmation_note: str = ""


class Conflict(StrictModel):
    id: str
    rule_ids: list[str]
    description: str
    blocking: bool = True
    status: Literal["pending", "resolved"] = "pending"
    resolution: str = ""


class ProjectInput(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    brand: str = Field(min_length=1, max_length=200)
    product: str = Field(min_length=1, max_length=200)
    platform: Literal["xiaohongshu", "douyin", "bilibili", "other", "unconfirmed"]
    phase: str = Field(min_length=1, max_length=80)
    mode: Literal["trial", "formal"] = "trial"


class SubmissionInput(StrictModel):
    creator: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None
    script_id: str | None = None
    video_id: str | None = None
    cover_id: str | None = None
    caption_id: str | None = None
    pairing_confirmed: bool = False


class RulesPatch(StrictModel):
    expected_revision: int = Field(ge=1)
    rules: list[Rule]
    conflicts: list[Conflict] = []


class RulesConfirm(StrictModel):
    expected_revision: int
    note: str = Field(min_length=1)
    kind: Literal["experimental", "brand_confirmed"] = "experimental"
    accept_partial_sources: bool = False


class ExtractInput(StrictModel):
    source_ids: list[str] = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=100)


class ReviewInput(StrictModel):
    submission_id: str
    ruleset_id: str
    idempotency_key: str = Field(min_length=1, max_length=100)
    script_approval_id: str | None = None
    content_snapshot_id: str | None = None
    historical_trial: bool = False
    allow_unverified_audio: bool = False
    trial_note: str = ""


class DecisionInput(StrictModel):
    expected_input_hash: str
    expected_revision: int
    decision: Literal["APPROVE", "REQUEST_CHANGES", "REJECT"]
    reason: str = Field(min_length=1, max_length=5000)
    mode: Literal["ai_assisted", "independent_human"] = "ai_assisted"


class ObservationInput(StrictModel):
    channel: Literal[
        "spoken_audio", "onscreen_text", "visual_event", "metadata", "human_observation", "unclassified_audio"
    ]
    content: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    uncertainty_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def time_order(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("Invalid time range")
        return self


class CheckInput(StrictModel):
    rule_id: str
    status: Literal["PASS", "FAIL", "UNCERTAIN", "NOT_APPLICABLE"]
    evidence_ids: list[str]
    explanation: str = Field(min_length=1)
    suggestion: str = ""
    applicability_reason: str = ""
    limitations: list[str] = []
