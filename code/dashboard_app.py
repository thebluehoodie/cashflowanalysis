#!/usr/bin/env python3
"""
dashboard_app.py  (Production)

Dash dashboard for personal FP&A cashflow analytics.

Design goals
- Robust to schema drift:
  - Accepts classifier output (v3) columns OR older dashboard columns.
  - Harmonizes into: Cashflow_Section, Category_L1, Category_L2, Instrument, Counterparty_Core.
- Avoids brittle "required columns" failures (only Amount is strictly required).
- Uses Dash v2+ import patterns (dash_table via `from dash import dash_table`).
- Keeps transformations explicit + auditable.

Env
- ANALYSIS_INPUT_CSV (required): path to classified_transactions CSV
- DASH_HOST (optional): default 127.0.0.1
- DASH_PORT (optional): default 8050
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from dash import Dash, dcc, html, Input, Output, State, dash_table
import plotly.express as px


# ======================================================
# ENV + IO
# ======================================================

def load_settings() -> tuple[Path, str, int]:
    load_dotenv()
    input_csv = os.getenv("ANALYSIS_INPUT_CSV", "").strip()
    if not input_csv:
        raise ValueError("ANALYSIS_INPUT_CSV not set in .env")

    host = os.getenv("DASH_HOST", "127.0.0.1").strip()
    port_raw = os.getenv("DASH_PORT", "8050").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 8050

    return Path(input_csv), host, port


# ======================================================
# SCHEMA HARMONIZATION
# ======================================================

_CANONICAL_DEFAULTS = {
    "Cashflow_Section": "OPERATING",
    "Category_L1": "UNCLASSIFIED",
    "Category_L2": "UNCLASSIFIED",
    "Instrument": "OTHER",
    "Flow_Nature": "UNKNOWN",
    "Record_Type": "TRANSACTION",
    "Counterparty_Core": "",
    "Counterparty_Norm": "",
    "Description": "",
}

# Prefer these mappings if present
_SCHEMA_ALIASES = [
    # Classifier v3 → dashboard canonical
    ("Cashflow_Statement", "Cashflow_Section"),
    ("Economic_Purpose_L1", "Category_L1"),
    ("Economic_Purpose_L2", "Category_L2"),
    ("Bank_Rail", "Instrument"),
    # Older / alternate naming
    ("Cashflow_Section", "Cashflow_Section"),
    ("Category_L1", "Category_L1"),
    ("Category_L2", "Category_L2"),
    ("Instrument", "Instrument"),
]

def _to_yearmonth(series: pd.Series) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce", dayfirst=True)
    return dt.dt.to_period("M").astype(str)

def harmonize_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    Produce a dashboard-friendly dataframe with canonical columns.
    Hard requirement: Amount must exist.
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
            # last resort: put everything into a single bucket
            df["YearMonth"] = "UNKNOWN"
    df["YearMonth"] = df["YearMonth"].astype(str)

    # Ensure Description exists
    if "Description" not in df.columns:
        df["Description"] = ""

    # Create canonical columns; fill from best available sources
    for canonical, default in _CANONICAL_DEFAULTS.items():
        if canonical not in df.columns:
            df[canonical] = default

    # Apply alias mapping (only if source exists and target missing/empty)
    for src, tgt in _SCHEMA_ALIASES:
        if src in df.columns and tgt in df.columns:
            tgt_blank = df[tgt].astype(str).str.strip().eq("") | df[tgt].isna()
            # If target is all blank/NA, overwrite entirely; else fill blanks only.
            if tgt_blank.all():
                df[tgt] = df[src]
            else:
                df.loc[tgt_blank, tgt] = df.loc[tgt_blank, src]

    # Counterparty normalization
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

    # Flags
    df["Is_Summary"] = df["Record_Type"].eq("SUMMARY") | df["Category_L2"].eq("BALANCE_BF")
    df["Is_TransferSection"] = df["Cashflow_Section"].eq("TRANSFER")
    df["Is_Inflow"] = df["Amount"] > 0
    df["Is_Outflow"] = df["Amount"] < 0
    df["AbsAmount"] = df["Amount"].abs()

    return df


# ======================================================
# METRICS
# ======================================================

def compute_monthly_kpis(df: pd.DataFrame) -> pd.DataFrame:
    """
    Assumes df already filtered for desired inclusion/exclusion.
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
            "Investing_Net": inv,
            "Financing_Net": fin,
            "Net_Cashflow": net,
        }
    ).fillna(0.0).reset_index()

    out["Operating_Spend_Abs"] = out["Operating_Spend"].abs()
    out["Operating_Net"] = out["Operating_Income"] + out["Operating_Spend"]
    return out.sort_values("YearMonth")


