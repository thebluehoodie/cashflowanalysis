# README_run_windows.md

## Prerequisites
- Python 3.9+ installed and available as `python`
- Install pandas:
  - `pip install pandas`

## Command Prompt command (Windows)
```bat
python clean_bank_statement.py --input_dir "C:\Users\Weilun\OneDrive\Documents\cashflowanalysis\cashflow analysis\UOB\CSV" --output_dir "C:\Users\Weilun\OneDrive\Documents\cashflowanalysis\cashflow analysis\UOB\CSV\output"
```

## Output files
- `cleaned_<original>.csv` for each input CSV file
- `combined_cleaned.csv` for all files combined
- `reconciliation_report.csv` with opening + sum(amount) vs closing per month
