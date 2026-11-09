"""Helpers shared by the dashboard pages."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboards import theme
from dashboards.data import Context


def header(ctx: Context, title: str, subtitle: str, show_window: bool = True) -> None:
    chips = []
    if show_window:
        chips.append((f"{ctx.start:%d %b %Y} – {ctx.end:%d %b %Y}", "accent"))
    chips += [(f"Data as of {ctx.as_of:%d %b %Y}", ""), (ctx.data_label, "warn")]
    theme.page_header(title, subtitle, chips)


def gbp(x: float) -> str:
    return theme.fmt_money(x, "£")


def dataframe(df: pd.DataFrame, **kwargs) -> None:
    st.dataframe(df, hide_index=True, width="stretch", **kwargs)


def money_col(label: str, help: str | None = None):
    """Money column in GBP (values should be rounded by the caller)."""
    return st.column_config.NumberColumn(f"{label} (£)", format="localized", help=help)


def pct_col(label: str, help: str | None = None):
    return st.column_config.NumberColumn(label, format="percent", help=help)


def complete_months(df: pd.DataFrame, month_col: str, as_of) -> pd.DataFrame:
    """Drop the in-progress month so a partial month never reads as a decline."""
    last = pd.Timestamp(as_of)
    cutoff = last.to_period("M").start_time if not last.is_month_end else last + pd.Timedelta(days=1)
    return df[pd.to_datetime(df[month_col]) < cutoff]


def series_color(entities: list[str]) -> dict[str, str]:
    """Stable entity -> colour mapping (sorted names, fixed slot order); never by rank."""
    names = sorted(entities)
    if len(names) > len(theme.SERIES):
        raise ValueError("more than 8 categorical series: fold into 'Other' instead of generating colours")
    return {e: theme.SERIES[i] for i, e in enumerate(names)}
