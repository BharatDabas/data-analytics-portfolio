"""Shared visual system for the analytics portfolio dashboards.

The same module is used (copied) by all three projects so that they read as one
product family; each project passes its own accent colour and product name.
Colour roles follow a validated categorical palette (fixed slot order, never
cycled) plus a reserved status palette that is only used for good/warning/critical.
"""
from __future__ import annotations

import html
from dataclasses import dataclass

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

# Categorical slots - assigned in this fixed order, never by rank.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
# Reserved status colours - never used as a series colour.
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
STATUS_ICON = {"good": "●", "warning": "▲", "serious": "◆", "critical": "■"}
# Sequential (magnitude) ramp, single hue light -> dark.
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
# Diverging blue <-> red with neutral grey midpoint (for correlations / changes).
DIVERGING = [[0.0, "#d03b3b"], [0.5, "#f0efec"], [1.0, "#2a78d6"]]

INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#8a8983"
SURFACE = "#fcfcfb"
CANVAS = "#f4f4f1"
HAIRLINE = "#e4e3de"


@dataclass(frozen=True)
class Brand:
    name: str
    tagline: str
    accent: str  # product identity colour (chrome only, never data)
    accent_soft: str
    icon: str


def register_plotly_template() -> None:
    template = go.layout.Template()
    template.layout = go.Layout(
        font=dict(family="Inter, Segoe UI, Helvetica, Arial, sans-serif", size=12, color=INK_2),
        title=dict(font=dict(size=14, color=INK), x=0, xanchor="left", y=0.98),
        colorway=SERIES,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=24, t=56, b=8),
        hoverlabel=dict(bgcolor="white", bordercolor=HAIRLINE, font=dict(color=INK, size=12)),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0, title=None,
                    font=dict(size=11, color=INK_2)),
        xaxis=dict(showgrid=False, linecolor=HAIRLINE, ticks="outside", tickcolor=HAIRLINE,
                   zeroline=False, title=dict(font=dict(size=11))),
        yaxis=dict(showgrid=True, gridcolor=HAIRLINE, gridwidth=1, linecolor=HAIRLINE,
                   zeroline=False, title=dict(font=dict(size=11))),
        bargap=0.25,
    )
    pio.templates["portfolio"] = template
    pio.templates.default = "portfolio"


