"""Maury the Skeptic (plan 8.1): argues that a candidate event is an artifact.

`MauryCore` holds everything that does not depend on the framework: the prompts and the
deterministic `known_issue_hits`. `FallbackMaury` here runs on the in-house base, and
`maury_nooa.NooaMaury` runs the same prompts on NOOA. Both take the packet as the same
rendered text, so the two paths see identical input.
"""
from __future__ import annotations

from dataclasses import dataclass

from computers.crew.base import FallbackAgent
from computers.crew.llm import Gateway
from computers.crew.packet import render
from computers.crew.schemas import Argument, ContextPacket

__all__ = ["FallbackMaury", "IssueHit", "MauryCore", "format_hits"]

SYSTEM = (
    "You are the Skeptic of the Harvard plate patrol (DASCH plates, 1880 to 1990). For a candidate event in one "
    "star's lightcurve, find the most likely explanation that is NOT astrophysical. Argue only from evidence in the "
    "packet. Every claim must cite one or more evidence ids such as E-004, taken from the packet. The event is a "
    "candidate and never a discovery. If the evidence does not support an artifact explanation, say what is missing "
    "and do not invent evidence."
)
TASK = (
    "Make the strongest evidence-based case that this event is an artifact: a plate defect, a blend, an emulsion or "
    "calibration effect, a missing point, or a seam between plate series. Return side 'artifact' and at most five "
    "claims, each with its evidence ids. Use only the packet text and the known-issue hits."
)


@dataclass(frozen=True)
class IssueHit:
    check: str
    detail: dict
    evidence_id: str


def format_hits(hits: list[IssueHit]) -> str:
    if not hits:
        return "No L1 check is flagged for this event."
    return "\n".join(f"- {h.check} [{h.evidence_id}] {h.detail}" for h in hits)


class MauryCore:
    """Framework-free parts of Maury."""

    SYSTEM = SYSTEM
    TASK = TASK

    def known_issue_hits(self, packet: ContextPacket) -> list[IssueHit]:
        """The L1 checks of block 9 that are flagged. Deterministic: no model is called."""
        return [
            IssueHit(e.payload["check"], {k: v for k, v in e.payload.items() if k not in ("check", "status")}, e.id)
            for e in packet.evidence
            if e.block == 9 and e.kind == "check" and e.payload.get("status") == "flag"
        ]

    def prompt_text(self, packet: ContextPacket) -> tuple[str, str]:
        """The two strings both bases pass to the model: the rendered packet and the known-issue hits."""
        return render(packet, "maury"), format_hits(self.known_issue_hits(packet))

    async def argue_artifact(self, packet_text: str, known_issues: str) -> Argument:  # pragma: no cover - overridden by each base
        raise NotImplementedError

    async def run(self, packet: ContextPacket) -> Argument:
        text, hits = self.prompt_text(packet)
        return await self.argue_artifact(text, hits)


class FallbackMaury(MauryCore, FallbackAgent):
    def __init__(self, gateway: Gateway) -> None:
        FallbackAgent.__init__(self, gateway)

    async def argue_artifact(self, packet_text: str, known_issues: str) -> Argument:
        user = f"{self.TASK}\n\n## Input parameters:\npacket_text = {packet_text!r}\nknown_issues = {known_issues!r}"
        return await self.ask(self.SYSTEM, user, Argument)
