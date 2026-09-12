"""Minimal model/configuration version registry primitives.

Persistence is intentionally left to the application layer. The registry key
must be immutable and tied to the exact configuration used for an evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping


@dataclass(frozen=True)
class ModelVersion:
    name: str
    version: str
    config_hash: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def identifier(self) -> str:
        return f"{self.name}:{self.version}:{self.config_hash}"