def inject_css(brand: Brand) -> None:
    st.markdown(
        f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stMarkdown, .stDataFrame, button, input, select, textarea {{
  font-family: 'Inter', 'Segoe UI', Helvetica, Arial, sans-serif;
}}
.stApp {{ background: {CANVAS}; }}
.block-container {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1480px; }}
header[data-testid="stHeader"] {{ background: transparent; }}
section[data-testid="stSidebar"] {{ background: #111418; }}
section[data-testid="stSidebar"] * {{ color: #d9dbe0; }}
section[data-testid="stSidebar"] .stRadio label p {{ font-size: 0.92rem; }}
section[data-testid="stSidebar"] [data-baseweb="select"] * {{ color: {INK}; }}
section[data-testid="stSidebar"] input {{ color: {INK} !important; -webkit-text-fill-color: {INK} !important; }}
section[data-testid="stSidebar"] [data-baseweb="input"] *, section[data-testid="stSidebar"] [data-baseweb="tag"] *,
section[data-testid="stSidebar"] [data-testid="stDateInput"] span {{ color: {INK}; }}
.brand {{ display:flex; gap:.65rem; align-items:center; margin:.2rem 0 1.2rem 0; }}
.brand-mark {{ width:34px; height:34px; border-radius:8px; background:{brand.accent};
  display:flex; align-items:center; justify-content:center; font-size:18px; color:white; }}
.brand-name {{ font-weight:700; font-size:1.0rem; color:white; line-height:1.15; }}
.brand-tag {{ font-size:.72rem; color:#9aa0aa; }}
.page-head {{ display:flex; justify-content:space-between; align-items:flex-end; gap:1rem;
  border-bottom:1px solid {HAIRLINE}; padding-bottom:.8rem; margin-bottom:1.1rem; flex-wrap:wrap; }}
.page-title {{ font-size:1.55rem; font-weight:700; color:{INK}; margin:0; letter-spacing:-.01em; }}
.page-sub {{ font-size:.88rem; color:{INK_2}; margin-top:.15rem; }}
.chip {{ display:inline-block; font-size:.72rem; font-weight:600; padding:.18rem .55rem; border-radius:999px;
  margin-left:.35rem; border:1px solid {HAIRLINE}; background:white; color:{INK_2}; white-space:nowrap; }}
.chip-accent {{ background:{brand.accent_soft}; color:{brand.accent}; border-color:transparent; }}
.chip-warn {{ background:#fff4dc; color:#8a5a00; border-color:transparent; }}
.kpi {{ background:{SURFACE}; border:1px solid {HAIRLINE}; border-radius:10px; padding:.85rem 1rem .8rem 1rem;
  height:100%; }}
.kpi-label {{ font-size:.74rem; text-transform:uppercase; letter-spacing:.05em; color:{INK_3}; font-weight:600; }}
.kpi-value {{ font-size:1.55rem; font-weight:700; color:{INK}; margin-top:.15rem; letter-spacing:-.01em;
  font-variant-numeric: tabular-nums; }}
.kpi-foot {{ font-size:.78rem; color:{INK_2}; margin-top:.1rem; }}
.delta-up {{ color:#006300; font-weight:600; }}
.delta-down {{ color:#b42318; font-weight:600; }}
.section {{ font-size:1.02rem; font-weight:650; color:{INK}; margin:1.3rem 0 .1rem 0; }}
.section-sub {{ font-size:.82rem; color:{INK_2}; margin-bottom:.4rem; }}
.note {{ background:{SURFACE}; border:1px solid {HAIRLINE}; border-left:3px solid {brand.accent};
  border-radius:8px; padding:.65rem .85rem; font-size:.82rem; color:{INK_2}; margin:.5rem 0 .8rem 0; }}
.empty {{ background:{SURFACE}; border:1px dashed {HAIRLINE}; border-radius:10px; padding:1.6rem;
  text-align:center; color:{INK_2}; font-size:.9rem; }}
.status {{ font-weight:600; font-size:.82rem; }}
div[data-testid="stPlotlyChart"], div[data-testid="stDataFrame"] {{ background:{SURFACE};
  border:1px solid {HAIRLINE}; border-radius:10px; padding:.35rem .5rem; }}
.stTabs [data-baseweb="tab-list"] {{ gap:.25rem; }}
.stTabs [data-baseweb="tab"] {{ padding:.4rem .9rem; }}
footer {{ visibility:hidden; }}
</style>
""",
        unsafe_allow_html=True,
    )


def sidebar_brand(brand: Brand) -> None:
    st.sidebar.markdown(
        f"""<div class="brand"><div class="brand-mark">{brand.icon}</div>
<div><div class="brand-name">{html.escape(brand.name)}</div>
<div class="brand-tag">{html.escape(brand.tagline)}</div></div></div>""",
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str, chips: list[tuple[str, str]] | None = None) -> None:
    """chips: list of (text, kind) where kind in {'accent','warn',''}."""
    chip_html = "".join(
        f'<span class="chip {"chip-" + k if k else ""}">{html.escape(t)}</span>' for t, k in (chips or [])
    )
    st.markdown(
        f"""<div class="page-head"><div><div class="page-title">{html.escape(title)}</div>
<div class="page-sub">{html.escape(subtitle)}</div></div><div>{chip_html}</div></div>""",
        unsafe_allow_html=True,
    )


def kpi_card(label: str, value: str, foot: str = "", delta: float | None = None,
             delta_fmt: str = "{:+.1%}", good_when_up: bool = True) -> str:
    delta_html = ""
    if delta is not None and delta == delta:  # skip NaN
        up = delta >= 0
        cls = "delta-up" if (up == good_when_up) else "delta-down"
        arrow = "▲" if up else "▼"
        delta_html = f'<span class="{cls}">{arrow} {delta_fmt.format(delta)}</span> '
    return (f'<div class="kpi"><div class="kpi-label">{html.escape(label)}</div>'
            f'<div class="kpi-value">{html.escape(value)}</div>'
            f'<div class="kpi-foot">{delta_html}{html.escape(foot)}</div></div>')


def kpi_row(cards: list[str]) -> None:
    cols = st.columns(len(cards))
    for col, card in zip(cols, cards):
        col.markdown(card, unsafe_allow_html=True)


def section(title: str, subtitle: str = "") -> None:
    st.markdown(f'<div class="section">{html.escape(title)}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="section-sub">{html.escape(subtitle)}</div>', unsafe_allow_html=True)


def note(text: str) -> None:
    st.markdown(f'<div class="note">{text}</div>', unsafe_allow_html=True)


def empty_state(message: str) -> None:
    st.markdown(f'<div class="empty">{html.escape(message)}</div>', unsafe_allow_html=True)


def status_label(level: str, text: str) -> str:
    """Status is never colour-alone: icon + text label."""
    return f"{STATUS_ICON.get(level, '●')} {text}"


# ---------- number formatting ----------

def fmt_money(x: float | None, currency: str = "$", decimals: int | None = None) -> str:
    if x is None or x != x:
        return "—"
    a = abs(x)
    sign = "-" if x < 0 else ""
    if decimals is None:
        if a >= 1e9:
            return f"{sign}{currency}{a / 1e9:,.2f}B"
        if a >= 1e6:
            return f"{sign}{currency}{a / 1e6:,.2f}M"
        if a >= 1e4:
            return f"{sign}{currency}{a / 1e3:,.1f}K"
        decimals = 2 if a < 1000 else 0
    return f"{sign}{currency}{a:,.{decimals}f}"


def fmt_pct(x: float | None, decimals: int = 1, signed: bool = False) -> str:
    if x is None or x != x:
        return "—"
    return f"{x:+.{decimals}%}" if signed else f"{x:.{decimals}%}"


def fmt_num(x: float | None, decimals: int = 0) -> str:
    if x is None or x != x:
        return "—"
    a = abs(x)
    if a >= 1e9:
        return f"{x / 1e9:,.2f}B"
    if a >= 1e6:
        return f"{x / 1e6:,.2f}M"
    return f"{x:,.{decimals}f}"


def style_fig(fig: go.Figure, height: int = 340, title: str | None = None,
              y_title: str | None = None, x_title: str | None = None) -> go.Figure:
    fig.update_layout(height=height)
    # outside bar labels must not be clipped by the plot area
    fig.update_traces(cliponaxis=False, selector=dict(type="bar"))
    # leave headroom for outside labels on horizontal bars
    xs = [v for t in fig.data if t.type == "bar" and t.orientation == "h" and t.textposition == "outside"
          for v in (list(t.x) if t.x is not None else []) if v is not None and v == v]
    if xs:
        lo, hi = min(min(xs), 0), max(max(xs), 0)
        pad = (hi - lo) * 0.16 or 1
        fig.update_xaxes(range=[lo - (pad if lo < 0 else 0), hi + (pad if hi > 0 else 0)])
    if title is not None:
        fig.update_layout(title=title)
    if y_title is not None:
        fig.update_yaxes(title_text=y_title)
    if x_title is not None:
        fig.update_xaxes(title_text=x_title)
    return fig
