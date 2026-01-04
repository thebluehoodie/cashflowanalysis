#!/usr/bin/env python3
"""
clean_bank_statements.py

Cleans Tabula-extracted bank statement CSVs where:
- Page headers repeat mid-file (e.g., SGD rows, repeated column header rows)
- Transaction descriptions wrap across multiple rows
- Numeric columns are strings with commas
- Dates may lack year (e.g., "02 Jan") -> infer year from filename

Outputs:
- cleaned_<original>.csv per file
- combined_cleaned.csv for all files
- reconciliation_report.csv (opening + sum(amount) vs closing)

Usage examples:
  python clean_bank_statements.py --input_dir "/path/to/csvs" --output_dir "/path/to/out"
  python clean_bank_statements.py --files "2024_1. Jan24.csv" "2024_2. Feb24.csv" --output_dir out
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Tuple

import pandas as pd


EXPECTED_COLS = ["Date", "Description", "Withdrawals", "Deposits", "Balance"]

MONTH_MAP: Dict[str, int] = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
}


@dataclass
class ReconcileResult:
    source_file: str
    year_month: str
    opening_balance: Optional[float]
    closing_balance: Optional[float]
    sum_amount: float
    delta: Optional[float]
    ok: bool


def _normalize_str(x: object) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip()


def _is_header_row(row: pd.Series) -> bool:
    """
    Detect repeated page headers and currency header rows.
    """
    date = _normalize_str(row.get("Date", ""))
    desc = _normalize_str(row.get("Description", ""))
    w = _normalize_str(row.get("Withdrawals", ""))
    d = _normalize_str(row.get("Deposits", ""))
    b = _normalize_str(row.get("Balance", ""))

    # Currency header pattern: Withdrawals/Deposits/Balance show SGD
    if w.upper() == "SGD" and d.upper() == "SGD" and b.upper() == "SGD":
        return True

    # Repeated column header row that sometimes gets injected mid-file
    tokens = {date.upper(), desc.upper(), w.upper(), d.upper(), b.upper()}
    if "DATE" in tokens and "DESCRIPTION" in tokens:
        return True
    if date.upper() == "DATE" or desc.upper() == "DESCRIPTION" or b.upper() == "BALANCE":
        return True

    # Fully blank separator rows
    if date == "" and desc == "" and w == "" and d == "" and b == "":
        return True

    return False


def _parse_amount(x: str) -> float:
    """
    Parse numeric strings like '3,610.00' or '' into float.
    Handles parentheses as negatives if present.
    Returns NaN if cannot parse.
    """
    s = _normalize_str(x)
    if s == "":
        return float("nan")

    # Remove common noise while keeping digits, minus, dot, parentheses
    # e.g. "1,234.56" -> "1234.56"
    s = s.replace(",", "")
    s = re.sub(r"[^0-9\.\-\(\)]", "", s)

    # Handle (123.45) as -123.45
    m = re.fullmatch(r"\((\-?\d+(\.\d+)?)\)", s)
    if m:
        s = "-" + m.group(1)

    try:
        return float(s)
    except ValueError:
        return float("nan")


def _infer_year_month_from_filename(path: Path) -> Tuple[Optional[int], Optional[int]]:
    """
    Infer (year, month) from filename patterns like:
      '2024_1. Jan24.csv' -> (2024, 1)
      '2024_10. Oct24.csv' -> (2024, 10)
      'UOB_2025_Mar.csv' -> (2025, 3)   [best-effort]
    If only year is found, month may be None.
    """
    name = path.name.upper()

    # Year
    year = None
    m_year = re.search(r"(20\d{2})", name)
    if m_year:
        year = int(m_year.group(1))

    # Month (prefer explicit 3-letter month)
    month = None
    m_mon = re.search(r"\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b", name)
    if m_mon:
        month = MONTH_MAP.get(m_mon.group(1))

        # If filename is like Jan24 (2-digit year) and year wasn't captured
        if year is None:
            m_yy = re.search(r"\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})\b", name)
            if m_yy:
                yy = int(m_yy.group(2))
                year = 2000 + yy

    return year, month


def _parse_date_with_year(date_str: str, year: Optional[int]) -> Optional[pd.Timestamp]:
    """
    Parses date formats like:
      '02 Jan' (no year) -> append inferred year
      '01 Jan 2024'      -> parse directly
      '2024-01-02'       -> parse directly
    """
    s = _normalize_str(date_str)
    if s == "":
        return None

    if re.search(r"\b20\d{2}\b", s):
        ts = pd.to_datetime(s, errors="coerce", dayfirst=True)
        return ts if not pd.isna(ts) else None

    if year is not None:
        ts = pd.to_datetime(f"{s} {year}", errors="coerce", dayfirst=True)
        return ts if not pd.isna(ts) else None

    ts = pd.to_datetime(s, errors="coerce", dayfirst=True)
    return ts if not pd.isna(ts) else None


def clean_statement_csv(csv_path: Path) -> pd.DataFrame:
    """
    Returns a cleaned transaction-level dataframe for one statement CSV.
    """
    raw = pd.read_csv(csv_path, dtype=str)
    missing = [c for c in EXPECTED_COLS if c not in raw.columns]
    if missing:
        raise ValueError(f"{csv_path.name}: Missing expected columns: {missing}. Found: {list(raw.columns)}")

    df = raw[EXPECTED_COLS].copy()

    # Normalize strings
    for c in EXPECTED_COLS:
        df[c] = df[c].map(_normalize_str)

    # Remove headers/blanks
    df = df[~df.apply(_is_header_row, axis=1)].copy()
    df.reset_index(drop=True, inplace=True)

    # Preserve original row order (critical when dates are missing)
    df["_row_order"] = range(len(df))

    # Identify anchor rows: new transaction begins when Date present OR any numeric fields present
    def is_anchor(r: pd.Series) -> bool:
        return (r["Date"] != "") or (r["Withdrawals"] != "") or (r["Deposits"] != "") or (r["Balance"] != "")

    df["_is_anchor"] = df.apply(is_anchor, axis=1)
    if len(df) > 0 and not bool(df.loc[0, "_is_anchor"]):
        df.loc[0, "_is_anchor"] = True

    df["_grp"] = df["_is_anchor"].cumsum()

    def first_non_empty(series: pd.Series) -> str:
        for v in series.tolist():
            v = _normalize_str(v)
            if v != "":
                return v
        return ""

    grouped = df.groupby("_grp", as_index=False).agg(
        Date=("Date", first_non_empty),
        Description=("Description", lambda s: " ".join([x for x in (t.strip() for t in s.tolist()) if x])),
        Withdrawals=("Withdrawals", first_non_empty),
        Deposits=("Deposits", first_non_empty),
        Balance=("Balance", first_non_empty),
        RowsMerged=("Description", "size"),
        RowOrder=("._row_order".replace(".", ""), "min") if False else ("_row_order", "min"),
    )

    # Infer year/month from filename
    year, file_month = _infer_year_month_from_filename(csv_path)

    # Parse dates
    grouped["DateParsed"] = grouped["Date"].apply(lambda x: _parse_date_with_year(x, year))

    # Parse amounts (float; NaN if empty/unparseable)
    grouped["WithdrawalsNum"] = grouped["Withdrawals"].apply(_parse_amount)
    grouped["DepositsNum"] = grouped["Deposits"].apply(_parse_amount)
    grouped["BalanceNum"] = grouped["Balance"].apply(_parse_amount)

    # CRITICAL FIX: Amount must not be NaN when only one side exists
    grouped["Amount"] = grouped["DepositsNum"].fillna(0.0) - grouped["WithdrawalsNum"].fillna(0.0)

    # YearMonth: from parsed date; fallback to filename year-month if missing
    grouped["YearMonth"] = grouped["DateParsed"].dt.strftime("%Y-%m")

    if year is not None and file_month is not None:
        fallback_ym = f"{year:04d}-{file_month:02d}"
        grouped["YearMonth"] = grouped["YearMonth"].fillna(fallback_ym)

    # Attach source metadata
    grouped["SourceFile"] = csv_path.name

    out = grouped[[
        "DateParsed", "YearMonth", "Description", "Amount",
        "BalanceNum", "WithdrawalsNum", "DepositsNum", "RowsMerged", "SourceFile", "RowOrder"
    ]].rename(columns={
        "DateParsed": "Date",
        "BalanceNum": "Balance",
        "WithdrawalsNum": "Withdrawals",
        "DepositsNum": "Deposits",
    })

    # Drop rows that are entirely empty/no-signal
    out = out[~(
        out["Date"].isna()
        & out["Description"].eq("")
        & out["Amount"].fillna(0.0).eq(0.0)
        & out["Balance"].isna()
    )].copy()

    # Ensure deterministic ordering
    out = out.sort_values(["RowOrder"]).reset_index(drop=True)

    return out


def reconcile(clean_df: pd.DataFrame, tolerance: float = 0.02) -> List[ReconcileResult]:
    """
    Reconcile per SourceFile + YearMonth:
      opening_balance + sum(amount) ~ closing_balance

    Uses first and last non-null Balance in file-order (RowOrder),
    not purely date order (because Balance B/F may have no date).
    """
    results: List[ReconcileResult] = []

    if clean_df.empty:
        return results

    df = clean_df.copy()

    # Ensure numeric
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0.0)
    df["Balance"] = pd.to_numeric(df["Balance"], errors="coerce")

    # Only where YearMonth exists (we now fill it from filename if possible)
    df = df.dropna(subset=["YearMonth"]).copy()
    if df.empty:
        return results

    # Sort by file order
    if "RowOrder" in df.columns:
        df = df.sort_values(["SourceFile", "YearMonth", "RowOrder"])
    else:
        df = df.sort_values(["SourceFile", "YearMonth", "Date"], na_position="first")

    for (src, ym), g in df.groupby(["SourceFile", "YearMonth"]):
        balances = g["Balance"].dropna().tolist()
        opening = balances[0] if balances else None
        closing = balances[-1] if balances else None
        sum_amount = float(g["Amount"].sum())

        delta = None
        ok = False
        if opening is not None and closing is not None:
            delta = (opening + sum_amount) - closing
            ok = abs(delta) <= tolerance

        results.append(ReconcileResult(
            source_file=str(src),
            year_month=str(ym),
            opening_balance=opening,
            closing_balance=closing,
            sum_amount=sum_amount,
            delta=delta,
            ok=ok
        ))

    return results


def _load_env_from_dotenv() -> None:
    """
    Loads .env located next to this script (not CWD), without requiring python-dotenv
    """
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return

    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(dotenv_path=env_path)
        return
    except Exception:
        # minimal fallback parser
        with env_path.open("r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                key, value = raw.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key and key not in os.environ:
                    os.environ[key] = value


def main():
    ap = argparse.ArgumentParser()
    grp = ap.add_mutually_exclusive_group(required=False)
    grp.add_argument("--input_dir", type=str, help="Directory containing CSV files")
    grp.add_argument("--files", nargs="+", type=str, help="List of CSV files to process")
    ap.add_argument("--output_dir", type=str, required=False, help="Directory to write outputs")
    ap.add_argument("--tolerance", type=float, default=0.02, help="Reconciliation tolerance (default: 0.02)")
    args = ap.parse_args()

    _load_env_from_dotenv()

    input_dir = args.input_dir or os.getenv("CLEAN_INPUT_DIR")
    output_dir = args.output_dir or os.getenv("CLEAN_OUTPUT_DIR")

    if not output_dir:
        raise SystemExit("Missing output_dir. Provide --output_dir or set CLEAN_OUTPUT_DIR in .env.")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.files:
        csv_files = [Path(f) for f in args.files]
    else:
        if not input_dir:
            raise SystemExit("Missing input_dir. Provide --input_dir or set CLEAN_INPUT_DIR in .env.")
        in_dir = Path(input_dir)
        if not in_dir.exists():
            raise SystemExit(f"Input directory does not exist: {in_dir}")
        csv_files = sorted(in_dir.glob("*.csv"))

    if not csv_files:
        raise SystemExit("No CSV files found to process.")

    all_cleaned = []
    per_file_reports = []

    for f in csv_files:
        clean_df = clean_statement_csv(f)
        all_cleaned.append(clean_df)

        cleaned_name = f"cleaned_{f.stem}.csv"
        clean_df.to_csv(out_dir / cleaned_name, index=False)

        rec = reconcile(clean_df, tolerance=args.tolerance)
        for r in rec:
            per_file_reports.append({
                "SourceFile": r.source_file,
                "YearMonth": r.year_month,
                "OpeningBalance": r.opening_balance,
                "SumAmount": r.sum_amount,
                "ClosingBalance": r.closing_balance,
                "Delta": r.delta,
                "OK": r.ok
            })

    combined = pd.concat(all_cleaned, ignore_index=True)
    combined.to_csv(out_dir / "combined_cleaned.csv", index=False)

    report_df = pd.DataFrame(per_file_reports)
    report_df.to_csv(out_dir / "reconciliation_report.csv", index=False)

    print(f"Processed {len(csv_files)} files.")
    print(f"Wrote outputs to: {out_dir.resolve()}")

    if not report_df.empty:
        ok_rate = report_df["OK"].mean()
        print(f"Reconciliation OK rate: {ok_rate:.1%}")
        bad = report_df[~report_df["OK"]]
        if not bad.empty:
            print("\nMonths failing reconciliation (investigate these first):")
            print(bad[["SourceFile", "YearMonth", "Delta"]].to_string(index=False))


if __name__ == "__main__":
    main()

 