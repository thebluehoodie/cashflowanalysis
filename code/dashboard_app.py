#!/usr/bin/env python3
"""
dashboard_app.py  (Production)

Dash dashboard for personal FP&A cashflow analytics.

Design goals
- Robust to schema drift:
  - Accepts classifier output (v3) columns OR older dashboard columns.
  - Harmonizes into: Cashflow_Section, Category_L1, Category_L2, Instrument, Counterparty_Core.
- FP&A-grade auditability:
  - Explicit data contract validation + visible banner (no silent masking of contract failures).
  - Clear semantics toggles for net vs gross movement, CC settlement spend proxy, baseline-only, NON-CASH inclusion.
- Uses Dash v2+ import patterns (dash_table via `from dash import dash_table`).
- Keeps transformations explicit + auditable.

Env
- ANALYSIS_INPUT_CSV (required): path to classified_transactions CSV
- DASH_HOST (optional): default 127.0.0.1
- DASH_PORT (optional): default 8050
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dotenv import load_dotenv
from dash import Dash, Input, Output, State, dcc, html, dash_table


# ======================================================
# SETTINGS (McKinsey-style FP&A palette)
# ======================================================

FONT_STACK = "Inter, -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif"
FIG_FONT = FONT_STACK
INTER_STYLESHEET = "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap"

# McKinsey-inspired color palette
COLORS = {
    "primary_blue": "#004990",      # McKinsey blue
    "secondary_blue": "#0067a5",    # Lighter blue
    "accent_teal": "#00838f",       # Teal accent
    "positive_green": "#16a34a",    # Positive variance
    "negative_red": "#dc2626",      # Negative variance
    "neutral_gray": "#64748b",      # Neutral text
    "dark_text": "#1e293b",         # Primary text
    "light_text": "#94a3b8",        # Secondary text
    "bg_primary": "#ffffff",        # Primary background
    "bg_secondary": "#f8fafc",      # Secondary background
    "border": "#e2e8f0",            # Borders
    "header_bg": "#004990",         # Header background
}

def load_env_file() -> None:
    """
    Load environment variables from a .env file if present.
    This is required because Python does not auto-load .env.
    """
    # Try current working directory first (common when running from project root)
    cwd_env = Path.cwd() / ".env"

    # Also try alongside this script (common when running from /code)
    script_env = Path(__file__).resolve().parent / ".env"

    if cwd_env.exists():
        load_dotenv(dotenv_path=cwd_env, override=False)
    elif script_env.exists():
        load_dotenv(dotenv_path=script_env, override=False)

def load_settings() -> Tuple[str, str, int]:
    input_csv = os.getenv("ANALYSIS_INPUT_CSV", "").strip()
    if not input_csv:
        raise ValueError("ANALYSIS_INPUT_CSV env var is required (path to classified_transactions_v3.csv)")

    host = os.getenv("DASH_HOST", "127.0.0.1").strip()
    port = int(os.getenv("DASH_PORT", "8050").strip())
    return input_csv, host, port

def get_assets_version() -> str:
    """
    Get cache-busting version string for assets.
    Uses DASH_ASSETS_VERSION env var, falls back to git SHA, then date.
    """
    env_version = os.getenv("DASH_ASSETS_VERSION", "").strip()
    if env_version:
        return env_version

    # Try git short SHA
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    # Fallback to date
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d")

def is_debug_ui_enabled() -> bool:
    """Check if UI debug mode is enabled via DASH_DEBUG_UI=1."""
    return os.getenv("DASH_DEBUG_UI", "0").strip() == "1"


# ======================================================
# SCHEMA HARMONIZATION
# ======================================================

# ======================================================
# DATA CONTRACT (FP&A-grade)
# ======================================================

# Hard requirements: without these the dashboard cannot operate deterministically.
# FP&A semantic correctness > convenience: Cashflow_Section is mandatory.
_REQUIRED_FIELDS = {"Amount"}

# Valid Cashflow_Section values (hard block on unrecognized)
_VALID_CASHFLOW_SECTIONS = {"OPERATING", "INVESTING", "FINANCING", "TRANSFER", "NON-CASH"}

# Soft requirements: dashboard will run in "degraded mode" if missing, but will surface a banner.
# NOTE: we intentionally validate by families (e.g., need either Date or YearMonth).
_SOFT_FAMILIES = {
    "identity": [{"Txn_ID"}],
    "time": [{"Date"}, {"YearMonth"}],  # need at least one
    "cashflow_section": [{"Cashflow_Section"}, {"Cashflow_Statement"}],
    "economic_pair": [{"Category_L1", "Category_L2"}, {"Economic_Purpose_L1", "Economic_Purpose_L2"}],
    "instrument": [{"Instrument"}, {"Bank_Rail"}],
    "counterparty": [{"Counterparty_Core"}, {"Description"}],
}

def validate_contract(df: pd.DataFrame) -> dict:
    """
    Returns a dict describing required/soft missing fields and basic data quality checks.
    This is UI-facing; do not silently "fix" contract failures.

    FP&A semantic correctness > convenience:
    - Cashflow_Section (or Cashflow_Statement alias) is a HARD requirement.
    - Unrecognized Cashflow_Section values are a HARD block.
    """
    missing_required = sorted([c for c in _REQUIRED_FIELDS if c not in df.columns])

    # Hard requirement: Cashflow_Section or Cashflow_Statement must exist
    has_cashflow_section = "Cashflow_Section" in df.columns or "Cashflow_Statement" in df.columns
    if not has_cashflow_section:
        missing_required.append("Cashflow_Section")

    # Hard requirement: Time column (Date or YearMonth) must exist
    has_time = "Date" in df.columns or "YearMonth" in df.columns
    if not has_time:
        missing_required.append("YearMonth (or Date)")

    soft_missing = []
    for fam, options in _SOFT_FAMILIES.items():
        # Skip cashflow_section and time families - they are now hard requirements
        if fam in ("cashflow_section", "time"):
            continue
        satisfied = False
        for opt in options:
            if all(c in df.columns for c in opt):
                satisfied = True
                break
        if not satisfied:
            pretty = " OR ".join(["+".join(sorted(list(opt))) for opt in options])
            soft_missing.append(f"{fam}: {pretty}")

    quality = {}
    if "Txn_ID" in df.columns:
        quality["txn_id_nulls"] = int(df["Txn_ID"].isna().sum())
        quality["txn_id_dupes"] = int(df["Txn_ID"].duplicated().sum())
    if "Date" in df.columns:
        dt = pd.to_datetime(df["Date"], errors="coerce", dayfirst=True)
        quality["date_parse_nulls"] = int(dt.isna().sum())
    if "Amount" in df.columns:
        amt = pd.to_numeric(df["Amount"], errors="coerce")
        quality["amount_parse_nulls"] = int(amt.isna().sum())

    # Validate Cashflow_Section values if column exists
    invalid_sections = []
    section_col = "Cashflow_Section" if "Cashflow_Section" in df.columns else "Cashflow_Statement" if "Cashflow_Statement" in df.columns else None
    if section_col:
        unique_sections = set(df[section_col].astype(str).str.upper().str.strip().unique())
        invalid_sections = sorted(unique_sections - _VALID_CASHFLOW_SECTIONS - {"", "NAN", "NONE"})

    return {
        "missing_required": sorted(set(missing_required)),
        "soft_missing": soft_missing,
        "quality": quality,
        "invalid_cashflow_sections": invalid_sections,
    }


_CANONICAL_DEFAULTS = {
    # IMPORTANT: do not default missing cashflow section to OPERATING (would hide upstream contract issues)
    "Cashflow_Section": "UNCLASSIFIED",
    "Category_L1": "UNCLASSIFIED",
    "Category_L2": "UNCLASSIFIED",
    "Instrument": "OTHER",
    "Flow_Nature": "UNKNOWN",
    "Record_Type": "TRANSACTION",
    "Counterparty_Core": "",
    "Counterparty_Norm": "",
    "Is_CC_Settlement": False,
    "Baseline_Eligible": True,
    "Stability_Class": "",
    "Event_Tag": "",
}

# (source, target) aliases
_SCHEMA_ALIASES: List[Tuple[str, str]] = [
    ("Cashflow_Statement", "Cashflow_Section"),
    ("Economic_Purpose_L1", "Category_L1"),
    ("Economic_Purpose_L2", "Category_L2"),
    ("Bank_Rail", "Instrument"),
]


def _to_yearmonth(series) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce", dayfirst=True)
    return dt.dt.strftime("%Y-%m").fillna("NaT")


_BOOL_TRUE = {"TRUE", "1", "YES", "Y", "T"}
_BOOL_FALSE = {"FALSE", "0", "NO", "N", "F", ""}

def _coerce_bool(val: object) -> bool:
    if pd.isna(val):
        return False
    if isinstance(val, (bool, np.bool_)):
        return bool(val)
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return bool(int(val))
    s = str(val).strip().upper()
    if s in _BOOL_TRUE:
        return True
    if s in _BOOL_FALSE:
        return False
    raise ValueError(f"Invalid boolean value: {val}")


def harmonize_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    Harmonize incoming dataset into canonical dashboard columns.

    Rule: Do NOT silently drop columns. Only add canonical columns (with explicit defaults)
    and map known aliases. Any missing contract issues should be surfaced by validate_contract.
    """
    df = df.copy()

    if "Amount" not in df.columns:
        raise ValueError("Missing required column: Amount")

    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0.0)

    # YearMonth
    if "YearMonth" not in df.columns or df["YearMonth"].astype(str).str.strip().eq("").all():
        if "Date" in df.columns:
            df["YearMonth"] = _to_yearmonth(df["Date"])
        else:
            df["YearMonth"] = "UNKNOWN"
    df["YearMonth"] = df["YearMonth"].astype(str)

    # Ensure Description exists (useful fallback for Counterparty)
    if "Description" not in df.columns:
        df["Description"] = ""

    # Create canonical columns; fill from defaults if missing
    for canonical, default in _CANONICAL_DEFAULTS.items():
        if canonical not in df.columns:
            df[canonical] = default

    # Apply alias mapping (only if source exists and target missing/empty)
    for src, tgt in _SCHEMA_ALIASES:
        if src in df.columns and tgt in df.columns:
            tgt_blank = df[tgt].astype(str).str.strip().eq("") | df[tgt].isna()
            if tgt_blank.all():
                df[tgt] = df[src]
            else:
                df.loc[tgt_blank, tgt] = df.loc[tgt_blank, src]
        elif src in df.columns and tgt not in df.columns:
            df[tgt] = df[src]

    # Counterparty fallback
    if "Counterparty_Norm" not in df.columns or df["Counterparty_Norm"].astype(str).str.strip().eq("").all():
        df["Counterparty_Norm"] = df["Description"].astype(str).str.upper()
    if "Counterparty_Core" not in df.columns or df["Counterparty_Core"].astype(str).str.strip().eq("").all():
        df["Counterparty_Core"] = df["Counterparty_Norm"].astype(str).str.slice(0, 80)

    # Normalize key fields for filters
    df["Cashflow_Section"] = df["Cashflow_Section"].astype(str).str.upper().str.strip()
    df["Category_L1"] = df["Category_L1"].astype(str).str.upper().str.strip()
    df["Category_L2"] = df["Category_L2"].astype(str).str.upper().str.strip()
    df["Instrument"] = df["Instrument"].astype(str).str.upper().str.strip()
    df["Record_Type"] = df["Record_Type"].astype(str).str.upper().str.strip()

    # Coerce booleans
    if "Is_CC_Settlement" in df.columns:
        df["Is_CC_Settlement"] = df["Is_CC_Settlement"].apply(_coerce_bool)
    else:
        df["Is_CC_Settlement"] = False

    if "Baseline_Eligible" in df.columns:
        df["Baseline_Eligible"] = df["Baseline_Eligible"].apply(_coerce_bool)
    else:
        df["Baseline_Eligible"] = True

    # Derived helpers
    df["AbsAmount"] = df["Amount"].abs()
    df["Is_Summary"] = df["Record_Type"].eq("SUMMARY") | df["Category_L2"].eq("BALANCE_BF")
    df["Is_TransferSection"] = df["Cashflow_Section"].eq("TRANSFER")
    df["Is_Inflow"] = df["Amount"] > 0
    df["Is_Outflow"] = df["Amount"] < 0

    return df


