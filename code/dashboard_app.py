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
# SETTINGS
# ======================================================

FONT_STACK = "Inter, -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif"
FIG_FONT = FONT_STACK
INTER_STYLESHEET = "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap"
GLOBAL_CSS = f"""
html, body, div, span, applet, object, iframe,
h1, h2, h3, h4, h5, h6, p, blockquote, pre,
a, abbr, acronym, address, big, cite, code,
del, dfn, em, img, ins, kbd, q, s, samp,
small, strike, strong, sub, sup, tt, var,
b, u, i, center,
dl, dt, dd, ol, ul, li,
fieldset, form, label, legend,
table, caption, tbody, tfoot, thead, tr, th, td,
article, aside, canvas, details, embed,
figure, figcaption, footer, header, hgroup,
menu, nav, output, ruby, section, summary,
time, mark, audio, video,
input, textarea, select, button {{
  font-family: {FONT_STACK} !important;
}}
"""

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
# KPI + Analytics helpers
# ======================================================

def _kpi_tile(label: str, value: float, subtitle: str = "", color_by_sign: bool = False) -> html.Div:
    """KPI tile with optional subtitle and sign-based coloring."""
    if color_by_sign:
        color = "#16a34a" if value >= 0 else "#dc2626"  # green / red
    else:
        color = "#111"

    children = [
        html.Div(label, style={"fontSize": "12px", "color": "#666", "fontWeight": "500"}),
        html.Div(f"{value:,.2f}", style={"fontSize": "22px", "fontWeight": "700", "color": color}),
    ]
    if subtitle:
        children.append(html.Div(subtitle, style={"fontSize": "11px", "color": "#888", "marginTop": "2px"}))

    return html.Div(
        children,
        style={
            "display": "inline-block",
            "padding": "12px 16px",
            "border": "1px solid #ddd",
            "borderRadius": "8px",
            "marginRight": "12px",
            "marginBottom": "10px",
            "minWidth": "180px",
            "verticalAlign": "top",
            "backgroundColor": "#fff",
            "boxShadow": "0 1px 3px rgba(0,0,0,0.05)",
        },
    )


def _build_waterfall_figure(operating_net: float, investing_net: float, financing_net: float) -> go.Figure:
    """Build a waterfall chart for cashflow sections."""
    net_cash = operating_net + investing_net + financing_net

    # Waterfall data: measure types are 'relative' for intermediate, 'total' for final
    fig = go.Figure(go.Waterfall(
        name="Cashflow",
        orientation="v",
        measure=["relative", "relative", "relative", "total"],
        x=["Operating Net", "Investing Net", "Financing Net", "Net Cash Movement"],
        y=[operating_net, investing_net, financing_net, net_cash],
        textposition="outside",
        text=[f"{operating_net:,.0f}", f"{investing_net:,.0f}", f"{financing_net:,.0f}", f"{net_cash:,.0f}"],
        connector={"line": {"color": "#888", "width": 1}},
        increasing={"marker": {"color": "#16a34a"}},  # green
        decreasing={"marker": {"color": "#dc2626"}},  # red
        totals={"marker": {"color": "#2563eb" if net_cash >= 0 else "#dc2626"}},  # blue or red
    ))

    fig.update_layout(
        title="Cashflow Waterfall",
        showlegend=False,
        font=dict(family=FIG_FONT, size=12),
        margin=dict(t=50, b=40, l=50, r=30),
        yaxis_title="Amount",
    )

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
            title="Largest cash movements (by magnitude)",
            annotations=[dict(text="No transactions in this period", x=0.5, y=0.5, showarrow=False)],
            font=dict(family=FIG_FONT, size=12),
        )
        return fig

    # Aggregate by Category_L2 with signed sum
    agg = d.groupby("Category_L2")["Amount"].sum().reset_index()
    agg["AbsAmount"] = agg["Amount"].abs()
    agg = agg.sort_values("AbsAmount", ascending=False).head(top_n)

    # Sort for display (largest magnitude at top in horizontal bar)
    agg = agg.sort_values("AbsAmount", ascending=True)

    # Color by sign
    colors = ["#16a34a" if v >= 0 else "#dc2626" for v in agg["Amount"]]

    fig = go.Figure(go.Bar(
        x=agg["Amount"],
        y=agg["Category_L2"],
        orientation="h",
        marker_color=colors,
        text=[f"{v:+,.0f}" for v in agg["Amount"]],
        textposition="outside",
    ))

    fig.update_layout(
        title="Largest cash movements (by magnitude)",
        font=dict(family=FIG_FONT, size=12),
        margin=dict(t=50, b=40, l=150, r=50),
        xaxis_title="Amount (signed)",
        yaxis_title="",
        showlegend=False,
    )

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

