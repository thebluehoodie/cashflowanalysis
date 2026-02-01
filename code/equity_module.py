#!/usr/bin/env python3
"""
equity_module.py

Computes monthly equity build-up from loan outstanding balances.

Purpose:
- Track principal paid down month-over-month (equity buildup)
- Track balance increases (refinance/top-up visibility)
- Deterministic, audit-grade balance-sheet analytics separate from cashflow classification

Input:
- inputs/loan_balances.csv with columns:
    * Loan_ID (string)
    * AsOfMonth (YYYY-MM format)
    * Outstanding_Balance (number)
    * Optional: Property_ID, Loan_Event

Output:
- outputs/equity_build_up_monthly.csv with columns:
    * Loan_ID, Property_ID, AsOfMonth, Outstanding_Balance
    * Previous_Balance, Principal_Paid, Balance_Increase, Loan_Event

Computation:
- Principal_Paid = max(0, Prev_Balance - Curr_Balance)
- Balance_Increase = max(0, Curr_Balance - Prev_Balance)
- Sorted by Loan_ID, AsOfMonth for auditability

Environment Variables:
- EQUITY_INPUT_CSV: Path to loan balances input (default: inputs/loan_balances.csv)
- EQUITY_OUTPUT_CSV: Path to equity output (default: outputs/equity_build_up_monthly.csv)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv


def compute_equity_buildup(input_csv: str, output_csv: str) -> pd.DataFrame:
    """
    Load loan balances and compute monthly equity buildup.

    Args:
        input_csv: Path to loan_balances.csv
        output_csv: Path to write equity_build_up_monthly.csv

    Returns:
        DataFrame with equity calculations

    Raises:
        FileNotFoundError: If input CSV doesn't exist
        ValueError: If required columns missing or data invalid
    """
    # Validate input
    if not Path(input_csv).exists():
        raise FileNotFoundError(f"Loan balances file not found: {input_csv}")

    # Load data
    df = pd.read_csv(input_csv)

    # Validate required columns
    required_cols = ["Loan_ID", "AsOfMonth", "Outstanding_Balance"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # Validate data types and values
    if df.empty:
        raise ValueError("Loan balances CSV is empty")

    if df["Loan_ID"].isnull().any():
        raise ValueError("Loan_ID cannot be null")

    if df["AsOfMonth"].isnull().any():
        raise ValueError("AsOfMonth cannot be null")

    # Validate AsOfMonth format (YYYY-MM)
    invalid_months = df[~df["AsOfMonth"].astype(str).str.match(r"^\d{4}-\d{2}$")]
    if not invalid_months.empty:
        raise ValueError(
            f"Invalid AsOfMonth format (expected YYYY-MM): "
            f"{invalid_months['AsOfMonth'].tolist()}"
        )

    # Convert Outstanding_Balance to numeric
    df["Outstanding_Balance"] = pd.to_numeric(
        df["Outstanding_Balance"],
        errors="coerce"
    )

    if df["Outstanding_Balance"].isnull().any():
        raise ValueError("Outstanding_Balance contains non-numeric values")

    # Sort by Loan_ID and AsOfMonth for consistent processing
    df = df.sort_values(["Loan_ID", "AsOfMonth"]).reset_index(drop=True)

    # Add optional columns if missing (for consistent output schema)
    if "Property_ID" not in df.columns:
        df["Property_ID"] = ""
    if "Loan_Event" not in df.columns:
        df["Loan_Event"] = ""

    # Compute previous balance within each loan
    df["Previous_Balance"] = df.groupby("Loan_ID")["Outstanding_Balance"].shift(1)

    # Compute equity metrics
    # Principal_Paid = max(0, Prev_Balance - Curr_Balance)
    df["Principal_Paid"] = (
        (df["Previous_Balance"] - df["Outstanding_Balance"])
        .apply(lambda x: max(0, x) if pd.notna(x) else 0)
    )

    # Balance_Increase = max(0, Curr_Balance - Prev_Balance)
    df["Balance_Increase"] = (
        (df["Outstanding_Balance"] - df["Previous_Balance"])
        .apply(lambda x: max(0, x) if pd.notna(x) else 0)
    )

    # Fill NaN Previous_Balance with 0 for first month (no prior data)
    df["Previous_Balance"] = df["Previous_Balance"].fillna(0)

    # Reorder columns for output clarity
    output_cols = [
        "Loan_ID",
        "Property_ID",
        "AsOfMonth",
        "Outstanding_Balance",
        "Previous_Balance",
        "Principal_Paid",
        "Balance_Increase",
        "Loan_Event",
    ]

    result = df[output_cols].copy()

    # Write output
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_csv, index=False)

    print(f"✓ Equity buildup computed: {len(result)} records")
    print(f"  Loans tracked: {result['Loan_ID'].nunique()}")
    print(f"  Date range: {result['AsOfMonth'].min()} to {result['AsOfMonth'].max()}")
    print(f"  Total principal paid: ${result['Principal_Paid'].sum():,.2f}")
    print(f"  Output written to: {output_csv}")

    return result


def main() -> None:
    """
    Main entry point for equity module.

    Loads paths from environment variables and computes equity buildup.
    """
    # Load environment from code/.env
    repo_root = Path(__file__).resolve().parents[1]
    code_dir = repo_root / "code"
    load_dotenv(code_dir / ".env")

    # Get paths from environment or use defaults
    default_input = str(repo_root / "inputs" / "loan_balances.csv")
    default_output = str(repo_root / "outputs" / "equity_build_up_monthly.csv")

    input_csv = os.getenv("EQUITY_INPUT_CSV", default_input)
    output_csv = os.getenv("EQUITY_OUTPUT_CSV", default_output)

    print("=" * 60)
    print("EQUITY BUILD-UP MODULE")
    print("=" * 60)
    print(f"Input:  {input_csv}")
    print(f"Output: {output_csv}")
    print()

    try:
        compute_equity_buildup(input_csv, output_csv)
        print("\n✓ Equity module completed successfully")
    except FileNotFoundError as e:
        print(f"\n✗ Error: {e}")
        print("\nTo use equity module, create: inputs/loan_balances.csv")
        print("Required columns: Loan_ID, AsOfMonth, Outstanding_Balance")
        print("Optional columns: Property_ID, Loan_Event")
        raise
    except ValueError as e:
        print(f"\n✗ Validation error: {e}")
        raise


if __name__ == "__main__":
    main()