# ======================================================
# KPI + Analytics helpers (McKinsey-style)
# ======================================================

def _format_currency(value: float, show_sign: bool = False) -> str:
    """Format currency value with optional sign prefix."""
    if show_sign and value > 0:
        return f"+${value:,.0f}"
    elif value < 0:
        return f"-${abs(value):,.0f}"
    else:
        return f"${value:,.0f}"


def _format_delta_pct(current: float, prior: float) -> Tuple[str, str]:
    """Calculate and format percentage change. Returns (formatted_string, color)."""
    if prior == 0:
        if current == 0:
            return "—", COLORS["neutral_gray"]
        return "N/A", COLORS["neutral_gray"]

    pct_change = ((current - prior) / abs(prior)) * 100

    if pct_change > 0:
        return f"+{pct_change:.1f}%", COLORS["positive_green"]
    elif pct_change < 0:
        return f"{pct_change:.1f}%", COLORS["negative_red"]
    else:
        return "0.0%", COLORS["neutral_gray"]


def _kpi_tile(
    label: str,
    value: float,
    subtitle: str = "",
    color_by_sign: bool = False,
    prior_value: float = None,
    show_delta: bool = True,
) -> html.Div:
    """Enhanced KPI tile with optional delta indicator (McKinsey-style)."""
    if color_by_sign:
        value_color = COLORS["positive_green"] if value >= 0 else COLORS["negative_red"]
    else:
        value_color = COLORS["dark_text"]

    children = [
        html.Div(label, style={
            "fontSize": "11px",
            "color": COLORS["neutral_gray"],
            "fontWeight": "600",
            "textTransform": "uppercase",
            "letterSpacing": "0.5px",
            "marginBottom": "4px",
        }),
        html.Div(_format_currency(value), style={
            "fontSize": "28px",
            "fontWeight": "700",
            "color": value_color,
            "lineHeight": "1.1",
        }),
    ]

    # Add delta indicator if prior value provided
    if prior_value is not None and show_delta:
        delta = value - prior_value
        delta_pct_str, delta_color = _format_delta_pct(value, prior_value)
        delta_str = _format_currency(delta, show_sign=True)

        children.append(
            html.Div([
                html.Span(delta_str, style={"color": delta_color, "fontWeight": "600"}),
                html.Span(f" ({delta_pct_str})", style={"color": delta_color, "fontWeight": "500"}),
            ], style={"fontSize": "12px", "marginTop": "4px"})
        )

    if subtitle:
        children.append(html.Div(subtitle, style={
            "fontSize": "10px",
            "color": COLORS["light_text"],
            "marginTop": "6px",
            "fontStyle": "italic",
        }))

    return html.Div(
        children,
        className="kpi-tile",
    )


def _executive_kpi_strip(
    net_cash: float,
    operating: float,
    investing: float,
    financing: float,
    prior_net: float = None,
    prior_operating: float = None,
    prior_investing: float = None,
    prior_financing: float = None,
    period_label: str = "Selected Period",
) -> html.Div:
    """Build executive KPI strip with 4 primary metrics (McKinsey-style)."""
    return html.Div([
        html.Div([
            html.Span(period_label, style={
                "fontSize": "14px",
                "fontWeight": "600",
                "color": COLORS["dark_text"],
            }),
        ], style={"marginBottom": "12px"}),
        html.Div([
            _kpi_tile(
                "Net Cash Movement",
                net_cash,
                prior_value=prior_net,
                color_by_sign=True,
            ),
            _kpi_tile(
                "Operating Cash",
                operating,
                subtitle="Income minus expenses",
                prior_value=prior_operating,
                color_by_sign=True,
            ),
            _kpi_tile(
                "Investing Cash",
                investing,
                subtitle="Capex, savings, investments",
                prior_value=prior_investing,
                color_by_sign=True,
            ),
            _kpi_tile(
                "Financing Cash",
                financing,
                subtitle="Debt service, CC settlements",
                prior_value=prior_financing,
                color_by_sign=True,
            ),
        ], className="kpi-grid"),
    ], style={
        "backgroundColor": COLORS["bg_secondary"],
        "padding": "20px 24px",
        "borderRadius": "12px",
        "marginBottom": "24px",
        "border": f"1px solid {COLORS['border']}",
    })


# ======================================================
# FP&A Analytics: Period Comparison
# ======================================================

