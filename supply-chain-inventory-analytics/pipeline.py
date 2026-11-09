"""Command-line entry point: regenerate the synthetic extracts and rebuild the DuckDB model.

    python pipeline.py                 # regenerate synthetic data + rebuild
    python pipeline.py --no-regenerate # rebuild from existing CSVs in data/raw (e.g. your own extracts)
    python pipeline.py --as-of 2026-06-30 --seed 7
"""
from __future__ import annotations

import argparse
import time
from datetime import date

from config.settings import DB_PATH, RAW_DIR, SEED, default_as_of
from transformations.build import build


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", type=date.fromisoformat, default=None, help="dataset as-of date (default: yesterday UTC)")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--raw-dir", default=str(RAW_DIR))
    ap.add_argument("--no-regenerate", action="store_true", help="load existing CSV extracts instead of generating")
    a = ap.parse_args()
    t0 = time.perf_counter()
    out = build(a.db, a.raw_dir, a.as_of or default_as_of(), a.seed, regenerate=not a.no_regenerate)
    print(f"[{out['status']}] run {out['run_id']}: {out['rows_loaded']:,} fact/dim rows, {out['checks']} DQ checks, "
          f"{out['rejected']} rows quarantined ({time.perf_counter() - t0:.1f}s)\n{out['message']}")
    print(f"Database: {a.db}")


if __name__ == "__main__":
    main()
