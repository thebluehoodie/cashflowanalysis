# Runbook

Scope
- Stage 1: code/clean_bank_statement.py
- Stage 2: code/auto_classify_transactions.py
- Stage 3: code/dashboard_app.py

## Stage 1 - Clean bank statements
Command (uses code/.env automatically)
```
python code/clean_bank_statement.py
```

Command (explicit paths)
```
python code/clean_bank_statement.py --input_dir "C:\path\to\csvs" --output_dir "C:\path\to\output"
```

Outputs
- cleaned_<original>.csv (per input file)
- combined_cleaned.csv
- reconciliation_report.csv

## Stage 2 - Classify transactions
Command (run from code folder so .env is loaded)
```
cd code
python auto_classify_transactions.py
```

Alternate (run from repo root with env vars set)
```
set CLASSIFY_INPUT_CSV=C:\path\to\combined_cleaned.csv
set CLASSIFY_OUTPUT_DIR=C:\path\to\output
python code\auto_classify_transactions.py
```

Output
- classified_transactions_v3.csv

## Stage 3 - Dashboard
Command
```
python code/dashboard_app.py
```

Notes
- The Dash app binds to DASH_HOST and DASH_PORT if set; defaults are 127.0.0.1:8050.

## Required environment variables
| Variable | Used by | Required | Purpose |
|---|---|---:|---|
| CLEAN_INPUT_DIR | clean_bank_statement.py | Yes (unless --input_dir used) | Directory of raw CSVs |
| CLEAN_OUTPUT_DIR | clean_bank_statement.py | Yes (unless --output_dir used) | Output directory |
| CLASSIFY_INPUT_CSV | auto_classify_transactions.py | Yes | Path to combined_cleaned.csv |
| CLASSIFY_OUTPUT_DIR | auto_classify_transactions.py | Yes | Output directory |
| ANALYSIS_INPUT_CSV | dashboard_app.py | Yes | Path to classified_transactions_v3.csv |
| CLASSIFY_OVERRIDE_XLSX | auto_classify_transactions.py | Optional | Full path to overrides.xlsx |
| CLASSIFY_OVERRIDE_DIR | auto_classify_transactions.py | Optional | Directory containing overrides.xlsx |
| DASH_HOST | dashboard_app.py | Optional | Dash host |
| DASH_PORT | dashboard_app.py | Optional | Dash port |

UNKNOWN
- ANALYSIS_OUTPUT_DIR is defined in code/.env but not used by dashboard_app.py.

## Typical Windows paths (from code/.env)
- CLEAN_INPUT_DIR=C:\Users\Weilun\OneDrive\Documents\cashflowanalysis\cashflow analysis\UOB\CSV
- CLEAN_OUTPUT_DIR=C:\Users\Weilun\OneDrive\Documents\cashflowanalysis\cashflow analysis\UOB\CSV\output
- CLASSIFY_INPUT_CSV=C:\Users\Weilun\OneDrive\Documents\cashflowanalysis\cashflow analysis\UOB\CSV\output\combined_cleaned.csv
- CLASSIFY_OUTPUT_DIR=C:\Users\Weilun\OneDrive\Documents\cashflowanalysis\cashflow analysis\UOB\CSV\output
- ANALYSIS_INPUT_CSV=C:\Users\Weilun\OneDrive\Documents\cashflowanalysis\cashflow analysis\UOB\CSV\output\classified_transactions_v3.csv
- ANALYSIS_OUTPUT_DIR=C:\Users\Weilun\OneDrive\Documents\cashflowanalysis\cashflow analysis\UOB\CSV\output\analysis
- CLASSIFY_OVERRIDE_DIR=C:\Users\Weilun\OneDrive\Documents\cashflowanalysis\data\overrides

## Common errors encountered
- clean_bank_statement.py
  - Missing output_dir. Provide --output_dir or set CLEAN_OUTPUT_DIR in .env.
  - Missing input_dir. Provide --input_dir or set CLEAN_INPUT_DIR in .env.
  - Input directory does not exist: <path>
  - No CSV files found to process.
  - Missing expected columns: [Date, Description, Withdrawals, Deposits, Balance]
  - FATAL: Blank Txn_IDs detected
  - FATAL: Txn_ID uniqueness violation
  - FATAL: Indistinguishable duplicate transactions detected

- auto_classify_transactions.py
  - Check CLASSIFY_INPUT_CSV and CLASSIFY_OUTPUT_DIR in .env
  - Duplicate Txn_ID in overrides.xlsx (must be unique)
  - Missing Amount for Txn_ID / Invalid Amount for Txn_ID
  - FATAL: Blank Txn_IDs detected
  - FATAL: Txn_ID uniqueness violation

- dashboard_app.py
  - ANALYSIS_INPUT_CSV env var is required (path to classified_transactions_v3.csv)
  - Data contract failure: missing required fields [...]
  - Data contract failure: unrecognized Cashflow_Section values [...]
  - Invalid boolean value: <value>

- dash run_server obsolete
  - UNKNOWN. The codebase does not reference run_server; dashboard_app.py uses app.run(...).

## Troubleshooting steps
1) Verify .env location
   - clean_bank_statement.py loads code/.env (next to the script).
   - auto_classify_transactions.py loads .env from the current working directory only.
   - dashboard_app.py loads .env from the current working directory or next to the script.
2) Verify input paths exist
   - CLEAN_INPUT_DIR exists and contains .csv files.
   - CLASSIFY_INPUT_CSV exists and is readable.
   - ANALYSIS_INPUT_CSV exists and is readable.
3) Validate input schema
   - Stage 1 input CSVs must have Date, Description, Withdrawals, Deposits, Balance.
4) Resolve Txn_ID failures
   - Remove duplicate rows that are identical on all content fields.
   - Ensure Amount, YearMonth, SourceFile are populated before Txn_ID generation.
5) Overrides hygiene
   - Ensure overrides.xlsx has unique Txn_ID values in the Overrides sheet.
   - Ensure Enabled column is TRUE/1/YES/Y for rows that should apply.
