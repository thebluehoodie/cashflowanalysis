import os
import pandas as pd
from dotenv import load_dotenv
from pathlib import Path
from .config import build_settings, Settings

REQUIRED_COLS = {
    "Date", "Amount", "Intent_L1", "Intent_L2",
    "Counterparty_Norm", "Description"
}

def load_settings(input_csv=None, output_dir=None) -> Settings:
    load_dotenv()
    input_csv = input_csv or os.getenv("ANALYSIS_INPUT_CSV")
    output_dir = output_dir or os.getenv("ANALYSIS_OUTPUT_DIR")
    if not input_csv or not output_dir:
        raise ValueError("ANALYSIS_INPUT_CSV and ANALYSIS_OUTPUT_DIR must be provided")
    return build_settings(input_csv, output_dir)

def ensure_dirs(s: Settings):
    s.output_dir.mkdir(parents=True, exist_ok=True)
    s.charts_dir.mkdir(parents=True, exist_ok=True)
    s.tables_dir.mkdir(parents=True, exist_ok=True)

def load_ledger(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)
    df = df.dropna(subset=["Date"])
    return df