def compute_period_metrics(df: pd.DataFrame, yearmonths: List[str]) -> dict:
    """Compute aggregate metrics for a list of yearmonths."""
    d = df[df["YearMonth"].isin(yearmonths)].copy()

    # Filter to cash movement sections
    cash_df = d[d["Cashflow_Section"].isin(["OPERATING", "INVESTING", "FINANCING"])]

    operating = float(cash_df[cash_df["Cashflow_Section"] == "OPERATING"]["Amount"].sum())
    investing = float(cash_df[cash_df["Cashflow_Section"] == "INVESTING"]["Amount"].sum())
    financing = float(cash_df[cash_df["Cashflow_Section"] == "FINANCING"]["Amount"].sum())
    net_cash = operating + investing + financing

    # Operating breakdown
    op_income = float(d[(d["Cashflow_Section"] == "OPERATING") & (d["Amount"] > 0)]["Amount"].sum())
    op_expense = float(d[(d["Cashflow_Section"] == "OPERATING") & (d["Amount"] < 0)]["Amount"].sum())

    return {
        "net_cash": net_cash,
        "operating": operating,
        "investing": investing,
        "financing": financing,
        "op_income": op_income,
        "op_expense": op_expense,
        "txn_count": len(d),
    }


def get_prior_period_months(current_months: List[str], comparison_type: str) -> List[str]:
    """
    Get prior period months for comparison.

    comparison_type: 'MoM', 'QoQ', 'YoY'
    """
    if not current_months:
        return []

    # Parse months
    current_dates = [pd.Timestamp(m + "-01") for m in current_months]

    if comparison_type == "MoM":
        # Prior month(s) - same number of months, shifted back by 1
        offset = pd.DateOffset(months=1)
    elif comparison_type == "QoQ":
        # Prior quarter - same number of months, shifted back by 3
        offset = pd.DateOffset(months=3)
    elif comparison_type == "YoY":
        # Prior year - same number of months, shifted back by 12
        offset = pd.DateOffset(months=12)
    else:
        return []

    prior_dates = [d - offset for d in current_dates]
    return [d.strftime("%Y-%m") for d in prior_dates]


def compute_variance_drivers(
    current_df: pd.DataFrame,
    prior_df: pd.DataFrame,
    group_by: str = "Category_L2",
    top_n: int = 10,
) -> pd.DataFrame:
    """
    Compute variance drivers between two periods.
    Returns top contributors to the change in net cashflow.
    """
    # Aggregate by group
    current_agg = current_df.groupby(group_by)["Amount"].sum().reset_index()
    current_agg.columns = [group_by, "Current"]

    prior_agg = prior_df.groupby(group_by)["Amount"].sum().reset_index()
    prior_agg.columns = [group_by, "Prior"]

    # Merge
    merged = pd.merge(current_agg, prior_agg, on=group_by, how="outer").fillna(0)
    merged["Variance"] = merged["Current"] - merged["Prior"]
    merged["Abs_Variance"] = merged["Variance"].abs()

    # Sort by absolute variance
    merged = merged.sort_values("Abs_Variance", ascending=False).head(top_n)

    return merged


def _build_variance_bridge_figure(variance_df: pd.DataFrame, group_by: str = "Category_L2") -> go.Figure:
    """Build variance bridge chart showing what's driving the change."""
    if variance_df.empty:
        fig = go.Figure()
        fig.update_layout(
            title="Variance Analysis",
            annotations=[dict(text="No data for comparison", x=0.5, y=0.5, showarrow=False)],
            font=dict(family=FIG_FONT, size=12),
        )
        return fig

    # Sort for display (largest at top)
    df = variance_df.sort_values("Abs_Variance", ascending=True)

    colors = [COLORS["positive_green"] if v >= 0 else COLORS["negative_red"] for v in df["Variance"]]

    fig = go.Figure(go.Bar(
        x=df["Variance"],
        y=df[group_by],
        orientation="h",
        marker_color=colors,
        text=[f"{v:+,.0f}" for v in df["Variance"]],
        textposition="outside",
    ))

    fig.update_layout(
        title="Variance Drivers (Current vs Prior Period)",
        font=dict(family=FIG_FONT, size=12),
        margin=dict(t=50, b=40, l=150, r=50),
        xaxis_title="Variance (Current - Prior)",
        yaxis_title="",
        showlegend=False,
        plot_bgcolor=COLORS["bg_primary"],
        paper_bgcolor=COLORS["bg_primary"],
    )

    fig.update_xaxes(gridcolor=COLORS["border"], zerolinecolor=COLORS["neutral_gray"])
    fig.update_yaxes(gridcolor=COLORS["border"])

    return fig


def _build_waterfall_figure(operating_net: float, investing_net: float, financing_net: float) -> go.Figure:
    """Build a waterfall chart for cashflow sections (McKinsey-style)."""
    net_cash = operating_net + investing_net + financing_net

    # Waterfall data: measure types are 'relative' for intermediate, 'total' for final
    fig = go.Figure(go.Waterfall(
        name="Cashflow",
        orientation="v",
        measure=["relative", "relative", "relative", "total"],
        x=["Operating", "Investing", "Financing", "Net Cash"],
        y=[operating_net, investing_net, financing_net, net_cash],
        textposition="outside",
        text=[f"${v:,.0f}" for v in [operating_net, investing_net, financing_net, net_cash]],
        connector={"line": {"color": COLORS["neutral_gray"], "width": 1, "dash": "dot"}},
        increasing={"marker": {"color": COLORS["positive_green"]}},
        decreasing={"marker": {"color": COLORS["negative_red"]}},
        totals={"marker": {"color": COLORS["primary_blue"] if net_cash >= 0 else COLORS["negative_red"]}},
    ))

    fig.update_layout(
        title=dict(
            text="Cash Flow Bridge",
            font=dict(size=16, color=COLORS["dark_text"]),
        ),
        showlegend=False,
        font=dict(family=FIG_FONT, size=12, color=COLORS["dark_text"]),
        margin=dict(t=60, b=40, l=60, r=30),
        yaxis_title="Amount ($)",
        plot_bgcolor=COLORS["bg_primary"],
        paper_bgcolor=COLORS["bg_primary"],
    )

    fig.update_xaxes(gridcolor=COLORS["border"])
    fig.update_yaxes(gridcolor=COLORS["border"], tickformat="$,.0f")

    return fig


def _build_drivers_figure(df: pd.DataFrame, top_n: int = 5) -> go.Figure:
    """
    Build drivers bar chart: top N Category_L2 by absolute magnitude, signed values.
    Uses SAME filtered dataset as waterfall (Cashflow_Section IN OPERATING/INVESTING/FINANCING).
    """
    # Filter to cash movement sections only (same as waterfall)
    d = df[df["Cashflow_Section"].isin(["OPERATING", "INVESTING", "FINANCING"])].copy()

    if d.empty:
        fig = go.Figure()
        fig.update_layout(
            title=dict(text="Top Cash Movements", font=dict(size=16, color=COLORS["dark_text"])),
            annotations=[dict(text="No transactions in this period", x=0.5, y=0.5, showarrow=False)],
            font=dict(family=FIG_FONT, size=12),
            plot_bgcolor=COLORS["bg_primary"],
            paper_bgcolor=COLORS["bg_primary"],
        )
        return fig

    # Aggregate by Category_L2 with signed sum
    agg = d.groupby("Category_L2")["Amount"].sum().reset_index()
    agg["AbsAmount"] = agg["Amount"].abs()
    agg = agg.sort_values("AbsAmount", ascending=False).head(top_n)

    # Sort for display (largest magnitude at top in horizontal bar)
    agg = agg.sort_values("AbsAmount", ascending=True)

    # Color by sign
    colors = [COLORS["positive_green"] if v >= 0 else COLORS["negative_red"] for v in agg["Amount"]]

    fig = go.Figure(go.Bar(
        x=agg["Amount"],
        y=agg["Category_L2"],
        orientation="h",
        marker_color=colors,
        text=[f"${v:+,.0f}" for v in agg["Amount"]],
        textposition="outside",
    ))

    fig.update_layout(
        title=dict(text="Top Cash Movements", font=dict(size=16, color=COLORS["dark_text"])),
        font=dict(family=FIG_FONT, size=12, color=COLORS["dark_text"]),
        margin=dict(t=60, b=40, l=150, r=60),
        xaxis_title="Amount ($)",
        yaxis_title="",
        showlegend=False,
        plot_bgcolor=COLORS["bg_primary"],
        paper_bgcolor=COLORS["bg_primary"],
    )

    fig.update_xaxes(gridcolor=COLORS["border"], tickformat="$,.0f")
    fig.update_yaxes(gridcolor=COLORS["border"])

    return fig