def recurring_candidates(df: pd.DataFrame, min_months: int = 6) -> pd.DataFrame:
    """
    Heuristic recurring detector for outflows:
    - Focus on Operating outflows by default
    - Group by Category_L2 and compute months_present, avg_abs, cov
    """
    d = df.copy()
    d = d[(d["Amount"] < 0) & (d["Cashflow_Section"].isin(["OPERATING", "FINANCING", "INVESTING"]))]

    g = d.groupby("Category_L2")
    months_present = g["YearMonth"].nunique()
    avg_abs = g["AbsAmount"].mean()
    std_abs = g["AbsAmount"].std(ddof=0)
    med_abs = g["AbsAmount"].median()

    out = pd.DataFrame(
        {
            "months_present": months_present,
            "avg_abs": avg_abs,
            "median_abs": med_abs,
            "std_abs": std_abs,
        }
    ).fillna(0.0)
    out["cov"] = np.where(out["avg_abs"] > 0, out["std_abs"] / out["avg_abs"], np.nan)
    out = out.reset_index()

    out["is_recurring_candidate"] = (out["months_present"] >= min_months) & (out["cov"].fillna(999) <= 1.0)
    return out.sort_values(["is_recurring_candidate", "months_present", "avg_abs"], ascending=[False, False, False])


# ======================================================
# DASH APP
# ======================================================

def _kpi_tile(title: str, value: float) -> html.Div:
    return html.Div(
        [
            html.Div(title, style={"fontSize": "12px", "color": "#555"}),
            html.Div(f"{value:,.2f}", style={"fontSize": "20px", "fontWeight": "bold"}),
        ],
        style={
            "border": "1px solid #ddd",
            "borderRadius": "10px",
            "padding": "10px 12px",
            "minWidth": "210px",
            "backgroundColor": "white",
        },
    )

