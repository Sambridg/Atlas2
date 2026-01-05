"""Validator interface and basic implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .types import ValidatorStatus


@dataclass
class ValidatorResult:
    validator_id: str
    code: str
    status: ValidatorStatus
    message: str
    details: dict | None = None


def run_validators(user_text: str) -> List[ValidatorResult]:
    """Run lightweight validators on user text.

    Currently a placeholder that always returns OK; hook in coherency checks or
    policy validators here.
    """
    return [
        ValidatorResult(
            validator_id="basic.coherency",
            code="validator.ok",
            status=ValidatorStatus.OK,
            message="coherent",
        )
    ]


def should_retry_from_validation(results: List[ValidatorResult]) -> bool:
    """Return True if any validator suggests retry."""
    return any(r.status == ValidatorStatus.RETRY for r in results)


def is_blocked(results: List[ValidatorResult]) -> bool:
    """Return True if any validator signals error/escalate."""
    return any(r.status in {ValidatorStatus.ERROR, ValidatorStatus.ESCALATE} for r in results)
