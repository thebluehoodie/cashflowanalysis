#!/usr/bin/env python3
"""
run_pipeline.py

Single entrypoint to run:
Stage 1 (clean) -> Stage 2 (classify) -> diagnostics -> optional dashboard.

Design:
- No refactor of existing scripts.
- Uses .env as the single source of truth where possible.
- Uses subprocess for Stage 1 (argparse-based).
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

from dotenv import load_dotenv


def _run(cmd: list[str]) -> None:
    print("\nRUN:", " ".join(cmd))
    subprocess.check_call(cmd)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    code_dir = repo_root / "code"

    load_dotenv(code_dir / ".env")

    # ---- Stage 1: Clean bank statements (argparse-based) ----
    clean_in = os.getenv("CLEAN_INPUT_DIR")
    clean_out = os.getenv("CLEAN_OUTPUT_DIR")
    if not clean_in or not clean_out:
        raise ValueError("Missing CLEAN_INPUT_DIR / CLEAN_OUTPUT_DIR in code/.env")

    _run([
        sys.executable, str(code_dir / "clean_bank_statement.py"),
        "--input_dir", clean_in,
        "--output_dir", clean_out,
    ])

    # ---- Stage 2: Classify (env-based) ----
    # Ensure Stage 2 input points at Stage 1 combined output
    combined_cleaned = Path(clean_out) / "combined_cleaned.csv"
    if not combined_cleaned.exists():
        raise FileNotFoundError(f"Expected {combined_cleaned} from Stage 1 but not found.")

    # If your .env already points correctly, this is redundant but safe.
    os.environ["CLASSIFY_INPUT_CSV"] = str(combined_cleaned)

    _run([sys.executable, str(code_dir / "auto_classify_transactions.py")])

    # ---- Diagnostics (requires CLI args) ----
    classify_output_dir = os.getenv("CLASSIFY_OUTPUT_DIR", clean_out)
    classified_csv = Path(classify_output_dir) / "classified_transactions_v3.csv"
    diagnostics_dir = repo_root / "diagnostics"

    _run([
        sys.executable, str(code_dir / "classification_diagnostics.py"),
        "--input", str(classified_csv),
        "--output-dir", str(diagnostics_dir),
    ])

    # ---- Optional: Dashboard (blocking server) ----
    # Set RUN_DASH=1 if you want to launch it at end.
    if os.getenv("RUN_DASH") == "1":
        _run([sys.executable, str(code_dir / "dashboard_app.py")])

    print("\nPipeline complete.")


if __name__ == "__main__":
    main()
