"""Publisher protocol."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class PublishResult:
    ok: bool
    remote_id: str = ""
    container_ids: list[str] | None = None
    staged_keys: list[str] | None = None
    error: str = ""
    ambiguous: bool = False
    retry_after: float | None = None
    auth_error: bool = False
    permalink: str = ""


class Publisher(Protocol):
    name: str

    def publish(self, path: Path, caption: str) -> PublishResult: ...

    def test_connection(self) -> PublishResult: ...
