"""Versioned video facts. Interpretation and human confirmation stay separate."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION = "video-content-v1"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContentEntity(Strict):
    id: str
    kind: Literal["person", "object", "brand", "other"]
    description: str
    identity_level: Literal["unknown", "category", "brand", "product_line", "sku"] = "unknown"
    basis: Literal["visual", "spoken_claim", "onscreen_claim", "unknown"] = "unknown"
    evidence_ids: list[str] = []
    status: Literal["CANDIDATE", "UNKNOWN"] = "UNKNOWN"


class ContentEvent(Strict):
    id: str
    channel: Literal["spoken_audio", "onscreen_text", "visual_event", "sound_event"]
    raw_content: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    uncertainty_ms: int | None = Field(default=None, ge=0)
    actor_id: str | None = None
    object_id: str | None = None
    speaker_id: str | None = None
    action: str | None = None
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    status: Literal["CANDIDATE", "UNCLEAR", "NOT_OBSERVED"] = "CANDIDATE"

    @model_validator(mode="after")
    def coordinates(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("Invalid event interval")
        if self.bbox and not (
            0 <= self.bbox[0] < self.bbox[2] <= 1 and 0 <= self.bbox[1] < self.bbox[3] <= 1
        ):
            raise ValueError("Invalid normalized display bbox")
        return self


class ContentClaim(Strict):
    id: str
    kind: Literal["explicit_statement", "editing_implication", "interpretation", "relation"]
    description: str
    evidence_ids: list[str] = Field(min_length=1)
    relation: Literal[
        "supports", "contradicts", "retracts", "before", "same_entity_candidate", "refers_to", "unknown"
    ] = "unknown"
    status: Literal["CANDIDATE", "DISPUTED", "UNKNOWN"] = "CANDIDATE"
    alternatives: list[str] = []


class ContentLimit(Strict):
    reason: str
    channel: Literal["audio", "visual", "text", "identity", "relation", "all"] = "all"
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)


class SemanticBoundary(Strict):
    at_ms: int = Field(gt=0)
    reason: str


class ContentBatch(Strict):
    events: list[ContentEvent] = Field(max_length=500)
    entities: list[ContentEntity] = Field(default=[], max_length=80)
    claims: list[ContentClaim] = Field(default=[], max_length=200)
    limitations: list[ContentLimit] = []
    boundaries: list[SemanticBoundary] = Field(default=[], max_length=100)
    audio_observed: bool
    inspected_channels: list[str]


class ContentCreate(Strict):
    asset_id: str
    idempotency_key: str = Field(min_length=1, max_length=100)
    acknowledge_trial: bool


class EventCorrection(Strict):
    event_id: str
    raw_content: str | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    speaker_id: str | None = None
    actor_id: str | None = None
    object_id: str | None = None
    status: Literal["CANDIDATE", "UNCLEAR", "NOT_OBSERVED"] | None = None


class ContentRevision(Strict):
    expected_content_hash: str
    corrections: list[EventCorrection] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=5000)


class ContentConfirmation(Strict):
    expected_checks_hash: str | None = None
    expected_content_hash: str
    reason: str = Field(min_length=1)
    acknowledge_remaining_gaps: bool
