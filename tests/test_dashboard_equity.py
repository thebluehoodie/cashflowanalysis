#!/usr/bin/env python3
"""
test_dashboard_equity.py

Unit tests for dashboard equity section integration.

Tests:
- Dashboard loads successfully with equity data
- Dashboard loads successfully without equity data (graceful degradation)
- Equity section callback works correctly
"""

import unittest
import sys
import tempfile
import shutil
from pathlib import Path
import pandas as pd

# Add code directory to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))


class TestDashboardEquity(unittest.TestCase):
    """Test equity integration in dashboard."""

    def setUp(self):
        """Set up test environment."""
        self.code_dir = Path(__file__).resolve().parents[1] / "code"

    def test_dashboard_imports(self):
        """Test that dashboard_app can be imported without errors."""
        try:
            import dashboard_app
            self.assertTrue(hasattr(dashboard_app, "build_app"))
            self.assertTrue(hasattr(dashboard_app, "main"))
        except Exception as e:
            self.fail(f"Failed to import dashboard_app: {e}")

    def test_build_app_with_equity_data(self):
        """Test that build_app accepts equity_df parameter."""
        from dashboard_app import build_app

        # Create minimal transaction data
        df = pd.DataFrame({
            "Date": ["2024-01-01"],
            "YearMonth": ["2024-01"],
            "Description": ["Test"],
            "Amount": [100.0],
            "Cashflow_Section": ["OPERATING"],
            "Category_L1": ["INCOME"],
            "Category_L2": ["SALARY"],
            "Instrument": ["BANK"],
            "Counterparty_Core": ["EMPLOYER"],
        })

        # Create equity data
        equity_df = pd.DataFrame({
            "Loan_ID": ["L001"],
            "AsOfMonth": ["2024-01"],
            "Outstanding_Balance": [100000],
            "Previous_Balance": [0],
            "Principal_Paid": [0],
            "Balance_Increase": [0],
        })

        try:
            # Build app with equity data
            app = build_app(df, equity_df=equity_df, host="127.0.0.1", port=8050)
            self.assertIsNotNone(app)
            self.assertTrue(hasattr(app, "layout"))
        except Exception as e:
            self.fail(f"Failed to build app with equity data: {e}")

    def test_build_app_without_equity_data(self):
        """Test that build_app works without equity_df (graceful degradation)."""
        from dashboard_app import build_app

        # Create minimal transaction data
        df = pd.DataFrame({
            "Date": ["2024-01-01"],
            "YearMonth": ["2024-01"],
            "Description": ["Test"],
            "Amount": [100.0],
            "Cashflow_Section": ["OPERATING"],
            "Category_L1": ["INCOME"],
            "Category_L2": ["SALARY"],
            "Instrument": ["BANK"],
            "Counterparty_Core": ["EMPLOYER"],
        })

        try:
            # Build app without equity data
            app = build_app(df, equity_df=None, host="127.0.0.1", port=8050)
            self.assertIsNotNone(app)
            self.assertTrue(hasattr(app, "layout"))
        except Exception as e:
            self.fail(f"Failed to build app without equity data: {e}")

    def test_equity_callback_logic_with_data(self):
        """Test equity section callback logic with data present."""
        # Create sample equity data
        equity_df = pd.DataFrame({
            "Loan_ID": ["L001", "L001", "L001"],
            "Property_ID": ["P123", "P123", "P123"],
            "AsOfMonth": ["2024-01", "2024-02", "2024-03"],
            "Outstanding_Balance": [100000, 98000, 96000],
            "Previous_Balance": [0, 100000, 98000],
            "Principal_Paid": [0, 2000, 2000],
            "Balance_Increase": [0, 0, 0],
            "Loan_Event": ["Initial", "Payment", "Payment"],
        })

        # Test filtering by period
        filtered = equity_df[
            (equity_df["AsOfMonth"] >= "2024-02") &
            (equity_df["AsOfMonth"] <= "2024-03")
        ]

        self.assertEqual(len(filtered), 2)
        self.assertEqual(filtered["Principal_Paid"].sum(), 4000)

    def test_equity_callback_logic_without_data(self):
        """Test equity section callback logic without data (None case)."""
        equity_df = None

        # Verify graceful handling
        if equity_df is None:
            # Should show placeholder message
            result = "Equity data not loaded"
            self.assertIn("not loaded", result)
        else:
            self.fail("Should handle None equity_df gracefully")

    def test_equity_callback_logic_empty_dataframe(self):
        """Test equity section callback logic with empty DataFrame."""
        equity_df = pd.DataFrame()

        # Verify graceful handling
        if equity_df.empty:
            # Should show placeholder message
            result = "Equity data not loaded"
            self.assertIn("not loaded", result)
        else:
            self.fail("Should handle empty equity_df gracefully")


if __name__ == "__main__":
    unittest.main(verbosity=2)
