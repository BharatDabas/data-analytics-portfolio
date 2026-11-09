"""Source adapters. Every adapter returns raw, untyped (text) frames per entity so that
validation and type casting happen in one place (transformations.cleanse)."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pandas as pd


class SourceAdapter(Protocol):
    name: str

    def read(self, entity: str) -> pd.DataFrame: ...


class CsvSource:
    """Reads <folder>/<entity>.csv, e.g. exports from an e-commerce platform, ERP or ad platform."""

    def __init__(self, folder: Path, name: str = "csv_extract"):
        self.folder = Path(folder)
        self.name = name

    def read(self, entity: str) -> pd.DataFrame:
        path = self.folder / f"{entity}.csv"
        if not path.exists():
            raise FileNotFoundError(f"source extract not found: {path}")
        return pd.read_csv(path, dtype=str, keep_default_na=False)


class FrameSource:
    """In-memory adapter (tests, notebooks, or an API client that already returned frames)."""

    def __init__(self, frames: dict[str, pd.DataFrame], name: str = "in_memory"):
        self.frames = frames
        self.name = name

    def read(self, entity: str) -> pd.DataFrame:
        return self.frames[entity].astype("string").fillna("").astype(object)
