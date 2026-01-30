#!/usr/bin/env python3
"""
classification_diagnostics.py

FP&A-grade diagnostic tool for transaction classification analysis.

Generates evidence artifacts to explain "odd" dashboard numbers, focusing on
fallback rule pressure from R14_OTHER_INCOME and R15_GENERIC_OUTFLOW.

Outputs:
- rule_impact_summary.csv
- fallback_pressure_report.csv
- category_anomaly_report.csv
- override_masking_report.csv

Usage:
    python classification_diagnostics.py \\
        --input <classified_transactions_v3.csv> \\
        --output-dir <diagnostics/> \\
        [--overrides <overrides.xlsx>] \\
        [--include-transfers] \\
        [--include-non-cash]
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd


# ======================================================
# CONFIGURATION BLOCK
# ======================================================

THRESHOLDS = {
    "R14_WARNING_PCT": 0.15,    # 15% of inflows
    "R14_CRITICAL_PCT": 0.25,   # 25% of inflows
    "R15_WARNING_PCT": 0.30,    # 30% of outflows
    "R15_CRITICAL_PCT": 0.50,   # 50% of outflows
}

REQUIRED_COLUMNS = [
    "Txn_ID",
    "YearMonth",
    "Description",
    "Amount",
    "Record_Type",
    "Cashflow_Section",
    "Rule_ID",
    "Bank_Rail",
]

# Regex hints for suggested categorization (HINT ONLY - does not modify data)
CATEGORY_HINTS: Dict[str, str] = {
    # Utilities
    r"\bSINGTEL\b|\bSTARHUB\b|\bM1\b": "UTILITIES/TELECOM",
    r"\bSP\s+SERVICES\b|\bSP\s+GROUP\b": "UTILITIES/ELECTRIC_GAS",
    r"\bPUB\b": "UTILITIES/WATER",
    # Housing
    r"\bTOWNCOUNCIL\b|\bTCSC\b": "HOUSING/TOWN_COUNCIL_FEES",
    r"\bMCST\b": "HOUSING/HOA_CONDO_FEES",
    # Transport
    r"\bTRANSITLIN\b|\bEZLINK\b": "TRANSPORT/PUBLIC_TRANSIT",
    r"\bGRAB\b|\bGOJEK\b": "TRANSPORT/RIDESHARE",
    r"\bCOMFORT\b|\bCDG\b": "TRANSPORT/TAXI",
    # Subscriptions
    r"\bNETFLIX\b|\bSPOTIFY\b|\bDISNEY\b": "SUBSCRIPTIONS/ENTERTAINMENT",
    # Income hints
    r"\bREFUND\b": "INCOME/REFUND",
    r"\bCASHBACK\b": "INCOME/CASHBACK",
    r"\bDIVIDEND\b": "INCOME/DIVIDEND",
    # Possible transfers (need review)
    r"\bFUNDS\s+TRANSFER\b.*\d{10}": "POSSIBLE_TRANSFER",
    r"\bFAST\b.*\d{8}": "POSSIBLE_TRANSFER",
}


# ======================================================
# HELPERS
# ======================================================

def normalize_description(desc: str) -> str:
    """
    Normalize description for grouping.
    - Uppercase
    - Collapse whitespace
    - Truncate to 80 chars
    """
    if pd.isna(desc):
        return ""
    s = str(desc).upper().strip()
    s = " ".join(s.split())  # Collapse whitespace
    return s[:80]


def suggest_category(desc_norm: str) -> str:
    """
    Suggest category based on regex hints.
    Returns hint string or empty string if no match.
    This is HINT ONLY - does not modify classification.
    """
    for pattern, category in CATEGORY_HINTS.items():
        if re.search(pattern, desc_norm, re.IGNORECASE):
            return category
    return ""


def calculate_months_span(first_ym: str, last_ym: str) -> int:
    """
    Calculate months between two YearMonth strings (YYYY-MM).
    Returns inclusive count (same month = 1).
    """
    if not first_ym or not last_ym:
        return 1
    try:
        first_parts = first_ym.split("-")
        last_parts = last_ym.split("-")
        first_year, first_month = int(first_parts[0]), int(first_parts[1])
        last_year, last_month = int(last_parts[0]), int(last_parts[1])
        return (last_year - first_year) * 12 + (last_month - first_month) + 1
    except (ValueError, IndexError):
        return 1


def detect_recurrence_pattern(count: int, unique_months: int, months_span: int) -> str:
    """
    Classify recurrence pattern based on transaction frequency and coverage.
    - ONE_OFF: single occurrence
    - RECURRING: 3+ occurrences with 70%+ month coverage
    - SPORADIC: 2+ occurrences with 30%+ month coverage
    """
    if count == 1:
        return "ONE_OFF"
    coverage = unique_months / max(1, months_span)
    if count >= 3 and coverage >= 0.7:
        return "RECURRING"
    if coverage >= 0.3:
        return "SPORADIC"
    return "ONE_OFF"


def get_severity(pct: float, warning_threshold: float, critical_threshold: float) -> str:
    """Determine severity level based on percentage thresholds."""
    if pct >= critical_threshold:
        return "CRITICAL"
    elif pct >= warning_threshold:
        return "WARNING"
    return "OK"


# ======================================================
# COLUMN NORMALIZATION
# ======================================================

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Create canonical columns from aliases if missing.
    This is explicit and happens at load time.
    """
    df = df.copy()

    # Cashflow_Section from Cashflow_Statement
    if "Cashflow_Section" not in df.columns and "Cashflow_Statement" in df.columns:
        df["Cashflow_Section"] = df["Cashflow_Statement"]

    # Category_L1/L2 from Economic_Purpose_L1/L2
    if "Category_L1" not in df.columns and "Economic_Purpose_L1" in df.columns:
        df["Category_L1"] = df["Economic_Purpose_L1"]
    if "Category_L2" not in df.columns and "Economic_Purpose_L2" in df.columns:
        df["Category_L2"] = df["Economic_Purpose_L2"]

    # Bank_Rail from Instrument
    if "Bank_Rail" not in df.columns and "Instrument" in df.columns:
        df["Bank_Rail"] = df["Instrument"]

    return df


