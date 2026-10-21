"""Staging adapter protocol for temporary public image URLs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class StagedObject:
    key: str
    url: str


class StagingAdapter(Protocol):
    def upload(self, path: Path, key: str) -> StagedObject: ...

    def delete(self, key: str) -> None: ...
