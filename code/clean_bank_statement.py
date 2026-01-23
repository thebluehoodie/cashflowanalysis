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
import hashlib
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


def _collapse_ws(s: str) -> str:
    return " ".join(s.split())


def _canon_desc(s: object) -> str:
    if pd.isna(s):
        return ""
    return _collapse_ws(str(s)).upper()


def _canon_date(d: object) -> str:
    if pd.isna(d):
        return ""
    s = str(d).strip()
    if s == "":
        return ""
    ts = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if pd.isna(ts):
        return _collapse_ws(s)
    return ts.strftime("%Y-%m-%d")


def _canon_amount_cents(a: object) -> str:
    if pd.isna(a):
        raise ValueError("Missing Amount for Txn_ID.")
    s = str(a).strip()
    if s == "":
        raise ValueError("Missing Amount for Txn_ID.")
    try:
        v = float(s)
    except ValueError as exc:
        raise ValueError(f"Invalid Amount for Txn_ID: {a}") from exc
    return str(int(round(v * 100)))


def _mk_row_fingerprint(row: pd.Series) -> str:
    """
    Generate deterministic row fingerprint from stable content fields.

    Used as final tie-breaker for occurrence index assignment (NOT part of Txn_ID hash).
    This ensures deterministic ordering even when all other tie-breakers are identical.

    CRITICAL: All numeric fields canonicalized to integer cents to prevent
    floating-point string variations from affecting determinism.

    Fields included (all stable, content-based):
    - Date, YearMonth, Amount, Description, SourceFile (base_key fields)
    - Balance, Withdrawals, Deposits (additional numeric differentiators)

    Returns:
        40-character SHA-1 hex hash of canonicalized row content
    """
    date = _canon_date(row.get("Date", ""))
    if date == "":
        date = "NA"

    year_month = _collapse_ws(_normalize_str(row.get("YearMonth", "")))

    # HARDENING: Canonicalize ALL numeric fields to integer cents
    amount = _canon_amount_cents(row.get("Amount", "0"))

    desc = _canon_desc(row.get("Description", ""))
    source = _canon_desc(row.get("SourceFile", ""))

    # Additional fields for fingerprint (canonicalized to cents)
    balance_val = row.get("Balance", None)
    balance = _canon_amount_cents(balance_val) if pd.notna(balance_val) else "NaN"

    withdrawals_val = row.get("Withdrawals", None)
    withdrawals = _canon_amount_cents(withdrawals_val) if pd.notna(withdrawals_val) else "NaN"

    deposits_val = row.get("Deposits", None)
    deposits = _canon_amount_cents(deposits_val) if pd.notna(deposits_val) else "NaN"

    # Concatenate all fields
    fingerprint_key = "|".join([
        date, year_month, amount, desc, source,
        balance, withdrawals, deposits
    ])

    return hashlib.sha1(fingerprint_key.encode("utf-8")).hexdigest()


def _mk_txn_id(row: pd.Series, occurrence_index: int) -> str:
    """
    Generate Txn_ID with occurrence index.

    New scheme (order-independent):
    - base_key = Date | YearMonth | Amount_cents | Description | SourceFile
    - RowOrder REMOVED from base_key for order independence
    - occurrence_index added for disambiguation when base_keys collide

    Args:
        row: Transaction row with required fields
        occurrence_index: 1-based index within duplicate base_key group

    Returns:
        40-character SHA-1 hex hash
    """
    date = _canon_date(row.get("Date", ""))
    if date == "":
        date = "NA"

    year_month = _collapse_ws(_normalize_str(row.get("YearMonth", "")))
    if year_month == "":
        raise ValueError("Missing YearMonth for Txn_ID.")

    amount_cents = _canon_amount_cents(row.get("Amount", ""))
    desc = _canon_desc(row.get("Description", ""))

    source = _canon_desc(row.get("SourceFile", ""))
    if source == "":
        raise ValueError("Missing SourceFile for Txn_ID.")

    # Build base_key WITHOUT RowOrder
    base_key = "|".join([date, year_month, amount_cents, desc, source])

    # Append occurrence index
    occ_suffix = f"OCC{occurrence_index:03d}"
    raw_key = f"{base_key}|{occ_suffix}"

    return hashlib.sha1(raw_key.encode("utf-8")).hexdigest()


