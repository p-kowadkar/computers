"""Schemas the crew passes around (plan 8.4 and Appendix B).

Every fact an agent may cite is an `Evidence` with an id. A `Claim` must cite at least one
id, and the validator removes any claim whose ids are not in the packet. `Verdict` is
declared here and first used in Sprint 9.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "Argument",
    "Claim",
    "ContextPacket",
    "EventDescription",
    "Evidence",
    "StarIdentity",
    "Verdict",
]

PACKET_VERSION = "v0"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StarIdentity(_Frozen):
    ref_number: int
    tile: str
    ra_deg: float
    dec_deg: float
    apass_b: float | None = None
    color: float | None = None


class EventDescription(_Frozen):
    """What L1 saw. The event is a candidate: nothing here says it is real."""

    kind: str  # outburst, dip, step, long_term or disappearance
    epoch_start: float  # decimal years
    epoch_end: float
    amplitude_mag: float
    lower_limit: bool  # an outburst's amplitude is a lower limit, because plates saturate
    single_plate: bool


class Evidence(_Frozen):
    id: str  # "E-017"
    block: int  # packet block 1..9
    kind: str  # "identity" | "event" | "score" | "decade_summary" | "series_zero_point" | "measurement" | "check" | ...
    payload: dict[str, Any]

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not (v.startswith("E-") and v[2:].isdigit()):
            raise ValueError(f"evidence id must look like E-017, got {v!r}")
        return v

    @field_validator("block")
    @classmethod
    def _block_range(cls, v: int) -> int:
        if not 1 <= v <= 9:
            raise ValueError("block must be 1 to 9")
        return v


class ContextPacket(_Frozen):
    candidate_id: str
    star: StarIdentity
    event: EventDescription
    evidence: list[Evidence]
    mode: Literal["full", "lightcurve_only"]
    packet_version: str = PACKET_VERSION
    cuts: list[str] = Field(default_factory=list)  # what the token budget removed, so nothing is cut silently
    token_estimate: int = 0

    @field_validator("evidence")
    @classmethod
    def _unique_ids(cls, v: list[Evidence]) -> list[Evidence]:
        ids = [e.id for e in v]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence ids must be unique within a packet")
        return v

    def ids(self) -> set[str]:
        return {e.id for e in self.evidence}


class Claim(_Frozen):
    """One statement and the evidence ids it rests on.

    The non-empty rule is a validator and not a `minItems` keyword, because strict structured
    output on some providers rejects schema keywords beyond the basics.
    """

    text: str
    evidence_ids: list[str]  # must be non-empty; the claim validator checks they exist in the packet

    @field_validator("evidence_ids")
    @classmethod
    def _non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("a claim must cite at least one evidence id")
        return v


class Argument(_Frozen):
    side: Literal["artifact", "real"]
    claims: list[Claim]


class Verdict(_Frozen):
    verdict: Literal["real_event", "artifact", "inconclusive"]
    cause: Literal[
        "dust", "scratch", "annotation", "blend", "emulsion_colour", "astrometry_mismatch", "missing_point",
        "calibration_seam", "real_outburst", "real_dip", "real_longterm", "unknown",
    ]  # fmt: skip
    confidence: float  # 0 to 1, checked by a validator for the same reason as `Claim.evidence_ids`
    evidence_ids: list[str]
    rationale: str  # 80 words at most
    model_id: str
    prompt_hash: str

    @field_validator("confidence")
    @classmethod
    def _unit(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return v

    @field_validator("rationale")
    @classmethod
    def _short(cls, v: str) -> str:
        if len(v.split()) > 80:
            raise ValueError("rationale is limited to 80 words")
        return v
