#!/usr/bin/env python3
"""
loan_equity.py

Computes property equity and validates loan balance data.

Input Contract
--------------
Expects CSV with columns:
- Loan_ID (str): Unique loan identifier
- Property_ID (str): Property identifier
- AsOfMonth (str): Month-end date in YYYY-MM format
- Outstanding_Balance (float): Current loan balance at month-end
- Previous_Balance (float): Previous month's balance (0.0 for initial loan)
- Principal_Paid (float): Principal paid during month (computed or provided)
- Balance_Increase (float): Balance increase for refinance/top-up events
- Loan_Event (str): Event type (Initial Loan, Regular Payment, Refinance/Top-up)

Validations
-----------
1. No negative principal paid unless flagged as Refinance/Top-up
2. Balance continuity: Outstanding_Balance[t] = Previous_Balance[t] - Principal_Paid[t] + Balance_Increase[t]
3. AsOfMonth must be parseable as YYYY-MM
4. Outstanding_Balance >= 0
5. No duplicate (Loan_ID, AsOfMonth) combinations
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pandas as pd


class EquityValidationError(Exception):
    """Raised when equity data validation fails."""
    pass


# ======================================================
# INPUT CONTRACT VALIDATION
# ======================================================

_REQUIRED_COLS = {
    "Loan_ID",
    "Property_ID",
    "AsOfMonth",
    "Outstanding_Balance",
    "Previous_Balance",
    "Principal_Paid",
    "Balance_Increase",
    "Loan_Event",
}


def validate_equity_data(df: pd.DataFrame) -> Tuple[bool, list[str]]:
    """
    Validate equity_build_up_monthly.csv data contract.

    Returns:
        (is_valid, error_messages)
    """
    errors = []

    # 1. Check required columns
    missing_cols = _REQUIRED_COLS - set(df.columns)
    if missing_cols:
        errors.append(f"Missing required columns: {sorted(missing_cols)}")
        return False, errors

    # 2. Check for empty dataframe
    if len(df) == 0:
        errors.append("DataFrame is empty")
        return False, errors

    # 3. Validate AsOfMonth format (YYYY-MM)
    try:
        pd.to_datetime(df["AsOfMonth"], format="%Y-%m", errors="raise")
    except Exception as e:
        errors.append(f"AsOfMonth format error (expected YYYY-MM): {e}")

    # 4. Check for negative Outstanding_Balance
    negative_balance = df[df["Outstanding_Balance"] < 0]
    if len(negative_balance) > 0:
        errors.append(
            f"Found {len(negative_balance)} rows with negative Outstanding_Balance: "
            f"{negative_balance[['Loan_ID', 'AsOfMonth', 'Outstanding_Balance']].to_dict('records')}"
        )

    # 5. Check for duplicate (Loan_ID, AsOfMonth)
    duplicates = df.duplicated(subset=["Loan_ID", "AsOfMonth"], keep=False)
    if duplicates.any():
        dup_rows = df[duplicates][["Loan_ID", "AsOfMonth"]]
        errors.append(
            f"Found {duplicates.sum()} duplicate (Loan_ID, AsOfMonth) combinations: "
            f"{dup_rows.to_dict('records')}"
        )

    # 6. Validate balance continuity equation (skip for Initial Loan events)
    # Outstanding_Balance[t] = Previous_Balance[t] - Principal_Paid[t] + Balance_Increase[t]
    # For Initial Loan: Outstanding_Balance = loan amount (no continuity check needed)
    non_initial = df[~df["Loan_Event"].str.contains("Initial Loan", case=False, na=False)].copy()
    if len(non_initial) > 0:
        non_initial["_computed_balance"] = (
            non_initial["Previous_Balance"] - non_initial["Principal_Paid"] + non_initial["Balance_Increase"]
        )
        balance_mismatch = non_initial[
            (non_initial["Outstanding_Balance"] - non_initial["_computed_balance"]).abs() > 0.01
        ]
        if len(balance_mismatch) > 0:
            errors.append(
                f"Found {len(balance_mismatch)} rows with balance continuity errors: "
                f"{balance_mismatch[['Loan_ID', 'AsOfMonth', 'Outstanding_Balance', '_computed_balance']].to_dict('records')}"
            )

    # 7. Check for negative Principal_Paid without Refinance/Top-up flag
    negative_principal = df[
        (df["Principal_Paid"] < 0)
        & (~df["Loan_Event"].str.contains("Refinance|Top-up", case=False, na=False))
    ]
    if len(negative_principal) > 0:
        errors.append(
            f"Found {len(negative_principal)} rows with negative Principal_Paid without Refinance/Top-up event: "
            f"{negative_principal[['Loan_ID', 'AsOfMonth', 'Principal_Paid', 'Loan_Event']].to_dict('records')}"
        )

    # 8. Warn if Balance_Increase > 0 without Refinance/Top-up flag
    balance_increase_no_flag = df[
        (df["Balance_Increase"] > 0)
        & (~df["Loan_Event"].str.contains("Refinance|Top-up", case=False, na=False))
    ]
    if len(balance_increase_no_flag) > 0:
        errors.append(
            f"[WARNING] Found {len(balance_increase_no_flag)} rows with Balance_Increase > 0 without Refinance/Top-up event: "
            f"{balance_increase_no_flag[['Loan_ID', 'AsOfMonth', 'Balance_Increase', 'Loan_Event']].to_dict('records')}"
        )

    is_valid = len([e for e in errors if not e.startswith("[WARNING]")]) == 0
    return is_valid, errors


# ======================================================
# EQUITY COMPUTATION
# ======================================================


def compute_equity_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute equity summary from validated equity data.

    Returns:
        DataFrame with columns:
        - AsOfMonth: Month-end date (YYYY-MM)
        - Total_Outstanding_Balance: Sum of all loan balances
        - Total_Principal_Paid: Sum of principal paid across all loans
        - Total_Balance_Increase: Sum of balance increases (refinance/top-up)
        - Net_Equity_Change: Principal_Paid - Balance_Increase (positive = equity buildup)
        - Cumulative_Equity: Running total of net equity change
    """
    # Validate first
    is_valid, errors = validate_equity_data(df)
    if not is_valid:
        raise EquityValidationError(
            f"Equity data validation failed:\n" + "\n".join(errors)
        )

    # Group by month
    monthly = (
        df.groupby("AsOfMonth")
        .agg(
            {
                "Outstanding_Balance": "sum",
                "Principal_Paid": "sum",
                "Balance_Increase": "sum",
            }
        )
        .reset_index()
    )

    # Rename columns for clarity
    monthly = monthly.rename(
        columns={
            "Outstanding_Balance": "Total_Outstanding_Balance",
            "Principal_Paid": "Total_Principal_Paid",
            "Balance_Increase": "Total_Balance_Increase",
        }
    )

    # Compute net equity change (positive = equity buildup)
    monthly["Net_Equity_Change"] = (
        monthly["Total_Principal_Paid"] - monthly["Total_Balance_Increase"]
    )

    # Compute cumulative equity
    monthly["Cumulative_Equity"] = monthly["Net_Equity_Change"].cumsum()

    # Sort by month
    monthly = monthly.sort_values("AsOfMonth").reset_index(drop=True)

    return monthly


# ======================================================
# CLI INTERFACE (for testing)
# ======================================================


def main():
    """CLI entry point for validation and equity computation."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python loan_equity.py <path_to_equity_csv>")
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"[ERROR] File not found: {csv_path}")
        sys.exit(1)

    # Load data
    print(f"[INFO] Loading equity data from: {csv_path}")
    df = pd.read_csv(csv_path)
    print(f"[INFO] Loaded {len(df)} records")

    # Validate
    print("[INFO] Validating equity data...")
    is_valid, errors = validate_equity_data(df)

    if not is_valid:
        print("[ERROR] Validation failed:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    if errors:
        print("[WARNING] Validation warnings:")
        for err in errors:
            print(f"  - {err}")

    print("[OK] Validation passed")

    # Compute equity summary
    print("[INFO] Computing equity summary...")
    try:
        summary = compute_equity_summary(df)
        print("[OK] Equity summary computed")
        print("\n" + summary.to_string(index=False))

        # Save summary
        output_path = csv_path.parent / "equity_summary_monthly.csv"
        summary.to_csv(output_path, index=False)
        print(f"\n[OK] Saved equity summary to: {output_path}")

    except EquityValidationError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
