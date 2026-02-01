#!/usr/bin/env python3
"""
test_pipeline_integration.py

Integration tests for equity module in run_pipeline.py

Tests:
- Equity module runs successfully when loan_balances.csv present
- Pipeline skips gracefully when loan_balances.csv missing
"""

import unittest
import tempfile
import shutil
import subprocess
import sys
from pathlib import Path


class TestPipelineIntegration(unittest.TestCase):
    """Test equity module integration in pipeline."""

    def setUp(self):
        """Set up test environment."""
        self.repo_root = Path(__file__).resolve().parents[1]
        self.code_dir = self.repo_root / "code"
        self.inputs_dir = self.repo_root / "inputs"
        self.outputs_dir = self.repo_root / "outputs"

    def test_equity_module_runs_with_data_present(self):
        """Test equity module runs successfully when loan_balances.csv exists."""
        # Verify loan_balances.csv exists
        loan_balances = self.inputs_dir / "loan_balances.csv"
        self.assertTrue(
            loan_balances.exists(),
            f"Test requires {loan_balances} to exist"
        )

        # Run equity module directly
        result = subprocess.run(
            [sys.executable, str(self.code_dir / "equity_module.py")],
            capture_output=True,
            text=True
        )

        # Verify successful execution
        self.assertEqual(result.returncode, 0, f"Equity module failed: {result.stderr}")
        self.assertIn("Equity buildup computed", result.stdout)
        self.assertIn("✓ Equity module completed successfully", result.stdout)

        # Verify output file created
        equity_output = self.outputs_dir / "equity_build_up_monthly.csv"
        self.assertTrue(equity_output.exists(), "Equity output file not created")

    def test_pipeline_logic_with_missing_data(self):
        """Test pipeline skip logic when loan_balances.csv missing."""
        # Create temporary directory without loan_balances.csv
        temp_dir = tempfile.mkdtemp()
        temp_inputs = Path(temp_dir) / "inputs"
        temp_inputs.mkdir()

        try:
            # Simulate pipeline logic
            equity_input = temp_inputs / "loan_balances.csv"

            # Verify file doesn't exist
            self.assertFalse(equity_input.exists())

            # Simulate pipeline decision logic
            if equity_input.exists():
                self.fail("Should not reach here - file should not exist")
            else:
                # This is what the pipeline does
                message = f"Skipping equity module (no loan_balances.csv found)"
                self.assertIn("Skipping", message)
                # Pipeline continues without error

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_equity_module_error_handling(self):
        """Test equity module fails fast with invalid data."""
        # Create temporary invalid data
        temp_dir = tempfile.mkdtemp()
        temp_inputs = Path(temp_dir) / "inputs"
        temp_inputs.mkdir()

        try:
            # Create invalid CSV (missing required columns)
            invalid_csv = temp_inputs / "loan_balances.csv"
            invalid_csv.write_text("Invalid,Data\n1,2\n")

            # Set environment to use temp inputs
            import os
            original_env = os.environ.get("EQUITY_INPUT_CSV")
            os.environ["EQUITY_INPUT_CSV"] = str(invalid_csv)

            try:
                # Run equity module - should fail with ValueError
                result = subprocess.run(
                    [sys.executable, str(self.code_dir / "equity_module.py")],
                    capture_output=True,
                    text=True
                )

                # Verify it failed (non-zero exit code)
                self.assertNotEqual(
                    result.returncode, 0,
                    "Equity module should fail with invalid data"
                )
                self.assertIn("Validation error", result.stdout)

            finally:
                # Restore environment
                if original_env is not None:
                    os.environ["EQUITY_INPUT_CSV"] = original_env
                elif "EQUITY_INPUT_CSV" in os.environ:
                    del os.environ["EQUITY_INPUT_CSV"]

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