def build_app(df: pd.DataFrame, contract: dict | None = None, host: str = "127.0.0.1", port: int = 8050):
    app = Dash(
        __name__,
        external_stylesheets=[INTER_STYLESHEET],
    )

    app.title = "Cashflow FP&A Dashboard"

    ym_options = sorted([x for x in df["YearMonth"].unique() if x and x != "NaT"])
    min_ym = ym_options[0] if ym_options else "UNKNOWN"
    max_ym = ym_options[-1] if ym_options else "UNKNOWN"

    section_options = ["OPERATING", "INVESTING", "FINANCING", "TRANSFER", "NON-CASH"]
    cat1_options = sorted(df["Category_L1"].unique())
    cat2_options = sorted(df["Category_L2"].unique())

    app.layout = html.Div(
        [
            dcc.Markdown(f"<style>{GLOBAL_CSS}</style>", dangerously_allow_html=True),
            html.H2("Personal Cashflow FP&A Dashboard"),

            html.Div(id="contract_banner", style={"marginBottom": "10px"}),
            dcc.Store(id="contract_store", data=contract or {}),

            html.Div(
                [
                    html.Div(
                        [
                            html.Label("YearMonth Start"),
                            dcc.Dropdown(
                                id="ym_start",
                                options=[{"label": x, "value": x} for x in ym_options],
                                value=min_ym,
                                clearable=False,
                            ),
                            html.Label("YearMonth End", style={"marginTop": "8px"}),
                            dcc.Dropdown(
                                id="ym_end",
                                options=[{"label": x, "value": x} for x in ym_options],
                                value=max_ym,
                                clearable=False,
                            ),
                        ],
                        style={"width": "18%", "display": "inline-block", "verticalAlign": "top"},
                    ),

                    html.Div(
                        [
                            html.Label("Cashflow Section"),
                            dcc.Dropdown(
                                id="section_filter",
                                options=[{"label": s, "value": s} for s in section_options],
                                value=["OPERATING", "INVESTING", "FINANCING"],
                                multi=True,
                            ),
                            dcc.Checklist(
                                id="exclude_transfers",
                                options=[{"label": "Exclude Transfers", "value": "EX"}],
                                value=["EX"],
                                style={"marginTop": "8px"},
                            ),
                            dcc.Checklist(
                                id="exclude_summary",
                                options=[{"label": "Exclude Balance B/F (Summary)", "value": "SUM"}],
                                value=["SUM"],
                                style={"marginTop": "4px"},
                            ),

                            html.Label("Cash Lens", style={"marginTop": "10px"}),
                            dcc.RadioItems(
                                id="cash_lens",
                                options=[
                                    {"label": "Net Economic (exclude transfers from net)", "value": "NET_ECONOMIC"},
                                    {"label": "Gross Movement (include transfers)", "value": "GROSS_MOVEMENT"},
                                ],
                                value="NET_ECONOMIC",
                                labelStyle={"display": "block"},
                            ),
                            html.Label("Spend Lens", style={"marginTop": "10px"}),
                            dcc.RadioItems(
                                id="spend_mode",
                                options=[
                                    {"label": "Direct Spend (exclude CC settlement)", "value": "DIRECT"},
                                    {"label": "Include CC Settlement Proxy", "value": "INCLUDE_CC_PROXY"},
                                ],
                                value="DIRECT",
                                labelStyle={"display": "block"},
                            ),
                            html.Label("Baseline Mode", style={"marginTop": "10px"}),
                            dcc.RadioItems(
                                id="baseline_mode",
                                options=[
                                    {"label": "All Transactions", "value": "ALL"},
                                    {"label": "Baseline Only (Baseline_Eligible=True)", "value": "BASELINE_ONLY"},
                                ],
                                value="ALL",
                                labelStyle={"display": "block"},
                            ),
                            dcc.Checklist(
                                id="include_non_cash",
                                options=[{"label": "Include NON-CASH section", "value": "NC"}],
                                value=[],
                                style={"marginTop": "6px"},
                            ),
                        ],
                        style={"width": "22%", "display": "inline-block", "marginLeft": "2%", "verticalAlign": "top"},
                    ),

                    html.Div(
                        [
                            html.Label("Category L1"),
                            dcc.Dropdown(
                                id="cat1_filter",
                                options=[{"label": c, "value": c} for c in cat1_options],
                                value=[],
                                multi=True,
                            ),
                            html.Label("Category L2", style={"marginTop": "8px"}),
                            dcc.Dropdown(
                                id="cat2_filter",
                                options=[{"label": c, "value": c} for c in cat2_options],
                                value=[],
                                multi=True,
                            ),
                            html.Label("Search (Description / Counterparty)", style={"marginTop": "8px"}),
                            dcc.Input(
                                id="search_text",
                                type="text",
                                placeholder="contains...",
                                style={"width": "100%"},
                            ),
                        ],
                        style={"width": "34%", "display": "inline-block", "marginLeft": "2%", "verticalAlign": "top"},
                    ),

                    html.Div(
                        [
                            html.Label("Drilldown"),
                            dcc.RadioItems(
                                id="drill_mode",
                                options=[
                                    {"label": "Category_L2", "value": "Category_L2"},
                                    {"label": "Counterparty_Core", "value": "Counterparty_Core"},
                                    {"label": "Instrument", "value": "Instrument"},
                                ],
                                value="Category_L2",
                                labelStyle={"display": "block"},
                            ),
                            html.Label("Top N", style={"marginTop": "8px"}),
                            dcc.Slider(id="topn", min=5, max=50, step=5, value=15),
                        ],
                        style={"width": "18%", "display": "inline-block", "marginLeft": "2%", "verticalAlign": "top"},
                    ),
                ],
                style={"padding": "8px 0"},
            ),

            dcc.Store(id="df_store"),

            # ===== LANDING SECTION (Above the Fold) =====
            html.Div(
                [
                    html.H3("Cash Movement Summary", style={"marginBottom": "12px", "marginTop": "16px"}),
                    html.Div(id="kpi_tiles"),
                ],
                style={"marginBottom": "16px"},
            ),

            # Waterfall + Drivers side by side
            html.Div(
                [
                    html.Div([dcc.Graph(id="waterfall_chart", style={"height": "360px"})], style={"width": "54%", "display": "inline-block", "verticalAlign": "top"}),
                    html.Div([dcc.Graph(id="drivers_chart", style={"height": "360px"})], style={"width": "44%", "display": "inline-block", "marginLeft": "2%", "verticalAlign": "top"}),
                ],
                style={"marginBottom": "20px"},
            ),

            html.Hr(style={"margin": "24px 0", "borderColor": "#ddd"}),

            # ===== DETAILED ANALYSIS (Below the Fold) =====
            html.H3("Detailed Analysis", style={"marginBottom": "12px"}),

            html.Div(
                [
                    dcc.Graph(id="net_cashflow_line", style={"height": "320px"}),
                ]
            ),

            html.Div(
                [
                    html.Div([dcc.Graph(id="income_stack")], style={"width": "49%", "display": "inline-block"}),
                    html.Div([dcc.Graph(id="spend_stack")], style={"width": "49%", "display": "inline-block", "marginLeft": "2%"}),
                ],
                style={"marginTop": "10px"},
            ),

            html.Div(
                [
                    html.Div([dcc.Graph(id="drill_bar")], style={"width": "49%", "display": "inline-block"}),
                    html.Div([dcc.Graph(id="recurring_bar")], style={"width": "49%", "display": "inline-block", "marginLeft": "2%"}),
                ],
                style={"marginTop": "10px"},
            ),

            html.Hr(style={"margin": "24px 0", "borderColor": "#ddd"}),

            # ===== AUDIT SECTION (Below the Fold) =====
            html.H3("Audit Trail"),
            dash_table.DataTable(
                id="tx_table",
                page_size=25,
                sort_action="native",
                filter_action="native",
                style_table={"overflowX": "auto"},
                style_cell={"fontFamily": FONT_STACK, "fontSize": "12px", "padding": "6px"},
                style_header={"fontWeight": "600"},
            ),
        ],
        style={
            "fontFamily": "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif",
            "backgroundColor": "#f7f7f7",
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
    )
    def refresh_views(djson, drill_mode, topn, cash_lens, spend_mode):
        d = pd.read_json(StringIO(djson), orient="split") if djson else df.copy()

        monthly_source = d.copy()

        # Cash lens determines whether transfers are included in net calculations/line
        if cash_lens == "NET_ECONOMIC" and "Is_TransferSection" in monthly_source.columns:
            monthly_source = monthly_source[~monthly_source["Is_TransferSection"]].copy()

        monthly = compute_monthly_kpis(monthly_source)

        # ===== LANDING KPIs: 4 tiles per spec =====
        # Filter to cash movement sections (exclude TRANSFER, NON-CASH)
        cash_movement_df = monthly_source[monthly_source["Cashflow_Section"].isin(["OPERATING", "INVESTING", "FINANCING"])].copy()

        operating_net = float(cash_movement_df[cash_movement_df["Cashflow_Section"] == "OPERATING"]["Amount"].sum())
        investing_net = float(cash_movement_df[cash_movement_df["Cashflow_Section"] == "INVESTING"]["Amount"].sum())
        financing_net = float(cash_movement_df[cash_movement_df["Cashflow_Section"] == "FINANCING"]["Amount"].sum())
        net_cash_movement = operating_net + investing_net + financing_net

        # Determine if empty period
        is_empty = cash_movement_df.empty

        tiles = [
            _kpi_tile("Net Cash Movement", net_cash_movement, subtitle="(No transactions)" if is_empty else "", color_by_sign=True),
            _kpi_tile("Operating Cash", operating_net, subtitle="Income minus operating expenses", color_by_sign=True),
            _kpi_tile("Investing Cash", investing_net, subtitle="", color_by_sign=True),
            _kpi_tile("Financing Cash", financing_net, subtitle="Includes CC settlements", color_by_sign=True),
        ]

        # ===== WATERFALL CHART =====
        fig_waterfall = _build_waterfall_figure(operating_net, investing_net, financing_net)

        # ===== DRIVERS CHART (Top 5 Category_L2 by magnitude) =====
        fig_drivers = _build_drivers_figure(cash_movement_df, top_n=5)

        # ===== DETAILED ANALYSIS CHARTS (Below the fold) =====
        total_income = float(monthly_source[(monthly_source["Cashflow_Section"] == "OPERATING") & (monthly_source["Amount"] > 0)]["Amount"].sum())

        # Spend lens: optionally include CC settlement proxy outflows
        sp_base = monthly_source[(monthly_source["Cashflow_Section"] == "OPERATING") & (monthly_source["Amount"] < 0)].copy()
        if spend_mode == "DIRECT" and "Is_CC_Settlement" in monthly_source.columns:
            sp_base = sp_base[~monthly_source.loc[sp_base.index, "Is_CC_Settlement"].fillna(False)]
        elif spend_mode == "INCLUDE_CC_PROXY" and "Is_CC_Settlement" in monthly_source.columns:
            cc = monthly_source[(monthly_source["Is_CC_Settlement"].fillna(False)) & (monthly_source["Amount"] < 0)].copy()
            sp_base = pd.concat([sp_base, cc], ignore_index=False)

        fig_net = px.line(monthly, x="YearMonth", y="Net_Cashflow", markers=True, title=f"Net Cashflow by Month (signed) [{cash_lens}]")
        fig_net.update_layout(font=dict(family=FIG_FONT, size=12))

        inc = monthly_source[(monthly_source["Cashflow_Section"] == "OPERATING") & (monthly_source["Amount"] > 0)]
        if inc.empty:
            fig_inc = px.bar(title="Operating Income (no data)")
        else:
            inc_g = inc.groupby(["YearMonth", "Category_L2"])["Amount"].sum().reset_index()
            fig_inc = px.bar(inc_g, x="YearMonth", y="Amount", color="Category_L2", title="Operating Income by Category_L2")
        fig_inc.update_layout(font=dict(family=FIG_FONT, size=12))

        sp = sp_base.copy()
        if sp.empty:
            fig_sp = px.bar(title="Operating Spend (no data)")
        else:
            sp_g = sp.groupby(["YearMonth", "Category_L2"])["AbsAmount"].sum().reset_index()
            fig_sp = px.bar(sp_g, x="YearMonth", y="AbsAmount", color="Category_L2", title=f"Operating Spend by Category_L2 (abs) [{spend_mode}]")
        fig_sp.update_layout(font=dict(family=FIG_FONT, size=12))

        drill_col = drill_mode if drill_mode in d.columns else "Category_L2"
        g = d.groupby(drill_col)["AbsAmount"].sum().reset_index().sort_values("AbsAmount", ascending=False).head(int(topn))
        fig_drill = px.bar(g, x="AbsAmount", y=drill_col, orientation="h", title=f"Top {topn} by Abs Amount ({drill_col})")
        fig_drill.update_layout(font=dict(family=FIG_FONT, size=12))

        rec = recurring_candidates(d, min_months=6).head(25).copy()
        if rec.empty:
            fig_rec = px.bar(title="Recurring candidates (no data)")
        else:
            rec["label"] = (
                rec["Category_L2"].astype(str)
                + " | m=" + rec["months_present"].astype(int).astype(str)
                + " | cov=" + rec["cov"].fillna(np.nan).round(2).astype(str)
            )
            fig_rec = px.bar(rec, x="avg_abs", y="label", orientation="h", title="Recurring candidates (avg abs outflow)")
        fig_rec.update_layout(font=dict(family=FIG_FONT, size=12))

        return tiles, fig_waterfall, fig_drivers, fig_net, fig_inc, fig_sp, fig_drill, fig_rec

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

    app = build_app(df, contract=contract, host=host, port=port)
    app.run(debug=False, host=host, port=port)


if __name__ == "__main__":
    main()
