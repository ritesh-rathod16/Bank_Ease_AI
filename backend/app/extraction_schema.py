"""Additive, versioned contract for extracted document fields.

Legacy term keys remain part of the API. This model describes the standard
metadata layered onto those terms without claiming calibrated confidence.
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


FieldDataType = Literal["text", "date", "money", "percentage", "integer", "number", "unknown"]
EvidenceStatus = Literal[
    "matched", "not_found", "source_mismatch", "invalid_page", "invalid_bbox", "not_applicable",
]
VerificationStatus = Literal[
    "extracted", "verified", "needs_review", "conflicting", "failed", "not_found", "not_applicable",
]
ValidationStatus = Literal[
    "not_run", "passed", "needs_review", "failed", "conflicting", "not_found", "not_applicable",
]
ReviewStatus = Literal["not_reviewed", "pending", "corrected"]


class ExtractedField(BaseModel):
    """Structured field metadata serialized alongside the existing term shape."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=16, max_length=64)
    field_id: str = Field(min_length=16, max_length=64)
    document_id: str = Field(min_length=1, max_length=128)
    field_name: str = Field(min_length=1, max_length=100)
    raw_value: Any = None
    normalized_value: Any = None
    data_type: FieldDataType = "unknown"
    currency: str | None = Field(default=None, max_length=12)
    page_number: int | None = Field(default=None, ge=1)
    source_text: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    extraction_method: str | None = Field(default=None, max_length=80)
    ocr_confidence: float | None = Field(default=None, ge=0, le=100)
    field_extraction_confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_calibration: Literal["unavailable"] = "unavailable"
    evidence_ref: str | None = Field(default=None, min_length=16, max_length=64)
    evidence_status: EvidenceStatus
    source_text_status: Literal["matched", "not_found", "mismatch"]
    page_status: Literal["valid", "missing", "invalid"]
    bbox_status: Literal["valid", "unavailable", "invalid"]
    verification_status: VerificationStatus
    validation_status: ValidationStatus
    review_status: ReviewStatus
    processing_run_id: str = Field(min_length=16, max_length=64)
    schema_version: int = Field(default=1, ge=1)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value):
        if value is None:
            return value
        x0, y0, x1, y1 = value
        if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
            raise ValueError("Bounding box must be finite, non-negative, and have positive area.")
        return value