def _generate_occurrence_indices(df: pd.DataFrame) -> pd.Series:
    """
    Assign occurrence index (1-based) to each row within base_key groups.

    For transactions with identical base_key (Date, YearMonth, Amount, Description, SourceFile),
    assign deterministic occurrence indices based on tie-breaker sort:
      1. Balance (ascending, NaN last)
      2. Withdrawals (ascending, NaN last)
      3. Deposits (ascending, NaN last)
      4. Amount (ascending, NaN last)
      5. row_fingerprint (ascending) - final deterministic tie-breaker

    Note: RowOrder has been REMOVED to achieve true order-independence.
    row_fingerprint ensures deterministic ordering even when all other fields are identical.

    HARDENING: Detects true indistinguishable duplicates (where row_fingerprint is identical
    within a base_key group) and raises ValueError to prevent silent instability.

    Returns:
        pd.Series of occurrence indices (1, 2, 3, ...) aligned with df index

    Raises:
        ValueError: If indistinguishable duplicate transactions are detected
    """
    df_work = df.copy()

    # Build base_key for grouping
    df_work["_base_key"] = (
        df_work["Date"].apply(_canon_date).replace("", "NA") + "|" +
        df_work["YearMonth"].apply(lambda x: _collapse_ws(_normalize_str(x))) + "|" +
        df_work["Amount"].apply(lambda x: str(int(round(float(x) * 100)))) + "|" +
        df_work["Description"].apply(_canon_desc) + "|" +
        df_work["SourceFile"].apply(_canon_desc)
    )

    # Generate row fingerprint as final deterministic tie-breaker
    df_work["_row_fingerprint"] = df_work.apply(_mk_row_fingerprint, axis=1)

    # HARDENING: Detect true indistinguishable duplicates
    # Check for rows with identical base_key AND identical row_fingerprint
    dup_check = df_work.groupby(["_base_key", "_row_fingerprint"]).size()
    true_dups = dup_check[dup_check > 1]

    if not true_dups.empty:
        # Find sample rows for error message
        first_dup_key = true_dups.index[0]
        dup_rows = df_work[
            (df_work["_base_key"] == first_dup_key[0]) &
            (df_work["_row_fingerprint"] == first_dup_key[1])
        ]

        error_msg = (
            f"FATAL: Indistinguishable duplicate transactions detected.\n"
            f"Txn_ID cannot be made stable when rows are identical across ALL content fields.\n"
            f"\nDetected {len(true_dups)} duplicate groups affecting {true_dups.sum()} rows.\n"
            f"\nSample duplicate group (base_key + fingerprint identical):\n"
            f"{dup_rows[['Date', 'Amount', 'Description', 'SourceFile', 'Balance']].to_string(index=False)}\n"
            f"\nThis indicates:\n"
            f"  - Duplicate bank statement ingestion, OR\n"
            f"  - True duplicate transactions with no differentiating fields\n"
            f"\nAction required:\n"
            f"  - Review source CSV files for duplicates\n"
            f"  - Add manual differentiating field if these are truly distinct transactions"
        )
        raise ValueError(error_msg)

    # Sort by tie-breaker within each base_key group
    # Use stable mergesort for consistency
    df_sorted = df_work.sort_values(
        by=["_base_key", "Balance", "Withdrawals", "Deposits", "Amount", "_row_fingerprint"],
        kind="mergesort",
        na_position="last"
    ).reset_index(drop=False)

    # Assign occurrence index within each base_key group
    df_sorted["_occurrence_index"] = df_sorted.groupby("_base_key").cumcount() + 1

    # Restore original index order and return occurrence series
    df_sorted = df_sorted.set_index("index").sort_index()

    return df_sorted["_occurrence_index"]


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

    # Generate occurrence indices for base_key collisions
    out["_occurrence_index"] = _generate_occurrence_indices(out)

    # Generate Txn_IDs with occurrence indices
    out["Txn_ID"] = out.apply(lambda row: _mk_txn_id(row, row["_occurrence_index"]), axis=1)

    # HARDENING: Explicit invariant assertions
    # 1. No blank Txn_IDs
    blank_mask = out["Txn_ID"].isna() | (out["Txn_ID"] == "")
    if blank_mask.any():
        sample = out.loc[blank_mask, ["Date", "Amount", "Description", "SourceFile"]].head(5)
        raise ValueError(f"FATAL: Blank Txn_IDs detected:\n{sample.to_string(index=False)}")

    # 2. Txn_ID uniqueness must equal row count
    txn_id_count = len(out)
    txn_id_unique = out["Txn_ID"].nunique()
    if txn_id_unique != txn_id_count:
        raise ValueError(
            f"FATAL: Txn_ID uniqueness violation. "
            f"Expected {txn_id_count} unique Txn_IDs, got {txn_id_unique}. "
            f"Difference: {txn_id_count - txn_id_unique} duplicates."
        )

    # 3. Legacy duplicate check (for detailed diagnostics)
    dup_mask = out["Txn_ID"].duplicated(keep=False)
    if dup_mask.any():
        sample = out.loc[
            dup_mask,
            ["Txn_ID", "Date", "YearMonth", "Amount", "Description", "SourceFile", "Balance", "RowOrder"]
        ].head(10)
        raise ValueError(f"CRITICAL: Txn_ID collision after occurrence index assignment:\n{sample.to_string(index=False)}")

    # Drop helper column
    out = out.drop(columns=["_occurrence_index"])

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
    dup_mask = combined["Txn_ID"].duplicated(keep=False)
    if dup_mask.any():
        sample = combined.loc[
            dup_mask,
            ["Txn_ID", "Date", "YearMonth", "Amount", "Description", "SourceFile", "Balance", "RowOrder"]
        ].head(10)
        raise ValueError(f"CRITICAL: Txn_ID collision in combined output (implementation bug):\n{sample.to_string(index=False)}")
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
