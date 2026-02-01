#!/usr/bin/env python3
"""
test_skip_equity.py

Simulates the pipeline's equity module skip logic.
"""

import sys
from pathlib import Path


def simulate_pipeline_equity_check():
    """Simulate the equity module check from run_pipeline.py."""
    repo_root = Path(__file__).resolve().parents[1]
    equity_input = repo_root / "inputs" / "loan_balances.csv"

    print("=" * 60)
    print("SIMULATING PIPELINE EQUITY MODULE CHECK")
    print("=" * 60)
    print(f"Checking for: {equity_input}")
    print(f"File exists: {equity_input.exists()}")
    print()

    if equity_input.exists():
        print("→ Running equity build-up module (found loan_balances.csv)...")
        print("  [Equity module would run here]")
        return True
    else:
        print("→ Skipping equity module (no loan_balances.csv found)")
        print(f"  To enable equity analytics, create: {equity_input}")
        print("  Required columns: Loan_ID, AsOfMonth, Outstanding_Balance")
        return False


if __name__ == "__main__":
    result = simulate_pipeline_equity_check()
    print()
    print(f"Pipeline would continue: {'✓' if not result or result else '✓'}")
    print("(Equity module is optional - pipeline continues either way)")
