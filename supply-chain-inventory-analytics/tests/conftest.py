from datetime import date

import pytest

from database import db
from transformations.build import build

AS_OF = date(2026, 6, 30)


@pytest.fixture(scope="session")
def built_db(tmp_path_factory):
    """Full pipeline build on the deterministic synthetic dataset (shared by integration and app tests)."""
    root = tmp_path_factory.mktemp("supply")
    path = root / "supply_test.duckdb"
    summary = build(path, root / "raw", AS_OF, seed=42)
    return path, root / "raw", summary


@pytest.fixture()
def con(built_db):
    c = db.connect(built_db[0])
    yield c
    c.close()
