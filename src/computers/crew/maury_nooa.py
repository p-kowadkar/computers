"""Maury on NOOA. The prompts are the same strings as `MauryCore`; a test checks that they match."""
from __future__ import annotations

from computers.crew.base import PREDICT, Agent, PredictStrategy, strategy
from computers.crew.maury import MauryCore
from computers.crew.schemas import Argument

__all__ = ["NooaMaury"]


class NooaMaury(MauryCore, Agent):
    """You are the Skeptic of the Harvard plate patrol (DASCH plates, 1880 to 1990). For a candidate event in one star's lightcurve, find the most likely explanation that is NOT astrophysical. Argue only from evidence in the packet. Every claim must cite one or more evidence ids such as E-004, taken from the packet. The event is a candidate and never a discovery. If the evidence does not support an artifact explanation, say what is missing and do not invent evidence."""

    @strategy(PredictStrategy(config=PREDICT))
    async def argue_artifact(self, packet_text: str, known_issues: str) -> Argument:
        """Make the strongest evidence-based case that this event is an artifact: a plate defect, a blend, an emulsion or calibration effect, a missing point, or a seam between plate series. Return side 'artifact' and at most five claims, each with its evidence ids. Use only the packet text and the known-issue hits."""
        ...  # noqa: PIE790  NOOA completes a method whose body is `...`; a docstring alone is not enough
