#!/usr/bin/env python3
"""
test_equity_module.py

Unit tests for equity_module.py

Tests:
- Valid loan balance processing
- Principal paid calculation
- Balance increase calculation
- Multi-loan tracking
- Edge cases (empty, missing columns, invalid formats)
- File I/O
"""

import unittest
import tempfile
import shutil
from pathlib import Path
import pandas as pd
import sys

# Add code directory to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from equity_module import compute_equity_buildup


class TestEquityModule(unittest.TestCase):
    """Test cases for equity buildup computation."""

    def setUp(self):
        """Create temporary directory for test files."""
        self.test_dir = tempfile.mkdtemp()
        self.input_csv = str(Path(self.test_dir) / "loan_balances.csv")
        self.output_csv = str(Path(self.test_dir) / "equity_output.csv")

    def tearDown(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_basic_principal_payment(self):
        """Test basic principal payment calculation for single loan."""
        # Create test data: loan balance decreasing each month
        data = {
            "Loan_ID": ["L001", "L001", "L001"],
            "AsOfMonth": ["2024-01", "2024-02", "2024-03"],
            "Outstanding_Balance": [100000, 98000, 96000],
        }
        pd.DataFrame(data).to_csv(self.input_csv, index=False)

        result = compute_equity_buildup(self.input_csv, self.output_csv)

        # First month: no previous balance
        self.assertEqual(result.iloc[0]["Principal_Paid"], 0)
        self.assertEqual(result.iloc[0]["Previous_Balance"], 0)

        # Second month: 100000 - 98000 = 2000 principal paid
        self.assertEqual(result.iloc[1]["Principal_Paid"], 2000)
        self.assertEqual(result.iloc[1]["Previous_Balance"], 100000)

        # Third month: 98000 - 96000 = 2000 principal paid
        self.assertEqual(result.iloc[2]["Principal_Paid"], 2000)
        self.assertEqual(result.iloc[2]["Previous_Balance"], 98000)

    def test_balance_increase_refinance(self):
        """Test balance increase detection (refinance/top-up scenario)."""
        # Create test data: loan balance increases
        data = {
            "Loan_ID": ["L001", "L001", "L001"],
            "AsOfMonth": ["2024-01", "2024-02", "2024-03"],
            "Outstanding_Balance": [100000, 150000, 148000],
        }
        pd.DataFrame(data).to_csv(self.input_csv, index=False)

        result = compute_equity_buildup(self.input_csv, self.output_csv)

        # First month: no increase
        self.assertEqual(result.iloc[0]["Balance_Increase"], 0)

        # Second month: balance increased by 50000 (refinance/top-up)
        self.assertEqual(result.iloc[1]["Balance_Increase"], 50000)
        self.assertEqual(result.iloc[1]["Principal_Paid"], 0)  # No principal paid

        # Third month: balance decreased by 2000 (principal paid)
        self.assertEqual(result.iloc[2]["Balance_Increase"], 0)
        self.assertEqual(result.iloc[2]["Principal_Paid"], 2000)

    def test_multiple_loans(self):
        """Test tracking multiple loans independently."""
        data = {
            "Loan_ID": ["L001", "L001", "L002", "L002"],
            "AsOfMonth": ["2024-01", "2024-02", "2024-01", "2024-02"],
            "Outstanding_Balance": [100000, 98000, 200000, 195000],
        }
        pd.DataFrame(data).to_csv(self.input_csv, index=False)

        result = compute_equity_buildup(self.input_csv, self.output_csv)

        # Verify 4 records
        self.assertEqual(len(result), 4)

        # Verify sorting (should be by Loan_ID, AsOfMonth)
        self.assertEqual(result.iloc[0]["Loan_ID"], "L001")
        self.assertEqual(result.iloc[2]["Loan_ID"], "L002")

        # Verify L001 calculations
        l001_month2 = result[(result["Loan_ID"] == "L001") & (result["AsOfMonth"] == "2024-02")].iloc[0]
        self.assertEqual(l001_month2["Principal_Paid"], 2000)

        # Verify L002 calculations
        l002_month2 = result[(result["Loan_ID"] == "L002") & (result["AsOfMonth"] == "2024-02")].iloc[0]
        self.assertEqual(l002_month2["Principal_Paid"], 5000)

    def test_optional_columns(self):
        """Test handling of optional Property_ID and Loan_Event columns."""
        data = {
            "Loan_ID": ["L001", "L001"],
            "AsOfMonth": ["2024-01", "2024-02"],
            "Outstanding_Balance": [100000, 98000],
            "Property_ID": ["P123", "P123"],
            "Loan_Event": ["", "Regular Payment"],
        }
        pd.DataFrame(data).to_csv(self.input_csv, index=False)

        result = compute_equity_buildup(self.input_csv, self.output_csv)

        # Verify optional columns are preserved
        self.assertIn("Property_ID", result.columns)
        self.assertIn("Loan_Event", result.columns)
        self.assertEqual(result.iloc[0]["Property_ID"], "P123")
        self.assertEqual(result.iloc[1]["Loan_Event"], "Regular Payment")

    def test_missing_optional_columns(self):
        """Test that missing optional columns are added with empty values."""
        data = {
            "Loan_ID": ["L001", "L001"],
            "AsOfMonth": ["2024-01", "2024-02"],
            "Outstanding_Balance": [100000, 98000],
        }
        pd.DataFrame(data).to_csv(self.input_csv, index=False)

        result = compute_equity_buildup(self.input_csv, self.output_csv)

        # Verify optional columns are added
        self.assertIn("Property_ID", result.columns)
        self.assertIn("Loan_Event", result.columns)
        self.assertEqual(result.iloc[0]["Property_ID"], "")
        self.assertEqual(result.iloc[0]["Loan_Event"], "")

    def test_file_not_found(self):
        """Test error handling for missing input file."""
        with self.assertRaises(FileNotFoundError) as ctx:
            compute_equity_buildup("nonexistent.csv", self.output_csv)
        self.assertIn("not found", str(ctx.exception))

    def test_missing_required_columns(self):
        """Test error handling for missing required columns."""
        # Missing Outstanding_Balance
        data = {
            "Loan_ID": ["L001"],
            "AsOfMonth": ["2024-01"],
        }
        pd.DataFrame(data).to_csv(self.input_csv, index=False)

        with self.assertRaises(ValueError) as ctx:
            compute_equity_buildup(self.input_csv, self.output_csv)
        self.assertIn("Missing required columns", str(ctx.exception))

    def test_empty_file(self):
        """Test error handling for empty CSV."""
        pd.DataFrame(columns=["Loan_ID", "AsOfMonth", "Outstanding_Balance"]).to_csv(
            self.input_csv, index=False
        )

        with self.assertRaises(ValueError) as ctx:
            compute_equity_buildup(self.input_csv, self.output_csv)
        self.assertIn("empty", str(ctx.exception))

    def test_invalid_month_format(self):
        """Test error handling for invalid AsOfMonth format."""
        data = {
            "Loan_ID": ["L001"],
            "AsOfMonth": ["2024/01"],  # Invalid format (should be YYYY-MM)
            "Outstanding_Balance": [100000],
        }
        pd.DataFrame(data).to_csv(self.input_csv, index=False)

        with self.assertRaises(ValueError) as ctx:
            compute_equity_buildup(self.input_csv, self.output_csv)
        self.assertIn("Invalid AsOfMonth format", str(ctx.exception))

    def test_non_numeric_balance(self):
        """Test error handling for non-numeric Outstanding_Balance."""
        data = {
            "Loan_ID": ["L001"],
            "AsOfMonth": ["2024-01"],
            "Outstanding_Balance": ["abc"],
        }
        pd.DataFrame(data).to_csv(self.input_csv, index=False)

        with self.assertRaises(ValueError) as ctx:
            compute_equity_buildup(self.input_csv, self.output_csv)
        self.assertIn("non-numeric", str(ctx.exception))

    def test_null_loan_id(self):
        """Test error handling for null Loan_ID."""
        data = {
            "Loan_ID": [None, "L001"],
            "AsOfMonth": ["2024-01", "2024-02"],
            "Outstanding_Balance": [100000, 98000],
        }
        pd.DataFrame(data).to_csv(self.input_csv, index=False)

        with self.assertRaises(ValueError) as ctx:
            compute_equity_buildup(self.input_csv, self.output_csv)
        self.assertIn("Loan_ID cannot be null", str(ctx.exception))

    def test_output_file_created(self):
        """Test that output file is created with correct structure."""
        data = {
            "Loan_ID": ["L001"],
            "AsOfMonth": ["2024-01"],
            "Outstanding_Balance": [100000],
        }
        pd.DataFrame(data).to_csv(self.input_csv, index=False)

        compute_equity_buildup(self.input_csv, self.output_csv)

        # Verify output file exists
        self.assertTrue(Path(self.output_csv).exists())

        # Verify output structure
        output_df = pd.read_csv(self.output_csv)
        expected_cols = [
            "Loan_ID",
            "Property_ID",
            "AsOfMonth",
            "Outstanding_Balance",
            "Previous_Balance",
            "Principal_Paid",
            "Balance_Increase",
            "Loan_Event",
        ]
        self.assertEqual(list(output_df.columns), expected_cols)

    def test_deterministic_sorting(self):
        """Test that output is deterministically sorted by Loan_ID, AsOfMonth."""
        # Create unsorted data
        data = {
            "Loan_ID": ["L002", "L001", "L002", "L001"],
            "AsOfMonth": ["2024-02", "2024-02", "2024-01", "2024-01"],
            "Outstanding_Balance": [195000, 98000, 200000, 100000],
        }
        pd.DataFrame(data).to_csv(self.input_csv, index=False)

        result = compute_equity_buildup(self.input_csv, self.output_csv)

        # Verify sorted order
        expected_order = [
            ("L001", "2024-01"),
            ("L001", "2024-02"),
            ("L002", "2024-01"),
            ("L002", "2024-02"),
        ]
        actual_order = list(zip(result["Loan_ID"], result["AsOfMonth"]))
        self.assertEqual(actual_order, expected_order)


if __name__ == "__main__":
    unittest.main(verbosity=2)
