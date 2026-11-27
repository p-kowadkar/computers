"""The deterministic claim validator (plan 8.3 step 4, spec S8 R4).

A claim stays only if it cites at least one evidence id and every id it cites is in the
packet. The text of a kept claim is never changed. An argument with nothing left is an empty
argument, not an error: it is information about the path that produced it.
"""
from __future__ import annotations

from dataclasses import dataclass

from computers.crew.schemas import Argument, Claim, ContextPacket

__all__ = ["Removed", "ValidationResult", "validate"]


@dataclass(frozen=True)
class Removed:
    claim: Claim
    reason: str  # "no_evidence_ids" or "unknown_evidence_id: E-999"


@dataclass(frozen=True)
class ValidationResult:
    argument: Argument
    removed: tuple[Removed, ...]

    @property
    def empty(self) -> bool:
        return not self.argument.claims

    @property
    def clean(self) -> bool:
        """True when nothing had to be removed and something is left."""
        return not self.removed and not self.empty


def validate(packet: ContextPacket, argument: Argument) -> ValidationResult:
    known = packet.ids()
    kept: list[Claim] = []
    removed: list[Removed] = []
    for c in argument.claims:
        if not c.evidence_ids:
            removed.append(Removed(c, "no_evidence_ids"))
            continue
        bad = [i for i in c.evidence_ids if i not in known]
        if bad:
            removed.append(Removed(c, f"unknown_evidence_id: {', '.join(bad)}"))
        else:
            kept.append(c)
    return ValidationResult(Argument(side=argument.side, claims=kept), tuple(removed))
