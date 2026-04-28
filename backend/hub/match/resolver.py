"""MatchResolver：unique / multi / none 三种结果统一处理。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MatchOutcome(StrEnum):
    UNIQUE = "unique"
    MULTI = "multi"
    NONE = "none"


@dataclass
class MatchResult:
    outcome: MatchOutcome
    selected: dict | None = None
    choices: list[dict] | None = None
    truncated: bool = False


class MatchResolver:
    def resolve(
        self, *, keyword: str, resource: str, candidates: list[dict], max_show: int = 5,
    ) -> MatchResult:
        if not candidates:
            return MatchResult(outcome=MatchOutcome.NONE)
        if len(candidates) == 1:
            return MatchResult(outcome=MatchOutcome.UNIQUE, selected=candidates[0])
        truncated = len(candidates) > max_show
        return MatchResult(
            outcome=MatchOutcome.MULTI,
            choices=candidates[:max_show],
            truncated=truncated,
        )

    def resolve_choice(self, candidates: list[dict], choice_number: int) -> dict | None:
        if 1 <= choice_number <= len(candidates):
            return candidates[choice_number - 1]
        return None
