"""Reusable data-quality checks.

Every check returns a CheckResult; the pipeline persists the results to the
ops_dq_results table so exceptions are visible on the dashboard.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import pandas as pd

PASS, WARN, FAIL = "pass", "warn", "fail"


@dataclass
class CheckResult:
    check: str
    table: str
    status: str
    failed_rows: int
    detail: str


def check_schema(df: pd.DataFrame, required: list[str], table: str) -> CheckResult:
    missing = [c for c in required if c not in df.columns]
    return CheckResult("schema", table, FAIL if missing else PASS, len(missing),
                       f"missing columns: {missing}" if missing else f"{len(required)} required columns present")


def check_not_null(df: pd.DataFrame, cols: list[str], table: str, severity: str = FAIL) -> CheckResult:
    present = [c for c in cols if c in df.columns]
    bad = int(df[present].isna().any(axis=1).sum()) if present else 0
    return CheckResult("not_null:" + ",".join(cols), table, severity if bad else PASS, bad,
                       f"{bad} rows with nulls in key columns" if bad else "no nulls in key columns")


def check_unique(df: pd.DataFrame, keys: list[str], table: str, severity: str = FAIL) -> CheckResult:
    dups = int(df.duplicated(subset=keys, keep="first").sum())
    return CheckResult("unique:" + ",".join(keys), table, severity if dups else PASS, dups,
                       f"{dups} duplicate rows on {keys}" if dups else f"grain {keys} is unique")


def check_referential(df: pd.DataFrame, col: str, ref: pd.Series, table: str,
                      ref_name: str, severity: str = FAIL) -> CheckResult:
    orphans = int((~df[col].isin(set(ref)) & df[col].notna()).sum())
    return CheckResult(f"fk:{col}->{ref_name}", table, severity if orphans else PASS, orphans,
                       f"{orphans} rows reference unknown {ref_name}" if orphans else "all keys resolve")


def check_range(df: pd.DataFrame, col: str, table: str, min_value=None, max_value=None,
                severity: str = FAIL) -> CheckResult:
    s = df[col]
    mask = pd.Series(False, index=df.index)
    if min_value is not None:
        mask |= s < min_value
    if max_value is not None:
        mask |= s > max_value
    bad = int(mask.sum())
    rng = f"[{min_value}, {max_value}]"
    return CheckResult(f"range:{col}", table, severity if bad else PASS, bad,
                       f"{bad} values outside {rng}" if bad else f"all values within {rng}")


def check_freshness(latest: datetime | pd.Timestamp | None, max_age_hours: float, table: str,
                    now: datetime | None = None, severity: str = WARN) -> CheckResult:
    now = now or datetime.now(timezone.utc)
    if latest is None or pd.isna(latest):
        return CheckResult("freshness", table, FAIL, 0, "no data loaded")
    latest = pd.Timestamp(latest)
    if latest.tzinfo is None:
        latest = latest.tz_localize("UTC")
    age_h = (pd.Timestamp(now) - latest).total_seconds() / 3600
    ok = age_h <= max_age_hours
    return CheckResult("freshness", table, PASS if ok else severity, 0 if ok else 1,
                       f"latest record {latest:%Y-%m-%d %H:%M} UTC is {age_h:,.1f}h old (limit {max_age_hours}h)")


def check_reconciliation(left_total: float, right_total: float, name: str, table: str,
                         tolerance: float = 0.005) -> CheckResult:
    denom = max(abs(left_total), abs(right_total), 1e-9)
    diff = abs(left_total - right_total) / denom
    ok = diff <= tolerance
    return CheckResult(f"reconcile:{name}", table, PASS if ok else FAIL, 0 if ok else 1,
                       f"{left_total:,.2f} vs {right_total:,.2f} (diff {diff:.3%}, tolerance {tolerance:.2%})")


def results_frame(results: list[CheckResult], run_id: str) -> pd.DataFrame:
    df = pd.DataFrame([asdict(r) for r in results])
    if df.empty:
        df = pd.DataFrame(columns=["check", "table", "status", "failed_rows", "detail"])
    df.insert(0, "run_id", run_id)
    df["checked_at"] = pd.Timestamp.now(tz="UTC")
    return df.rename(columns={"check": "check_name", "table": "table_name"})