def compute_monthly_kpis(df: pd.DataFrame) -> pd.DataFrame:
    """
    Assumes df already filtered for desired inclusion/exclusion and lens selection.
    """
    d = df.copy()

    op = d[d["Cashflow_Section"] == "OPERATING"]
    income = op[op["Amount"] > 0].groupby("YearMonth")["Amount"].sum()
    spend = op[op["Amount"] < 0].groupby("YearMonth")["Amount"].sum()  # negative

    inv = d[d["Cashflow_Section"] == "INVESTING"].groupby("YearMonth")["Amount"].sum()
    fin = d[d["Cashflow_Section"] == "FINANCING"].groupby("YearMonth")["Amount"].sum()
    net = d.groupby("YearMonth")["Amount"].sum()

    out = pd.DataFrame(
        {
            "Operating_Income": income,
            "Operating_Spend": spend,
            "Operating_Net": income.add(spend, fill_value=0),
            "Investing_Net": inv,
            "Financing_Net": fin,
            "Net_Cashflow": net,
        }
    ).fillna(0.0)

    out = out.reset_index().sort_values("YearMonth")
    return out


def recurring_candidates(df: pd.DataFrame, min_months: int = 6) -> pd.DataFrame:
    """
    Heuristic recurring detector for outflows:
    - Focus on outflows across sections (excluding transfers)
    - Group by Category_L2 and compute months_present, avg_abs, cov
    """
    d = df.copy()
    d = d[(d["Amount"] < 0) & (d["Cashflow_Section"].isin(["OPERATING", "FINANCING", "INVESTING", "NON-CASH", "UNCLASSIFIED"]))]
    d = d[~d["Is_TransferSection"]]

    g = d.groupby("Category_L2")
    months_present = g["YearMonth"].nunique()
    avg_abs = g["AbsAmount"].mean()
    std_abs = g["AbsAmount"].std(ddof=0)
    med_abs = g["AbsAmount"].median()

    cov = (std_abs / avg_abs).replace([np.inf, -np.inf], np.nan)

    out = pd.DataFrame(
        {
            "Category_L2": months_present.index,
            "months_present": months_present.values,
            "avg_abs": avg_abs.values,
            "med_abs": med_abs.values,
            "std_abs": std_abs.values,
            "cov": cov.values,
        }
    ).sort_values(["months_present", "avg_abs"], ascending=[False, False])

    out = out[out["months_present"] >= int(min_months)]
    return out


# ======================================================
# APP
# ======================================================