def build_app(df: pd.DataFrame) -> Dash:
    app = Dash(__name__)
    app.title = "Cashflow FP&A Dashboard"

    ym_options = sorted([x for x in df["YearMonth"].unique() if x and x != "NaT"])
    min_ym = ym_options[0] if ym_options else "UNKNOWN"
    max_ym = ym_options[-1] if ym_options else "UNKNOWN"

    section_options = ["OPERATING", "INVESTING", "FINANCING", "TRANSFER"]
    cat1_options = sorted(df["Category_L1"].unique())
    cat2_options = sorted(df["Category_L2"].unique())

    app.layout = html.Div(
        [
            html.H2("Personal Cashflow FP&A Dashboard"),

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
                ]
            ),

            html.Hr(),

            html.Div(id="kpi_tiles", style={"display": "flex", "gap": "12px", "flexWrap": "wrap"}),

            html.Hr(),

            dcc.Graph(id="net_cashflow_line"),

            html.Div(
                [
                    html.Div(dcc.Graph(id="income_stack"), style={"width": "49%", "display": "inline-block"}),
                    html.Div(dcc.Graph(id="spend_stack"), style={"width": "49%", "display": "inline-block", "marginLeft": "2%"}),
                ]
            ),

            html.Div(
                [
                    html.Div(dcc.Graph(id="drill_bar"), style={"width": "49%", "display": "inline-block"}),
                    html.Div(dcc.Graph(id="recurring_bar"), style={"width": "49%", "display": "inline-block", "marginLeft": "2%"}),
                ]
            ),

            html.Hr(),
            html.H4("Transaction Explorer"),
            dcc.Input(
                id="search_text",
                type="text",
                placeholder="Search Description / Counterparty_Core",
                style={"width": "60%"},
            ),
            html.Button("Apply Search", id="search_btn", style={"marginLeft": "8px"}),

            html.Div(style={"height": "10px"}),

            dash_table.DataTable(
                id="tx_table",
                columns=[],
                data=[],
                page_size=25,
                sort_action="native",
                filter_action="native",
                row_selectable="multi",
                style_table={"overflowX": "auto"},
                style_cell={
                    "fontFamily": "Arial",
                    "fontSize": "12px",
                    "padding": "6px",
                    "whiteSpace": "normal",
                    "height": "auto",
                },
                style_header={"fontWeight": "bold"},
            ),

            dcc.Store(id="df_store"),
        ],
        style={"fontFamily": "Arial", "margin": "16px", "backgroundColor": "#fafafa"},
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
    )
    def filter_df(ym_start, ym_end, sections, cat1, cat2, ex_transfers, ex_summary):
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

        return d.to_json(date_format="iso", orient="split")

    # ---------- charts + KPIs ----------
    @app.callback(
        Output("kpi_tiles", "children"),
        Output("net_cashflow_line", "figure"),
        Output("income_stack", "figure"),
        Output("spend_stack", "figure"),
        Output("drill_bar", "figure"),
        Output("recurring_bar", "figure"),
        Input("df_store", "data"),
        Input("drill_mode", "value"),
        Input("topn", "value"),
    )
    def refresh_views(djson, drill_mode, topn):
        d = pd.read_json(djson, orient="split") if djson else df.copy()

        monthly = compute_monthly_kpis(d)

        total_income = float(d[(d["Cashflow_Section"] == "OPERATING") & (d["Amount"] > 0)]["Amount"].sum())
        total_spend = float(d[(d["Cashflow_Section"] == "OPERATING") & (d["Amount"] < 0)]["Amount"].sum())  # negative
        net_operating = total_income + total_spend
        inv = float(d[d["Cashflow_Section"] == "INVESTING"]["Amount"].sum())
        fin = float(d[d["Cashflow_Section"] == "FINANCING"]["Amount"].sum())
        net = float(d["Amount"].sum())

        tiles = [
            _kpi_tile("Operating Income", total_income),
            _kpi_tile("Operating Spend (abs)", abs(total_spend)),
            _kpi_tile("Operating Net", net_operating),
            _kpi_tile("Investing Net (signed)", inv),
            _kpi_tile("Financing Net (signed)", fin),
            _kpi_tile("Net Cashflow (signed)", net),
        ]

        fig_net = px.line(monthly, x="YearMonth", y="Net_Cashflow", markers=True, title="Net Cashflow by Month (signed)")

        inc = d[(d["Cashflow_Section"] == "OPERATING") & (d["Amount"] > 0)]
        if inc.empty:
            fig_inc = px.bar(title="Operating Income (no data)")
        else:
            inc_g = inc.groupby(["YearMonth", "Category_L2"])["Amount"].sum().reset_index()
            fig_inc = px.bar(inc_g, x="YearMonth", y="Amount", color="Category_L2", title="Operating Income by Category_L2")

        sp = d[(d["Cashflow_Section"] == "OPERATING") & (d["Amount"] < 0)]
        if sp.empty:
            fig_sp = px.bar(title="Operating Spend (no data)")
        else:
            sp_g = sp.groupby(["YearMonth", "Category_L2"])["AbsAmount"].sum().reset_index()
            fig_sp = px.bar(sp_g, x="YearMonth", y="AbsAmount", color="Category_L2", title="Operating Spend by Category_L2 (abs)")

        drill_col = drill_mode if drill_mode in d.columns else "Category_L2"
        g = d.groupby(drill_col)["AbsAmount"].sum().reset_index().sort_values("AbsAmount", ascending=False).head(int(topn))
        fig_drill = px.bar(g, x="AbsAmount", y=drill_col, orientation="h", title=f"Top {topn} by Abs Amount ({drill_col})")

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

        return tiles, fig_net, fig_inc, fig_sp, fig_drill, fig_rec

    # ---------- transaction table ----------
    @app.callback(
        Output("tx_table", "columns"),
        Output("tx_table", "data"),
        Input("search_btn", "n_clicks"),
        State("search_text", "value"),
        State("df_store", "data"),
    )
    def render_table(_n, q, djson):
        d = pd.read_json(djson, orient="split") if djson else df.copy()

        if q:
            qu = str(q).upper().strip()
            if qu:
                mask = (
                    d["Description"].astype(str).str.upper().str.contains(qu, na=False)
                    | d["Counterparty_Core"].astype(str).str.upper().str.contains(qu, na=False)
                )
                d = d[mask]

        # Keep responsive: show last 500 (user can filter/sort in-table)
        d = d.sort_values(["YearMonth"], ascending=False).head(500).copy()

        preferred_cols = [
            "YearMonth", "Date", "Amount",
            "Cashflow_Section", "Category_L1", "Category_L2",
            "Instrument", "Counterparty_Core", "Description",
            "Rule_ID", "Was_Overridden", "Override_Reason",
        ]
        cols = [c for c in preferred_cols if c in d.columns]

        columns = [{"name": c, "id": c} for c in cols]
        data = d[cols].to_dict("records")
        return columns, data

    return app


def main():
    input_csv, host, port = load_settings()
    df_raw = pd.read_csv(input_csv)
    df = harmonize_schema(df_raw)

    app = build_app(df)
    app.run(debug=False, host=host, port=port)


if __name__ == "__main__":
    main()
