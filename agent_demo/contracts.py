"""Small JSON contracts shared by the controller and real execution adapters.

Adapters do not receive an injector's ground truth. All observations must come
from an actual probe, registered process, or workload progress record.
"""
from __future__ import annotations

from typing import Protocol


class Adapter(Protocol):
    backend: str

    def observe(self) -> dict:
        """Return status, evidence[{id,kind,target,value}], and observable facts."""
        ...

    def diagnose(self, observation: dict) -> dict:
        """Return code, evidence_ids, affected, and explanation; no fault labels."""
        ...

    def candidates(self, diagnosis: dict) -> list[dict]:
        """Return id, version, action, target, cost and changes for each plan."""
        ...

    def validate(self, candidate: dict) -> list[str]:
        """Return rejection reasons; [] means feasible in the current snapshot."""
        ...

    def apply(self, candidate: dict, *, idempotency_key: str) -> dict:
        """Revalidate version and constraints; cache receipt for exact retries."""
        ...

    def verify(self, receipt: dict) -> dict:
        """Return recovered:bool, evidence:list, details:dict from new work."""
        ...