def validate_required_columns(df: pd.DataFrame) -> None:
    """
    Fail fast with rich error message if required columns are missing.
    Must be called AFTER normalize_columns().
    """
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        available = sorted(df.columns.tolist())
        raise ValueError(
            f"Missing required columns for diagnostics: {missing}\n"
            f"Available columns: {available}\n"
            f"Hint: Ensure input is classified_transactions_v3.csv or compatible schema."
        )


# ======================================================
# BASE FILTER
# ======================================================

def apply_base_filter(
    df: pd.DataFrame,
    include_transfers: bool = False,
    include_non_cash: bool = False,
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """
    Apply FP&A semantic base filter.
    Uses Cashflow_Section + Record_Type, NOT Flow_Nature.

    Returns:
        Tuple of (filtered DataFrame, exclusion counts dict)
    """
    df = df.copy()
    original_count = len(df)

    # Track exclusions
    exclusions = {
        "summary": 0,
        "balance_bf": 0,
        "non_cash": 0,
        "transfer": 0,
    }

    # 1. Exclude SUMMARY rows
    summary_mask = df["Record_Type"] == "SUMMARY"
    exclusions["summary"] = summary_mask.sum()

    # 2. Exclude BALANCE_BF (edge case)
    balance_bf_mask = pd.Series(False, index=df.index)
    if "Category_L2" in df.columns:
        balance_bf_mask = df["Category_L2"] == "BALANCE_BF"
        # Don't double-count rows already marked as SUMMARY
        exclusions["balance_bf"] = (balance_bf_mask & ~summary_mask).sum()

    # 3. NON-CASH exclusion
    non_cash_mask = df["Cashflow_Section"] == "NON-CASH"
    if not include_non_cash:
        exclusions["non_cash"] = (non_cash_mask & ~summary_mask & ~balance_bf_mask).sum()

    # 4. TRANSFER exclusion
    transfer_mask = df["Cashflow_Section"] == "TRANSFER"
    if not include_transfers:
        exclusions["transfer"] = (transfer_mask & ~summary_mask & ~balance_bf_mask & ~non_cash_mask).sum()

    # Build final filter mask
    keep_mask = ~summary_mask & ~balance_bf_mask

    if not include_non_cash:
        keep_mask &= ~non_cash_mask

    if not include_transfers:
        keep_mask &= ~transfer_mask

    return df[keep_mask].copy(), exclusions


# ======================================================
# REPORT GENERATORS
# ======================================================

def generate_rule_impact_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    Generate rule impact summary grouped by Rule_ID.
    Computed on filtered dataset.
    """
    if len(df) == 0:
        return pd.DataFrame(columns=[
            "Rule_ID", "Txn_Count", "Txn_Pct", "Inflow_Total", "Outflow_Total",
            "Net_Impact", "Inflow_Pct", "Outflow_Pct", "Abs_Impact_Rank"
        ])

    total_txn = len(df)
    total_inflow = df[df["Amount"] > 0]["Amount"].sum()
    total_outflow = df[df["Amount"] < 0]["Amount"].abs().sum()

    # Group by Rule_ID
    grouped = df.groupby("Rule_ID").agg(
        Txn_Count=("Txn_ID", "count"),
        Inflow_Total=("Amount", lambda x: x[x > 0].sum()),
        Outflow_Total=("Amount", lambda x: x[x < 0].abs().sum()),
    ).reset_index()

    # Calculate percentages
    grouped["Txn_Pct"] = (grouped["Txn_Count"] / total_txn * 100).round(2)
    grouped["Net_Impact"] = grouped["Inflow_Total"] - grouped["Outflow_Total"]
    grouped["Inflow_Pct"] = (
        (grouped["Inflow_Total"] / total_inflow * 100).round(2)
        if total_inflow > 0 else 0.0
    )
    grouped["Outflow_Pct"] = (
        (grouped["Outflow_Total"] / total_outflow * 100).round(2)
        if total_outflow > 0 else 0.0
    )

    # Rank by absolute impact (tie-breaker: Rule_ID alphabetically)
    grouped = grouped.sort_values(
        ["Net_Impact", "Rule_ID"],
        key=lambda x: x.abs() if x.name == "Net_Impact" else x,
        ascending=[False, True]
    ).reset_index(drop=True)
    grouped["Abs_Impact_Rank"] = range(1, len(grouped) + 1)

    # Reorder columns
    return grouped[[
        "Rule_ID", "Txn_Count", "Txn_Pct", "Inflow_Total", "Outflow_Total",
        "Net_Impact", "Inflow_Pct", "Outflow_Pct", "Abs_Impact_Rank"
    ]]


def generate_fallback_pressure_report(df: pd.DataFrame) -> pd.DataFrame:
    """
    Analyze R14 (inflow fallback) and R15 (outflow fallback) pressure.
    """
    total_inflow = df[df["Amount"] > 0]["Amount"].sum()
    total_outflow = df[df["Amount"] < 0]["Amount"].abs().sum()

    results = []

    # R14_OTHER_INCOME (inflows only)
    r14_df = df[(df["Rule_ID"] == "R14_OTHER_INCOME") & (df["Amount"] > 0)].copy()
    r14_dollar = r14_df["Amount"].sum() if len(r14_df) > 0 else 0
    r14_pct = (r14_dollar / total_inflow) if total_inflow > 0 else 0

    # Get top descriptions for R14
    r14_top_descs = _get_top_descriptions(r14_df, 5)

    results.append({
        "Rule_ID": "R14_OTHER_INCOME",
        "Direction": "inflow",
        "Txn_Count": len(r14_df),
        "Dollar_Value": round(r14_dollar, 2),
        "Pct_of_Direction": round(r14_pct * 100, 2),
        "Severity": get_severity(r14_pct, THRESHOLDS["R14_WARNING_PCT"], THRESHOLDS["R14_CRITICAL_PCT"]),
        "Threshold_Warning": THRESHOLDS["R14_WARNING_PCT"] * 100,
        "Threshold_Critical": THRESHOLDS["R14_CRITICAL_PCT"] * 100,
        **r14_top_descs,
        "Top_Concentration_Pct": _get_top_concentration_pct(r14_df),
    })

    # R15_GENERIC_OUTFLOW (outflows only)
    r15_df = df[(df["Rule_ID"] == "R15_GENERIC_OUTFLOW") & (df["Amount"] < 0)].copy()
    r15_dollar = r15_df["Amount"].abs().sum() if len(r15_df) > 0 else 0
    r15_pct = (r15_dollar / total_outflow) if total_outflow > 0 else 0

    # Get top descriptions for R15
    r15_top_descs = _get_top_descriptions(r15_df, 5)

    results.append({
        "Rule_ID": "R15_GENERIC_OUTFLOW",
        "Direction": "outflow",
        "Txn_Count": len(r15_df),
        "Dollar_Value": round(r15_dollar, 2),
        "Pct_of_Direction": round(r15_pct * 100, 2),
        "Severity": get_severity(r15_pct, THRESHOLDS["R15_WARNING_PCT"], THRESHOLDS["R15_CRITICAL_PCT"]),
        "Threshold_Warning": THRESHOLDS["R15_WARNING_PCT"] * 100,
        "Threshold_Critical": THRESHOLDS["R15_CRITICAL_PCT"] * 100,
        **r15_top_descs,
        "Top_Concentration_Pct": _get_top_concentration_pct(r15_df),
    })

    return pd.DataFrame(results)


def _get_top_descriptions(df: pd.DataFrame, n: int) -> Dict[str, str]:
    """
    Get top N descriptions by count with deterministic sorting.
    Returns dict with Top_Description_1..N, counts, and dollars.
    """
    result = {}

    if len(df) == 0:
        for i in range(1, n + 1):
            result[f"Top_Description_{i}"] = ""
            result[f"Top_Description_{i}_Count"] = 0
            result[f"Top_Description_{i}_Dollars"] = 0.0
        return result

    df = df.copy()
    df["Description_Norm"] = df["Description"].apply(normalize_description)

    # Group by normalized description
    grouped = df.groupby("Description_Norm").agg(
        Count=("Txn_ID", "count"),
        Dollars=("Amount", lambda x: x.abs().sum()),
    ).reset_index()

    # Sort deterministically: by count desc, then by Description_Norm asc
    grouped = grouped.sort_values(
        ["Count", "Description_Norm"],
        ascending=[False, True]
    ).head(n).reset_index(drop=True)

    for i in range(n):
        idx = i + 1
        if i < len(grouped):
            result[f"Top_Description_{idx}"] = grouped.iloc[i]["Description_Norm"]
            result[f"Top_Description_{idx}_Count"] = int(grouped.iloc[i]["Count"])
            result[f"Top_Description_{idx}_Dollars"] = round(grouped.iloc[i]["Dollars"], 2)
        else:
            result[f"Top_Description_{idx}"] = ""
            result[f"Top_Description_{idx}_Count"] = 0
            result[f"Top_Description_{idx}_Dollars"] = 0.0

    return result


def _get_top_concentration_pct(df: pd.DataFrame) -> float:
    """Get percentage of fallback count from top description."""
    if len(df) == 0:
        return 0.0

    df = df.copy()
    df["Description_Norm"] = df["Description"].apply(normalize_description)
    counts = df["Description_Norm"].value_counts()

    if len(counts) == 0:
        return 0.0

    top_count = counts.iloc[0]
    return round(top_count / len(df) * 100, 2)


def generate_category_anomaly_report(df: pd.DataFrame) -> pd.DataFrame:
    """
    Generate drilldown on OTHER_INCOME and DISCRETIONARY patterns.
    UNION of R14 inflows and R15 outflows.
    """
    # R14_OTHER_INCOME patterns (inflows)
    r14_df = df[(df["Rule_ID"] == "R14_OTHER_INCOME") & (df["Amount"] > 0)].copy()
    r14_df["Anomaly_Type"] = "OTHER_INCOME"

    # R15_GENERIC_OUTFLOW patterns (outflows)
    r15_df = df[(df["Rule_ID"] == "R15_GENERIC_OUTFLOW") & (df["Amount"] < 0)].copy()
    r15_df["Anomaly_Type"] = "DISCRETIONARY"

    # Combine
    combined = pd.concat([r14_df, r15_df], ignore_index=True)

    if len(combined) == 0:
        return pd.DataFrame(columns=[
            "Anomaly_Type", "Description_Norm", "Counterparty_Core", "Rule_ID",
            "Txn_Count", "Total_Amount", "Avg_Amount", "First_YearMonth",
            "Last_YearMonth", "Months_Span", "Unique_Months", "Recurrence_Pattern",
            "Bank_Rail_Breakdown", "Suggested_Category"
        ])

    combined["Description_Norm"] = combined["Description"].apply(normalize_description)

    # Get Counterparty_Core if available
    if "Counterparty_Core" in combined.columns:
        combined["Counterparty_Core_Clean"] = combined["Counterparty_Core"].fillna("")
    else:
        combined["Counterparty_Core_Clean"] = ""

    # Group by Anomaly_Type + Description_Norm
    grouped = combined.groupby(["Anomaly_Type", "Description_Norm"]).agg(
        Counterparty_Core=("Counterparty_Core_Clean", "first"),
        Rule_ID=("Rule_ID", "first"),
        Txn_Count=("Txn_ID", "count"),
        Total_Amount=("Amount", "sum"),
        Avg_Amount=("Amount", "mean"),
        First_YearMonth=("YearMonth", "min"),
        Last_YearMonth=("YearMonth", "max"),
        Unique_Months=("YearMonth", "nunique"),
        Bank_Rail_List=("Bank_Rail", lambda x: x.tolist()),
        Amount_List=("Amount", lambda x: x.tolist()),
    ).reset_index()

    # Calculate derived fields
    grouped["Months_Span"] = grouped.apply(
        lambda r: calculate_months_span(r["First_YearMonth"], r["Last_YearMonth"]),
        axis=1
    )

    grouped["Recurrence_Pattern"] = grouped.apply(
        lambda r: detect_recurrence_pattern(r["Txn_Count"], r["Unique_Months"], r["Months_Span"]),
        axis=1
    )

    grouped["Bank_Rail_Breakdown"] = grouped.apply(
        lambda r: _build_rail_breakdown(r["Bank_Rail_List"], r["Amount_List"]),
        axis=1
    )

    grouped["Suggested_Category"] = grouped["Description_Norm"].apply(suggest_category)

    # Round amounts
    grouped["Total_Amount"] = grouped["Total_Amount"].round(2)
    grouped["Avg_Amount"] = grouped["Avg_Amount"].round(2)

    # Drop helper columns
    grouped = grouped.drop(columns=["Bank_Rail_List", "Amount_List"])

    # Sort deterministically: by abs(Total_Amount) desc, then Description_Norm asc
    grouped["_abs_total"] = grouped["Total_Amount"].abs()
    grouped = grouped.sort_values(
        ["_abs_total", "Description_Norm"],
        ascending=[False, True]
    ).drop(columns=["_abs_total"]).reset_index(drop=True)

    return grouped[[
        "Anomaly_Type", "Description_Norm", "Counterparty_Core", "Rule_ID",
        "Txn_Count", "Total_Amount", "Avg_Amount", "First_YearMonth",
        "Last_YearMonth", "Months_Span", "Unique_Months", "Recurrence_Pattern",
        "Bank_Rail_Breakdown", "Suggested_Category"
    ]]


def _build_rail_breakdown(rails: List[str], amounts: List[float]) -> str:
    """
    Build deterministic rail breakdown string like "GIRO:$1234|FAST:$567".
    Sorted by dollar magnitude desc, then rail name asc.
    """
    rail_totals: Dict[str, float] = {}
    for rail, amt in zip(rails, amounts):
        rail_str = str(rail) if pd.notna(rail) else "OTHER"
        rail_totals[rail_str] = rail_totals.get(rail_str, 0) + abs(amt)

    # Sort deterministically
    sorted_rails = sorted(
        rail_totals.items(),
        key=lambda x: (-x[1], x[0])  # By amount desc, then rail name asc
    )

    return "|".join([f"{rail}:${int(amt)}" for rail, amt in sorted_rails])


def load_overrides(overrides_path: Optional[Path]) -> Tuple[pd.DataFrame, bool]:
    """
    Load overrides.xlsx if provided.
    Returns (DataFrame with Enabled=TRUE overrides, overrides_available bool).
    """
    if overrides_path is None or not overrides_path.exists():
        return pd.DataFrame(), False

    try:
        ov = pd.read_excel(overrides_path, sheet_name="Overrides")

        # Normalize Enabled column
        if "Enabled" in ov.columns:
            ov["Enabled"] = ov["Enabled"].astype(str).str.upper().isin(["TRUE", "1", "YES", "Y"])
            ov = ov[ov["Enabled"] == True].copy()
        else:
            # If no Enabled column, assume all are enabled
            pass

        # Ensure Txn_ID exists
        if "Txn_ID" not in ov.columns:
            return pd.DataFrame(), False

        ov["Txn_ID"] = ov["Txn_ID"].astype(str).str.strip()
        ov = ov[ov["Txn_ID"].str.len() > 0].copy()

        return ov, True

    except Exception:
        return pd.DataFrame(), False


def generate_override_masking_report(
    df: pd.DataFrame,
    overrides: pd.DataFrame,
    overrides_available: bool,
) -> pd.DataFrame:
    """
    Generate override masking analysis.
    Reports current state - cannot infer original rule before override.
    """
    rows = []

    if not overrides_available:
        rows.append({
            "Metric": "Overrides_Available",
            "Value": "False",
            "Note": "No overrides file provided or file could not be loaded"
        })
        return pd.DataFrame(rows)

    # Overrides file was loaded
    rows.append({
        "Metric": "Overrides_Available",
        "Value": "True",
        "Note": ""
    })

    rows.append({
        "Metric": "Total_Overrides_Enabled",
        "Value": str(len(overrides)),
        "Note": "Count of Enabled=TRUE rows in overrides.xlsx"
    })

    # Check if Was_Overridden column exists in df
    if "Was_Overridden" in df.columns:
        overridden_count = df["Was_Overridden"].fillna(False).astype(bool).sum()
        override_pct = (overridden_count / len(df) * 100) if len(df) > 0 else 0

        rows.append({
            "Metric": "Transactions_Overridden",
            "Value": str(overridden_count),
            "Note": "Count where Was_Overridden=True in classified data"
        })

        rows.append({
            "Metric": "Override_Pct",
            "Value": f"{override_pct:.2f}%",
            "Note": "Percentage of filtered transactions that were overridden"
        })

        # Find override magnets (descriptions with >=2 overrides)
        overridden_df = df[df["Was_Overridden"] == True].copy()
        if len(overridden_df) > 0:
            overridden_df["Description_Norm"] = overridden_df["Description"].apply(normalize_description)
            desc_counts = overridden_df["Description_Norm"].value_counts()
            magnets = desc_counts[desc_counts >= 2]

            if len(magnets) > 0:
                magnet_str = "; ".join([f"{desc} ({count}x)" for desc, count in magnets.head(10).items()])
                rows.append({
                    "Metric": "Top_Override_Magnets",
                    "Value": magnet_str,
                    "Note": "Descriptions with >=2 overridden transactions (candidates for rule creation)"
                })
            else:
                rows.append({
                    "Metric": "Top_Override_Magnets",
                    "Value": "None",
                    "Note": "No description has >=2 overridden transactions"
                })
        else:
            rows.append({
                "Metric": "Top_Override_Magnets",
                "Value": "None",
                "Note": "No overridden transactions found"
            })
    else:
        rows.append({
            "Metric": "Transactions_Overridden",
            "Value": "N/A",
            "Note": "Was_Overridden column not present in classified data"
        })

        rows.append({
            "Metric": "Override_Pct",
            "Value": "N/A",
            "Note": "Cannot calculate without Was_Overridden column"
        })

        rows.append({
            "Metric": "Top_Override_Magnets",
            "Value": "N/A",
            "Note": "Cannot identify magnets without Was_Overridden column"
        })

    # Add disclaimer about original rule
    rows.append({
        "Metric": "Note_Original_Rule",
        "Value": "Not Available",
        "Note": "Cannot infer original Rule_ID before override. All Rule_ID values reflect current (post-override) classification."
    })

    return pd.DataFrame(rows)


# ======================================================
# OUTPUT AND SUMMARY
# ======================================================

def write_outputs(
    output_dir: Path,
    rule_impact: pd.DataFrame,
    fallback_pressure: pd.DataFrame,
    category_anomaly: pd.DataFrame,
    override_masking: pd.DataFrame,
) -> Dict[str, Path]:
    """Write all CSV artifacts to output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = {}

    # Write rule_impact_summary.csv
    path = output_dir / "rule_impact_summary.csv"
    rule_impact.to_csv(path, index=False)
    paths["rule_impact_summary"] = path

    # Write fallback_pressure_report.csv
    path = output_dir / "fallback_pressure_report.csv"
    fallback_pressure.to_csv(path, index=False)
    paths["fallback_pressure_report"] = path

    # Write category_anomaly_report.csv
    path = output_dir / "category_anomaly_report.csv"
    category_anomaly.to_csv(path, index=False)
    paths["category_anomaly_report"] = path

    # Write override_masking_report.csv
    path = output_dir / "override_masking_report.csv"
    override_masking.to_csv(path, index=False)
    paths["override_masking_report"] = path

    return paths


def print_console_summary(
    input_path: Path,
    total_rows: int,
    filtered_rows: int,
    exclusions: Dict[str, int],
    rule_impact: pd.DataFrame,
    fallback_pressure: pd.DataFrame,
    output_paths: Dict[str, Path],
) -> None:
    """Print human-readable summary to console."""
    print("\n" + "=" * 60)
    print("CLASSIFICATION DIAGNOSTICS")
    print("=" * 60)

    print(f"\nInput: {input_path}")
    print(f"Total rows loaded: {total_rows}")

    excluded_total = sum(exclusions.values())
    print(f"Rows after base filter: {filtered_rows} (excluded: {excluded_total})")
    print(f"  - Summary rows: {exclusions['summary']}")
    print(f"  - Balance B/F: {exclusions['balance_bf']}")
    print(f"  - NON-CASH: {exclusions['non_cash']}")
    print(f"  - TRANSFER: {exclusions['transfer']}")

    # Top 5 rules by absolute impact
    print("\nTop 5 Rules by Absolute Impact:")
    if len(rule_impact) > 0:
        top5 = rule_impact.head(5)
        for _, row in top5.iterrows():
            net = row["Net_Impact"]
            direction = "inflow" if net >= 0 else "outflow"
            pct = row["Inflow_Pct"] if net >= 0 else row["Outflow_Pct"]
            print(f"  {row['Abs_Impact_Rank']}. {row['Rule_ID']:<25} | ${abs(net):>10,.0f} ({pct:.1f}% {direction})")
    else:
        print("  (no data)")

    # Fallback pressure
    print("\nFallback Pressure:")
    for _, row in fallback_pressure.iterrows():
        severity_marker = ""
        if row["Severity"] == "CRITICAL":
            severity_marker = " [CRITICAL]"
        elif row["Severity"] == "WARNING":
            severity_marker = " [WARNING]"

        print(f"  {row['Rule_ID']}: {row['Pct_of_Direction']:.1f}% of {row['Direction']}s{severity_marker}")

    # Output paths
    print(f"\nOutputs written to: {output_paths['rule_impact_summary'].parent}/")
    for name, path in output_paths.items():
        print(f"  - {path.name}")

    print("\n" + "=" * 60)


# ======================================================
# CLI AND MAIN
# ======================================================

def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="FP&A-grade classification diagnostics tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python classification_diagnostics.py --input data.csv --output-dir diagnostics/
  python classification_diagnostics.py --input data.csv --output-dir diagnostics/ --overrides overrides.xlsx
  python classification_diagnostics.py --input data.csv --output-dir diagnostics/ --include-transfers
        """
    )

    parser.add_argument(
        "--input",
        required=True,
        type=str,
        help="Path to classified_transactions_v3.csv"
    )

    parser.add_argument(
        "--output-dir",
        required=True,
        type=str,
        help="Output directory for diagnostic CSVs"
    )

    parser.add_argument(
        "--overrides",
        type=str,
        default=None,
        help="Optional path to overrides.xlsx"
    )

    parser.add_argument(
        "--include-transfers",
        action="store_true",
        help="Include TRANSFER section in analysis (default: exclude)"
    )

    parser.add_argument(
        "--include-non-cash",
        action="store_true",
        help="Include NON-CASH section in analysis (default: exclude)"
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Resolve paths
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    overrides_path = Path(args.overrides) if args.overrides else None

    # Validate input exists
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Load data
    df = pd.read_csv(input_path)
    total_rows = len(df)

    # Normalize columns (explicit alias creation)
    df = normalize_columns(df)

    # Validate required columns (after normalization)
    validate_required_columns(df)

    # Apply base filter
    df_filtered, exclusions = apply_base_filter(
        df,
        include_transfers=args.include_transfers,
        include_non_cash=args.include_non_cash,
    )
    filtered_rows = len(df_filtered)

    # Load overrides
    overrides, overrides_available = load_overrides(overrides_path)

    # Generate reports
    rule_impact = generate_rule_impact_summary(df_filtered)
    fallback_pressure = generate_fallback_pressure_report(df_filtered)
    category_anomaly = generate_category_anomaly_report(df_filtered)
    override_masking = generate_override_masking_report(
        df_filtered, overrides, overrides_available
    )

    # Write outputs
    output_paths = write_outputs(
        output_dir,
        rule_impact,
        fallback_pressure,
        category_anomaly,
        override_masking,
    )

    # Print summary
    print_console_summary(
        input_path,
        total_rows,
        filtered_rows,
        exclusions,
        rule_impact,
        fallback_pressure,
        output_paths,
    )


if __name__ == "__main__":
    main()