def build_app(df: pd.DataFrame, equity_df: pd.DataFrame | None = None, contract: dict | None = None, host: str = "127.0.0.1", port: int = 8050):
    # Explicitly set assets folder to ensure CSS loads regardless of working directory
    assets_path = Path(__file__).resolve().parent / "assets"

    # Get cache-busting version for assets
    assets_version = get_assets_version()

    # Verify assets folder exists
    if not assets_path.exists():
        print(f"[ERROR] Assets folder not found: {assets_path}")
        print(f"[ERROR] CSS will NOT load. Expected dashboard.css at: {assets_path / 'dashboard.css'}")
    else:
        css_file = assets_path / "dashboard.css"
        if css_file.exists():
            print(f"[OK] CSS file found: {css_file} ({css_file.stat().st_size} bytes)")
        else:
            print(f"[WARNING] dashboard.css not found at: {css_file}")

    print(f"[INFO] Assets folder: {assets_path}")
    print(f"[INFO] Assets version (cache-busting): {assets_version}")
    print(f"[INFO] Current working directory: {Path.cwd()}")
    print(f"[INFO] UI Debug mode: {'ENABLED' if is_debug_ui_enabled() else 'DISABLED'}")

    app = Dash(
        __name__,
        external_stylesheets=[INTER_STYLESHEET],
        assets_folder=str(assets_path),
        assets_url_path="/assets",
        url_base_pathname="/",
        suppress_callback_exceptions=False,
    )

    # Set assets version for cache-busting (no need to add it to url_base_pathname)
    # Dash automatically appends ?v={version} to asset URLs when assets_ignore is configured
    # For manual cache-busting, we'll add a meta tag in the layout
    app.index_string = f'''
    <!DOCTYPE html>
    <html>
        <head>
            {{%metas%}}
            <title>{{%title%}}</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
            <meta http-equiv="Pragma" content="no-cache">
            <meta http-equiv="Expires" content="0">
            {{%favicon%}}
            {{%css%}}
        </head>
        <body>
            {{%app_entry%}}
            <footer>
                {{%config%}}
                {{%scripts%}}
                {{%renderer%}}
            </footer>
            <script>
                // Force CSS reload by appending version query parameter
                document.querySelectorAll('link[rel="stylesheet"]').forEach(link => {{
                    if (link.href.includes('/assets/')) {{
                        const url = new URL(link.href);
                        url.searchParams.set('v', '{assets_version}');
                        link.href = url.toString();
                    }}
                }});
            </script>
        </body>
    </html>
    '''

    app.title = "Cashflow FP&A Dashboard"

    ym_options = sorted([x for x in df["YearMonth"].unique() if x and x != "NaT"])
    min_ym = ym_options[0] if ym_options else "UNKNOWN"
    max_ym = ym_options[-1] if ym_options else "UNKNOWN"

    section_options = ["OPERATING", "INVESTING", "FINANCING", "TRANSFER", "NON-CASH"]
    cat1_options = sorted(df["Category_L1"].unique())
    cat2_options = sorted(df["Category_L2"].unique())

    app.layout = html.Div(
        [
            # ===== EXECUTIVE HEADER (McKinsey-style) =====
            html.Div([
                html.Div([
                    html.H1("Personal Cash Flow Dashboard", style={
                        "color": COLORS["bg_primary"],
                        "fontSize": "24px",
                        "fontWeight": "700",
                        "margin": "0",
                    }),
                    html.Div("FP&A Analytics • Audit-Grade Classification", style={
                        "color": COLORS["light_text"],
                        "fontSize": "12px",
                        "marginTop": "4px",
                    }),
                ], style={"flex": "1"}),
            ], style={
                "backgroundColor": COLORS["header_bg"],
                "padding": "16px 24px",
                "display": "flex",
                "alignItems": "center",
                "marginBottom": "0",
            }),

            # CSS Load Indicator: visible (red) if CSS fails to load; hidden if CSS loads
            html.Div(
                "⚠ CSS NOT LOADED - Layout degraded. Check assets/dashboard.css path.",
                className="css-load-indicator",
                style={
                    "display": "block",
                    "backgroundColor": "#dc2626",
                    "color": "white",
                    "padding": "12px 24px",
                    "fontWeight": "600",
                    "textAlign": "center",
                    "fontSize": "14px",
                    "borderBottom": "3px solid #991b1b",
                },
            ),

            # Viewport Debug Indicator: only visible when DASH_DEBUG_UI=1
            html.Div(
                id="viewport-debug",
                children="Viewport: Loading...",
                className="viewport-debug-indicator",
                style={
                    "display": "block" if is_debug_ui_enabled() else "none",
                    "backgroundColor": "#0891b2",
                    "color": "white",
                    "padding": "8px 16px",
                    "fontWeight": "500",
                    "fontSize": "11px",
                    "fontFamily": "monospace",
                    "borderBottom": "2px solid #0e7490",
                    "textAlign": "center",
                },
            ),

            html.Div(id="contract_banner", style={"marginBottom": "0"}),
            dcc.Store(id="contract_store", data=contract or {}),
            dcc.Store(id="df_store"),

            # ===== MAIN CONTENT =====
            html.Div([
                # ===== FILTER PANEL (Collapsible sidebar feel) =====
                html.Div([
                    # Period Selection
                    html.Div([
                        html.Label("Period", style={"fontWeight": "600", "color": COLORS["dark_text"], "fontSize": "12px", "marginBottom": "6px"}),
                        html.Div([
                            dcc.Dropdown(
                                id="ym_start",
                                options=[{"label": x, "value": x} for x in ym_options],
                                value=min_ym,
                                clearable=False,
                                style={"fontSize": "12px"},
                            ),
                            html.Span("to", style={"margin": "0 8px", "color": COLORS["neutral_gray"]}),
                            dcc.Dropdown(
                                id="ym_end",
                                options=[{"label": x, "value": x} for x in ym_options],
                                value=max_ym,
                                clearable=False,
                                style={"fontSize": "12px"},
                            ),
                        ], style={"display": "flex", "alignItems": "center"}),
                    ], style={"marginBottom": "16px"}),

                    # Comparison Mode (NEW)
                    html.Div([
                        html.Label("Compare vs", style={"fontWeight": "600", "color": COLORS["dark_text"], "fontSize": "12px", "marginBottom": "6px"}),
                        dcc.RadioItems(
                            id="comparison_mode",
                            options=[
                                {"label": "None", "value": "NONE"},
                                {"label": "Prior Month (MoM)", "value": "MoM"},
                                {"label": "Prior Quarter (QoQ)", "value": "QoQ"},
                                {"label": "Prior Year (YoY)", "value": "YoY"},
                            ],
                            value="NONE",
                            labelStyle={"display": "block", "fontSize": "11px", "marginBottom": "4px"},
                        ),
                    ], style={"marginBottom": "16px"}),

                    # Cashflow Sections
                    html.Div([
                        html.Label("Sections", style={"fontWeight": "600", "color": COLORS["dark_text"], "fontSize": "12px", "marginBottom": "6px"}),
                        dcc.Dropdown(
                            id="section_filter",
                            options=[{"label": s, "value": s} for s in section_options],
                            value=["OPERATING", "INVESTING", "FINANCING"],
                            multi=True,
                            style={"fontSize": "11px"},
                        ),
                        dcc.Checklist(
                            id="exclude_transfers",
                            options=[{"label": "Exclude Transfers", "value": "EX"}],
                            value=["EX"],
                            style={"marginTop": "6px", "fontSize": "11px"},
                        ),
                        dcc.Checklist(
                            id="exclude_summary",
                            options=[{"label": "Exclude Balance B/F", "value": "SUM"}],
                            value=["SUM"],
                            style={"marginTop": "4px", "fontSize": "11px"},
                        ),
                    ], style={"marginBottom": "16px"}),

                    # View Options
                    html.Div([
                        html.Label("View Options", style={"fontWeight": "600", "color": COLORS["dark_text"], "fontSize": "12px", "marginBottom": "6px"}),
                        dcc.RadioItems(
                            id="cash_lens",
                            options=[
                                {"label": "Net Economic", "value": "NET_ECONOMIC"},
                                {"label": "Gross Movement", "value": "GROSS_MOVEMENT"},
                            ],
                            value="NET_ECONOMIC",
                            labelStyle={"display": "block", "fontSize": "11px", "marginBottom": "2px"},
                        ),
                        dcc.RadioItems(
                            id="spend_mode",
                            options=[
                                {"label": "Direct Spend", "value": "DIRECT"},
                                {"label": "Include CC Proxy", "value": "INCLUDE_CC_PROXY"},
                            ],
                            value="DIRECT",
                            labelStyle={"display": "block", "fontSize": "11px", "marginBottom": "2px"},
                            style={"marginTop": "8px"},
                        ),
                        dcc.RadioItems(
                            id="baseline_mode",
                            options=[
                                {"label": "All Transactions", "value": "ALL"},
                                {"label": "Baseline Only", "value": "BASELINE_ONLY"},
                            ],
                            value="ALL",
                            labelStyle={"display": "block", "fontSize": "11px", "marginBottom": "2px"},
                            style={"marginTop": "8px"},
                        ),
                        dcc.Checklist(
                            id="include_non_cash",
                            options=[{"label": "Include NON-CASH", "value": "NC"}],
                            value=[],
                            style={"marginTop": "6px", "fontSize": "11px"},
                        ),
                    ], style={"marginBottom": "16px"}),

                    # Category Filters
                    html.Div([
                        html.Label("Categories", style={"fontWeight": "600", "color": COLORS["dark_text"], "fontSize": "12px", "marginBottom": "6px"}),
                        dcc.Dropdown(
                            id="cat1_filter",
                            options=[{"label": c, "value": c} for c in cat1_options],
                            value=[],
                            multi=True,
                            placeholder="Category L1...",
                            style={"fontSize": "11px", "marginBottom": "6px"},
                        ),
                        dcc.Dropdown(
                            id="cat2_filter",
                            options=[{"label": c, "value": c} for c in cat2_options],
                            value=[],
                            multi=True,
                            placeholder="Category L2...",
                            style={"fontSize": "11px"},
                        ),
                    ], style={"marginBottom": "16px"}),

                    # Search
                    html.Div([
                        html.Label("Search", style={"fontWeight": "600", "color": COLORS["dark_text"], "fontSize": "12px", "marginBottom": "6px"}),
                        dcc.Input(
                            id="search_text",
                            type="text",
                            placeholder="Description contains...",
                            style={"width": "100%", "fontSize": "11px", "padding": "6px"},
                        ),
                    ], style={"marginBottom": "16px"}),

                    # Drilldown Options
                    html.Div([
                        html.Label("Drilldown", style={"fontWeight": "600", "color": COLORS["dark_text"], "fontSize": "12px", "marginBottom": "6px"}),
                        dcc.RadioItems(
                            id="drill_mode",
                            options=[
                                {"label": "Category L2", "value": "Category_L2"},
                                {"label": "Counterparty", "value": "Counterparty_Core"},
                                {"label": "Instrument", "value": "Instrument"},
                            ],
                            value="Category_L2",
                            labelStyle={"display": "block", "fontSize": "11px", "marginBottom": "2px"},
                        ),
                        html.Label("Top N", style={"marginTop": "8px", "fontSize": "11px", "color": COLORS["neutral_gray"]}),
                        dcc.Slider(id="topn", min=5, max=50, step=5, value=15, marks={5: "5", 25: "25", 50: "50"}),
                    ]),

                ], className="sidebar"),

                # ===== MAIN DASHBOARD AREA =====
                html.Div([
                    # ===== EXECUTIVE KPI STRIP =====
                    html.Div(id="kpi_tiles", style={"marginBottom": "24px"}),

                    # ===== CHARTS ROW 1: Waterfall + Drivers =====
                    html.Div([
                        html.Div([
                            dcc.Graph(
                                id="waterfall_chart",
                                style={"height": "380px", "width": "100%"},
                                config={"responsive": True},
                            )
                        ], className="card-chart"),
                        html.Div([
                            dcc.Graph(
                                id="drivers_chart",
                                style={"height": "380px", "width": "100%"},
                                config={"responsive": True},
                            )
                        ], className="card-chart"),
                    ], className="chart-row"),

                    # ===== VARIANCE ANALYSIS (NEW - for comparison mode) =====
                    html.Div(id="variance_section", style={"marginBottom": "24px"}),

                    # ===== EQUITY / NET WORTH =====
                    html.Div(id="equity_section", style={"marginBottom": "24px"}),

                    # ===== TREND LINE =====
                    html.Div([
                        html.H3("Monthly Trend", className="section-heading"),
                        html.Div([
                            dcc.Graph(
                                id="net_cashflow_line",
                                style={"height": "300px", "width": "100%"},
                                config={"responsive": True},
                            )
                        ], className="card-chart"),
                    ], className="chart-full"),

                    # ===== DETAILED CHARTS =====
                    html.Div([
                        html.H3("Income & Spend Breakdown", className="section-heading"),
                        html.Div([
                            html.Div([
                                dcc.Graph(
                                    id="income_stack",
                                    style={"height": "320px", "width": "100%"},
                                    config={"responsive": True},
                                )
                            ], className="card-chart"),
                            html.Div([
                                dcc.Graph(
                                    id="spend_stack",
                                    style={"height": "320px", "width": "100%"},
                                    config={"responsive": True},
                                )
                            ], className="card-chart"),
                        ], className="chart-row"),
                    ], className="chart-full"),

                    html.Div([
                        html.Div([
                            dcc.Graph(
                                id="drill_bar",
                                style={"height": "320px", "width": "100%"},
                                config={"responsive": True},
                            )
                        ], className="card-chart"),
                        html.Div([
                            dcc.Graph(
                                id="recurring_bar",
                                style={"height": "320px", "width": "100%"},
                                config={"responsive": True},
                            )
                        ], className="card-chart"),
                    ], className="chart-row"),

                    # ===== AUDIT TRAIL =====
                    html.Div([
                        html.H3("Audit Trail", className="section-heading"),
                        html.Div([
                            dash_table.DataTable(
                                id="tx_table",
                                page_size=25,
                                sort_action="native",
                                filter_action="native",
                                style_table={"overflowX": "auto"},
                                style_cell={
                                    "fontFamily": FONT_STACK,
                                    "fontSize": "11px",
                                    "padding": "8px",
                                    "textAlign": "left",
                                },
                                style_header={
                                    "fontWeight": "600",
                                    "backgroundColor": COLORS["bg_secondary"],
                                    "borderBottom": f"2px solid {COLORS['border']}",
                                },
                                style_data_conditional=[
                                    {"if": {"row_index": "odd"}, "backgroundColor": COLORS["bg_secondary"]},
                                ],
                            ),
                        ], className="card"),
                    ]),

                ], className="main-content"),

            ], className="app-shell"),
        ],
        style={
            "fontFamily": FONT_STACK,
            "backgroundColor": COLORS["bg_secondary"],
            "margin": "0",
            "padding": "0",
            "minHeight": "100vh",
        },
    )

    # ---------- contract banner ----------
    @app.callback(Output("contract_banner", "children"), Input("contract_store", "data"))
    def render_contract_banner(c):
        if not c:
            return ""
        missing_required = c.get("missing_required") or []
        soft_missing = c.get("soft_missing") or []
        quality = c.get("quality") or {}

        if not missing_required and not soft_missing and not quality:
            return ""

        parts = []
        if missing_required:
            parts.append(html.Div(["Missing required fields: ", html.Code(", ".join(missing_required))],
                                  style={"color": "#b00020", "fontWeight": "500"}))
        if soft_missing:
            parts.append(html.Div(["Degraded mode (missing): ", html.Code(" | ".join(soft_missing))],
                                  style={"color": "#8a4b08"}))
        if quality:
            qtxt = ", ".join([f"{k}={v}" for k, v in quality.items()])
            parts.append(html.Div(["Quality checks: ", html.Code(qtxt)],
                                  style={"color": "#333"}))

        return html.Div(
            parts,
            style={
                "border": "1px solid #ddd",
                "padding": "8px 10px",
                "backgroundColor": "#fafafa",
            },
        )

    # Viewport debug: clientside callback to update viewport info on page load
    if is_debug_ui_enabled():
        app.clientside_callback(
            """
            function(data) {
                const width = window.innerWidth;
                let breakpoint = 'UNKNOWN';

                if (width >= 1400) {
                    breakpoint = 'DESKTOP (≥1400px): 4 KPI columns';
                } else if (width >= 1100) {
                    breakpoint = 'LAPTOP (1100-1399px): 2 KPI columns';
                } else {
                    breakpoint = 'MOBILE (<1100px): 1 KPI column';
                }

                return `Viewport: ${width}px | ${breakpoint}`;
            }
            """,
            Output("viewport-debug", "children"),
            Input("contract_store", "data"),
        )

    # ---------- filtering ----------
    @app.callback(
        Output("df_store", "data"),
        Input("ym_start", "value"),
        Input("ym_end", "value"),
        Input("section_filter", "value"),
        Input("cat1_filter", "value"),
        Input("cat2_filter", "value"),
        Input("exclude_transfers", "value"),
        Input("exclude_summary", "value"),
        Input("baseline_mode", "value"),
        Input("include_non_cash", "value"),
        Input("search_text", "value"),
    )
    def filter_df(ym_start, ym_end, sections, cat1, cat2, ex_transfers, ex_summary, baseline_mode, include_non_cash, search_text):
        d = df.copy()

        if ym_start and ym_end and ym_start != "UNKNOWN" and ym_end != "UNKNOWN":
            d = d[(d["YearMonth"] >= ym_start) & (d["YearMonth"] <= ym_end)]

        if sections:
            d = d[d["Cashflow_Section"].isin([s.upper() for s in sections])]

        if ex_transfers and "EX" in ex_transfers:
            d = d[~d["Is_TransferSection"]]

        if ex_summary and "SUM" in ex_summary:
            d = d[~d["Is_Summary"]]

        if cat1:
            d = d[d["Category_L1"].isin([c.upper() for c in cat1])]
        if cat2:
            d = d[d["Category_L2"].isin([c.upper() for c in cat2])]

        # Baseline mode (if column exists)
        if baseline_mode == "BASELINE_ONLY" and "Baseline_Eligible" in d.columns:
            d = d[d["Baseline_Eligible"].fillna(False) == True]

        # NON-CASH inclusion (explicit)
        if not (include_non_cash and "NC" in include_non_cash):
            d = d[d["Cashflow_Section"] != "NON-CASH"]

        # Search
        if search_text and str(search_text).strip():
            s = str(search_text).strip().lower()
            hay = (
                d["Description"].astype(str).str.lower().fillna("")
                + " "
                + d["Counterparty_Core"].astype(str).str.lower().fillna("")
            )
            d = d[hay.str.contains(s, regex=False)]

        return d.to_json(date_format="iso", orient="split")

    # ---------- charts + KPIs ----------
    @app.callback(
        Output("kpi_tiles", "children"),
        Output("variance_section", "children"),
        Output("waterfall_chart", "figure"),
        Output("drivers_chart", "figure"),
        Output("net_cashflow_line", "figure"),
        Output("income_stack", "figure"),
        Output("spend_stack", "figure"),
        Output("drill_bar", "figure"),
        Output("recurring_bar", "figure"),
        Input("df_store", "data"),
        Input("drill_mode", "value"),
        Input("topn", "value"),
        Input("cash_lens", "value"),
        Input("spend_mode", "value"),
        Input("comparison_mode", "value"),
        Input("ym_start", "value"),
        Input("ym_end", "value"),
    )
    def refresh_views(djson, drill_mode, topn, cash_lens, spend_mode, comparison_mode, ym_start, ym_end):
        d = pd.read_json(StringIO(djson), orient="split") if djson else df.copy()

        monthly_source = d.copy()

        # Cash lens determines whether transfers are included in net calculations/line
        if cash_lens == "NET_ECONOMIC" and "Is_TransferSection" in monthly_source.columns:
            monthly_source = monthly_source[~monthly_source["Is_TransferSection"]].copy()

        monthly = compute_monthly_kpis(monthly_source)

        # ===== CURRENT PERIOD METRICS =====
        cash_movement_df = monthly_source[monthly_source["Cashflow_Section"].isin(["OPERATING", "INVESTING", "FINANCING"])].copy()

        operating_net = float(cash_movement_df[cash_movement_df["Cashflow_Section"] == "OPERATING"]["Amount"].sum())
        investing_net = float(cash_movement_df[cash_movement_df["Cashflow_Section"] == "INVESTING"]["Amount"].sum())
        financing_net = float(cash_movement_df[cash_movement_df["Cashflow_Section"] == "FINANCING"]["Amount"].sum())
        net_cash_movement = operating_net + investing_net + financing_net

        # ===== PRIOR PERIOD METRICS (if comparison mode enabled) =====
        prior_operating = None
        prior_investing = None
        prior_financing = None
        prior_net = None
        variance_section = None

        if comparison_mode and comparison_mode != "NONE" and ym_start and ym_end:
            # Get current period months
            current_months = sorted([
                ym for ym in monthly_source["YearMonth"].unique()
                if ym and ym != "NaT"
            ])

            # Get prior period months
            prior_months = get_prior_period_months(current_months, comparison_mode)

            # Compute prior period metrics from FULL dataset (before filtering)
            prior_df = df[df["YearMonth"].isin(prior_months)].copy()
            if cash_lens == "NET_ECONOMIC" and "Is_TransferSection" in prior_df.columns:
                prior_df = prior_df[~prior_df["Is_TransferSection"]].copy()

            prior_cash_df = prior_df[prior_df["Cashflow_Section"].isin(["OPERATING", "INVESTING", "FINANCING"])]

            if not prior_cash_df.empty:
                prior_operating = float(prior_cash_df[prior_cash_df["Cashflow_Section"] == "OPERATING"]["Amount"].sum())
                prior_investing = float(prior_cash_df[prior_cash_df["Cashflow_Section"] == "INVESTING"]["Amount"].sum())
                prior_financing = float(prior_cash_df[prior_cash_df["Cashflow_Section"] == "FINANCING"]["Amount"].sum())
                prior_net = prior_operating + prior_investing + prior_financing

                # Build variance drivers
                variance_drivers = compute_variance_drivers(cash_movement_df, prior_cash_df, "Category_L2", 10)
                fig_variance = _build_variance_bridge_figure(variance_drivers, "Category_L2")

                # Build variance section
                comparison_labels = {"MoM": "Prior Month", "QoQ": "Prior Quarter", "YoY": "Prior Year"}
                variance_section = html.Div([
                    html.H3(
                        f"Variance Analysis vs {comparison_labels.get(comparison_mode, 'Prior Period')}",
                        className="section-heading",
                    ),
                    html.Div([
                        dcc.Graph(
                            figure=fig_variance,
                            style={"height": "350px", "width": "100%"},
                            config={"responsive": True},
                        )
                    ], className="card-chart"),
                ], className="chart-full")

        # ===== BUILD KPI STRIP =====
        period_label = f"{ym_start} to {ym_end}" if ym_start and ym_end else "Selected Period"
        kpi_strip = _executive_kpi_strip(
            net_cash=net_cash_movement,
            operating=operating_net,
            investing=investing_net,
            financing=financing_net,
            prior_net=prior_net,
            prior_operating=prior_operating,
            prior_investing=prior_investing,
            prior_financing=prior_financing,
            period_label=period_label,
        )

        # ===== WATERFALL CHART =====
        fig_waterfall = _build_waterfall_figure(operating_net, investing_net, financing_net)

        # ===== DRIVERS CHART (Top 5 Category_L2 by magnitude) =====
        fig_drivers = _build_drivers_figure(cash_movement_df, top_n=5)

        # ===== DETAILED ANALYSIS CHARTS (Below the fold) =====

        # Spend lens: optionally include CC settlement proxy outflows
        sp_base = monthly_source[(monthly_source["Cashflow_Section"] == "OPERATING") & (monthly_source["Amount"] < 0)].copy()
        if spend_mode == "DIRECT" and "Is_CC_Settlement" in monthly_source.columns:
            sp_base = sp_base[~monthly_source.loc[sp_base.index, "Is_CC_Settlement"].fillna(False)]
        elif spend_mode == "INCLUDE_CC_PROXY" and "Is_CC_Settlement" in monthly_source.columns:
            cc = monthly_source[(monthly_source["Is_CC_Settlement"].fillna(False)) & (monthly_source["Amount"] < 0)].copy()
            sp_base = pd.concat([sp_base, cc], ignore_index=False)

        # Net cashflow line chart (improved styling)
        fig_net = px.line(monthly, x="YearMonth", y="Net_Cashflow", markers=True)
        fig_net.update_traces(
            line=dict(color=COLORS["primary_blue"], width=2),
            marker=dict(size=8, color=COLORS["primary_blue"]),
        )
        fig_net.update_layout(
            title=dict(text="Net Cash Flow Trend", font=dict(size=14, color=COLORS["dark_text"])),
            font=dict(family=FIG_FONT, size=12, color=COLORS["dark_text"]),
            plot_bgcolor=COLORS["bg_primary"],
            paper_bgcolor=COLORS["bg_primary"],
            xaxis_title="Month",
            yaxis_title="Net Cash Flow ($)",
        )
        fig_net.update_xaxes(gridcolor=COLORS["border"])
        fig_net.update_yaxes(gridcolor=COLORS["border"], tickformat="$,.0f")

        # Income chart
        inc = monthly_source[(monthly_source["Cashflow_Section"] == "OPERATING") & (monthly_source["Amount"] > 0)]
        if inc.empty:
            fig_inc = px.bar(title="Operating Income (no data)")
        else:
            inc_g = inc.groupby(["YearMonth", "Category_L2"])["Amount"].sum().reset_index()
            fig_inc = px.bar(inc_g, x="YearMonth", y="Amount", color="Category_L2")
            fig_inc.update_layout(
                title=dict(text="Operating Income", font=dict(size=14, color=COLORS["dark_text"])),
            )
        fig_inc.update_layout(
            font=dict(family=FIG_FONT, size=12, color=COLORS["dark_text"]),
            plot_bgcolor=COLORS["bg_primary"],
            paper_bgcolor=COLORS["bg_primary"],
        )
        fig_inc.update_xaxes(gridcolor=COLORS["border"])
        fig_inc.update_yaxes(gridcolor=COLORS["border"], tickformat="$,.0f")

        # Spend chart
        sp = sp_base.copy()
        if sp.empty:
            fig_sp = px.bar(title="Operating Spend (no data)")
        else:
            sp_g = sp.groupby(["YearMonth", "Category_L2"])["AbsAmount"].sum().reset_index()
            fig_sp = px.bar(sp_g, x="YearMonth", y="AbsAmount", color="Category_L2")
            fig_sp.update_layout(
                title=dict(text=f"Operating Spend [{spend_mode}]", font=dict(size=14, color=COLORS["dark_text"])),
            )
        fig_sp.update_layout(
            font=dict(family=FIG_FONT, size=12, color=COLORS["dark_text"]),
            plot_bgcolor=COLORS["bg_primary"],
            paper_bgcolor=COLORS["bg_primary"],
        )
        fig_sp.update_xaxes(gridcolor=COLORS["border"])
        fig_sp.update_yaxes(gridcolor=COLORS["border"], tickformat="$,.0f")

        # Drilldown chart
        drill_col = drill_mode if drill_mode in d.columns else "Category_L2"
        g = d.groupby(drill_col)["AbsAmount"].sum().reset_index().sort_values("AbsAmount", ascending=False).head(int(topn))
        fig_drill = px.bar(g, x="AbsAmount", y=drill_col, orientation="h")
        fig_drill.update_layout(
            title=dict(text=f"Top {topn} by Amount ({drill_col})", font=dict(size=14, color=COLORS["dark_text"])),
            font=dict(family=FIG_FONT, size=12, color=COLORS["dark_text"]),
            plot_bgcolor=COLORS["bg_primary"],
            paper_bgcolor=COLORS["bg_primary"],
        )
        fig_drill.update_traces(marker_color=COLORS["primary_blue"])
        fig_drill.update_xaxes(gridcolor=COLORS["border"], tickformat="$,.0f")
        fig_drill.update_yaxes(gridcolor=COLORS["border"])

        # Recurring candidates chart
        rec = recurring_candidates(d, min_months=6).head(25).copy()
        if rec.empty:
            fig_rec = px.bar(title="Recurring candidates (no data)")
        else:
            rec["label"] = (
                rec["Category_L2"].astype(str)
                + " | m=" + rec["months_present"].astype(int).astype(str)
                + " | cov=" + rec["cov"].fillna(np.nan).round(2).astype(str)
            )
            fig_rec = px.bar(rec, x="avg_abs", y="label", orientation="h")
            fig_rec.update_traces(marker_color=COLORS["accent_teal"])
        fig_rec.update_layout(
            title=dict(text="Recurring Expenses", font=dict(size=14, color=COLORS["dark_text"])),
            font=dict(family=FIG_FONT, size=12, color=COLORS["dark_text"]),
            plot_bgcolor=COLORS["bg_primary"],
            paper_bgcolor=COLORS["bg_primary"],
            xaxis_title="Avg Monthly Amount ($)",
            yaxis_title="",
        )
        fig_rec.update_xaxes(gridcolor=COLORS["border"], tickformat="$,.0f")
        fig_rec.update_yaxes(gridcolor=COLORS["border"])

        return kpi_strip, variance_section, fig_waterfall, fig_drivers, fig_net, fig_inc, fig_sp, fig_drill, fig_rec

    # ---------- equity section ----------
    @app.callback(
        Output("equity_section", "children"),
        Input("ym_start", "value"),
        Input("ym_end", "value"),
    )
    def refresh_equity(ym_start, ym_end):
        """
        Render equity/net worth section if equity data is available.
        Shows graceful degradation if no equity data loaded.
        """
        if equity_df is None or equity_df.empty:
            # Graceful degradation: no equity data
            return html.Div([
                html.H3("Equity / Net Worth", className="section-heading"),
                html.Div([
                    html.Div([
                        html.P("Equity data not loaded", style={
                            "color": COLORS["neutral_gray"],
                            "fontSize": "14px",
                            "textAlign": "center",
                            "padding": "40px 20px",
                        }),
                        html.P("To enable equity analytics, create: inputs/loan_balances.csv", style={
                            "color": COLORS["light_text"],
                            "fontSize": "12px",
                            "textAlign": "center",
                        }),
                    ], className="card-chart"),
                ]),
            ], className="chart-full")

        # Filter equity data by selected period
        eq = equity_df.copy()
        if ym_start and ym_end:
            eq = eq[(eq["AsOfMonth"] >= ym_start) & (eq["AsOfMonth"] <= ym_end)]

        # Calculate total equity built in period
        total_principal_paid = eq["Principal_Paid"].sum()
        total_balance_increase = eq["Balance_Increase"].sum()

        # Calculate cumulative principal paid over time
        eq_sorted = equity_df.sort_values(["Loan_ID", "AsOfMonth"])
        eq_sorted["Cumulative_Principal_Paid"] = eq_sorted.groupby("Loan_ID")["Principal_Paid"].cumsum()

        # Aggregate by month for chart
        monthly_equity = eq_sorted.groupby("AsOfMonth").agg({
            "Principal_Paid": "sum",
            "Cumulative_Principal_Paid": "sum",
            "Balance_Increase": "sum",
        }).reset_index()

        # Create equity trend chart
        if monthly_equity.empty:
            fig_equity = go.Figure()
            fig_equity.add_annotation(
                text="No equity data in selected period",
                x=0.5, y=0.5,
                xref="paper", yref="paper",
                showarrow=False,
                font=dict(size=14, color=COLORS["neutral_gray"])
            )
        else:
            fig_equity = go.Figure()

            # Add cumulative principal paid line
            fig_equity.add_trace(go.Scatter(
                x=monthly_equity["AsOfMonth"],
                y=monthly_equity["Cumulative_Principal_Paid"],
                mode="lines+markers",
                name="Cumulative Equity Built",
                line=dict(color=COLORS["positive_green"], width=2),
                marker=dict(size=8, color=COLORS["positive_green"]),
            ))

            # Add monthly principal paid bars
            fig_equity.add_trace(go.Bar(
                x=monthly_equity["AsOfMonth"],
                y=monthly_equity["Principal_Paid"],
                name="Monthly Principal Paid",
                marker_color=COLORS["secondary_blue"],
                yaxis="y2",
            ))

        fig_equity.update_layout(
            title=dict(text="Equity Build-Up (Principal Paid)", font=dict(size=16, color=COLORS["dark_text"])),
            font=dict(family=FIG_FONT, size=12, color=COLORS["dark_text"]),
            plot_bgcolor=COLORS["bg_primary"],
            paper_bgcolor=COLORS["bg_primary"],
            xaxis_title="Month",
            yaxis=dict(title="Cumulative Equity ($)", side="left", tickformat="$,.0f"),
            yaxis2=dict(title="Monthly Principal ($)", side="right", overlaying="y", tickformat="$,.0f"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(t=80, b=40, l=60, r=60),
        )
        fig_equity.update_xaxes(gridcolor=COLORS["border"])
        fig_equity.update_yaxes(gridcolor=COLORS["border"])

        # Create KPI tiles for equity
        equity_kpis = html.Div([
            html.Div([
                html.Div("Equity Built (Principal Paid)", style={
                    "fontSize": "12px",
                    "color": COLORS["neutral_gray"],
                    "marginBottom": "8px",
                }),
                html.Div(f"${total_principal_paid:,.0f}", style={
                    "fontSize": "24px",
                    "fontWeight": "600",
                    "color": COLORS["positive_green"],
                }),
            ], style={
                "backgroundColor": COLORS["bg_primary"],
                "padding": "16px",
                "borderRadius": "8px",
                "border": f"1px solid {COLORS['border']}",
                "flex": "1",
            }),
            html.Div([
                html.Div("Balance Increases (Refinance/Top-up)", style={
                    "fontSize": "12px",
                    "color": COLORS["neutral_gray"],
                    "marginBottom": "8px",
                }),
                html.Div(f"${total_balance_increase:,.0f}", style={
                    "fontSize": "24px",
                    "fontWeight": "600",
                    "color": COLORS["accent_teal"],
                }),
            ], style={
                "backgroundColor": COLORS["bg_primary"],
                "padding": "16px",
                "borderRadius": "8px",
                "border": f"1px solid {COLORS['border']}",
                "flex": "1",
            }),
        ], style={
            "display": "flex",
            "gap": "16px",
            "marginBottom": "16px",
        })

        return html.Div([
            html.H3("Equity / Net Worth", className="section-heading"),
            equity_kpis,
            html.Div([
                dcc.Graph(
                    figure=fig_equity,
                    style={"height": "350px", "width": "100%"},
                    config={"responsive": True},
                )
            ], className="card-chart"),
        ], className="chart-full")

    # ---------- transaction table ----------
    @app.callback(
        Output("tx_table", "columns"),
        Output("tx_table", "data"),
        Input("df_store", "data"),
    )
    def refresh_table(djson):
        d = pd.read_json(StringIO(djson), orient="split") if djson else df.copy()

        preferred_cols = [
            "Date", "YearMonth", "Description", "Amount", "Balance",
            "Withdrawals", "Deposits",
            "Txn_ID", "Record_Type", "Flow_Nature",
            "Cashflow_Statement", "Cashflow_Section",
            "Economic_Purpose_L1", "Economic_Purpose_L2",
            "Managerial_Purpose_L1", "Managerial_Purpose_L2",
            "Baseline_Eligible", "Stability_Class", "Event_Tag",
            "Bank_Rail", "Instrument",
            "Counterparty_Norm", "Counterparty_Core",
            "Was_Overridden", "Override_ID_Applied", "Override_Reason",
            "Rule_ID", "Rule_Explanation",
            "SourceFile", "RowOrder", "RowsMerged",
        ]
        cols = [c for c in preferred_cols if c in d.columns]
        columns = [{"name": c, "id": c} for c in cols]
        data = d[cols].to_dict("records")
        return columns, data

    return app


def main():
    load_env_file()
    input_csv, host, port = load_settings()
    df_raw = pd.read_csv(input_csv)

    contract = validate_contract(df_raw)

    # Hard block: missing required fields
    if contract.get("missing_required"):
        raise ValueError(
            f"Data contract failure: missing required fields {contract.get('missing_required')}. "
            "FP&A semantic correctness requires explicit section classification."
        )

    # Hard block: unrecognized Cashflow_Section values
    if contract.get("invalid_cashflow_sections"):
        raise ValueError(
            f"Data contract failure: unrecognized Cashflow_Section values {contract.get('invalid_cashflow_sections')}. "
            f"Valid values: {sorted(_VALID_CASHFLOW_SECTIONS)}"
        )

    df = harmonize_schema(df_raw)

    # Load equity data if available (optional)
    equity_df = None
    try:
        # Import validation function
        from networth.loan_equity import validate_equity_data

        # Look for equity file in repo root / outputs
        repo_root = Path(__file__).resolve().parents[1]
        equity_csv = repo_root / "outputs" / "equity_build_up_monthly.csv"
        if equity_csv.exists():
            equity_df = pd.read_csv(equity_csv)
            print(f"[OK] Loaded equity data: {len(equity_df)} records")

            # Validate equity data
            is_valid, errors = validate_equity_data(equity_df)
            if not is_valid:
                print("[WARNING] Equity data validation failed:")
                for err in errors:
                    if not err.startswith("[WARNING]"):
                        print(f"  [ERROR] {err}")
                print("[WARNING] Equity section may show incorrect data. Please fix equity CSV.")
            elif errors:
                print("[INFO] Equity validation warnings:")
                for err in errors:
                    print(f"  {err}")
            else:
                print("[OK] Equity data validation passed")
        else:
            print(f"[INFO] No equity data found ({equity_csv})")
    except Exception as e:
        print(f"[WARNING] Could not load equity data: {e}")
        equity_df = None

    app = build_app(df, equity_df=equity_df, contract=contract, host=host, port=port)
    app.run(debug=False, host=host, port=port)


if __name__ == "__main__":
    main()
