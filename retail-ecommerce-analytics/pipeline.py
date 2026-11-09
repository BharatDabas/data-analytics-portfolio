"""End-to-end pipeline CLI.

    python pipeline.py                      # regenerate synthetic extracts + full rebuild
    python pipeline.py --source-dir PATH    # load your own CSV extracts (same contract) instead
    python pipeline.py --skip-generate      # rebuild from the existing data/raw extracts

The load is a full refresh and therefore idempotent: running it twice yields the same tables.
Run history, rejected records and data-quality results are appended to the ops_* tables.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import settings
from database import db
from ingestion.sources import CsvSource
from ingestion.synthetic import generate, write_raw
from transformations import build


def run_pipeline(db_path: Path | None = None, raw_dir: Path | None = None, generate_data: bool = True,
                 source_dir: Path | None = None) -> dict:
    db_path = Path(db_path or settings.DB_PATH)
    raw_dir = Path(raw_dir or settings.RAW_DIR)
    as_of = settings.as_of_date()
    if source_dir is None and generate_data:
        write_raw(generate(as_of, seed=settings.SEED), raw_dir)
    source = CsvSource(Path(source_dir) if source_dir else raw_dir,
                       name="csv_extract" if source_dir else "synthetic_demo_csv")
    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    con = db.connect(db_path)
    try:
        return build.run(con, source, settings.load_business_rules(), run_id, as_of)
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source-dir", type=Path, help="folder with CSV extracts following the source contract")
    p.add_argument("--skip-generate", action="store_true", help="do not regenerate synthetic extracts")
    p.add_argument("--db", type=Path, help="DuckDB file (default: data/processed/retail.duckdb)")
    a = p.parse_args(argv)
    summary = run_pipeline(a.db, generate_data=not a.skip_generate, source_dir=a.source_dir)
    print(f"[{summary['status']}] run {summary['run_id']}: {summary['message']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
