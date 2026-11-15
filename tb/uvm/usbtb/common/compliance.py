"""The compliance log: the USBCV-style record of every check and every failure.

USBCV "transcribes all results to a text based log file"; here each check is logged and
each failure becomes a :class:`Violation` that fails the test in its report phase.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Violation:
    source: str
    message: str

    def __str__(self) -> str:
        return f"[{self.source}] {self.message}"


def _compliance_logger() -> logging.Logger:
    logger = logging.getLogger("compliance")
    logger.setLevel(logging.INFO)
    return logger


@dataclass
class ComplianceLog:
    """Shared by the test sequences, the scoreboard and the checkers (through the ConfigDB)."""

    logger: logging.Logger = field(default_factory=_compliance_logger)
    violations: list[Violation] = field(default_factory=list)
    checks: int = 0
    expecting_violations: bool = False
    """Set by self-check tests: violations are still recorded, but logged at INFO as
    ``[expected]`` so that ERROR always means a real problem."""

    def check(self, condition: bool, source: str, message: str) -> bool:
        """Record one check; log and keep a violation when ``condition`` is false."""
        self.checks += 1
        if not condition:
            self.fail(source, message)
        return condition

    def fail(self, source: str, message: str) -> None:
        violation = Violation(source, message)
        self.violations.append(violation)
        if self.expecting_violations:
            self.logger.info("[expected] %s", violation)
        else:
            self.logger.error("%s", violation)

    def info(self, source: str, message: str) -> None:
        self.logger.info("[%s] %s", source, message)

    def matching(self, text: str) -> list[Violation]:
        return [v for v in self.violations if text in v.source or text in v.message]

    @property
    def passed(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        return f"{self.checks} checks, {len(self.violations)} violation(s)"
