# Data Contract (Pipeline)

Scope
- Code: code/clean_bank_statement.py, code/auto_classify_transactions.py, code/dashboard_app.py
- Artifacts (schema only): combined_cleaned.csv, classified_transactions_v3.csv, overrides.xlsx
- Purpose: audit-grade documentation of current behavior only

## Pipeline overview (Stage 1 -> Stage 2 -> Stage 3)
Stage 1 - Clean bank statements
- Script: code/clean_bank_statement.py
- Input: raw bank statement CSVs from Tabula with columns Date, Description, Withdrawals, Deposits, Balance
- Output: cleaned_<original>.csv, combined_cleaned.csv, reconciliation_report.csv

Stage 2 - Classify transactions
- Script: code/auto_classify_transactions.py
- Input: combined_cleaned.csv (or similar schema)
- Output: classified_transactions_v3.csv

Stage 3 - Analytics / dashboard
- Script: code/dashboard_app.py
- Input: classified_transactions_v3.csv (or compatible schema)
- Output: Dash UI (no CSV output)

## Produced CSVs (purpose and schema)

### cleaned_<original>.csv (Stage 1)
Purpose
- Cleaned, transaction-level output for a single source CSV.

Required columns (always produced)
- Date
- YearMonth
- Description
- Amount
- Balance
- Withdrawals
- Deposits
- RowsMerged
- SourceFile
- RowOrder
- Txn_ID

Optional columns
- NONE (output schema is fixed)

### combined_cleaned.csv (Stage 1)
Purpose
- Union of all cleaned_<original>.csv outputs.

Required columns (from file inspection)
| Column | Description |
|---|---|
| Date | Parsed date or NaT (from source Date + inferred year) |
| YearMonth | YYYY-MM from Date or filename fallback |
| Description | Collapsed description text |
| Amount | Deposits minus Withdrawals (float) |
| Balance | Parsed balance (float) |
| Withdrawals | Parsed withdrawals (float) |
| Deposits | Parsed deposits (float) |
| RowsMerged | Count of raw rows merged into this transaction |
| SourceFile | Source filename |
| RowOrder | Original row order (file order) |
| Txn_ID | Stable SHA-1 identifier |

Optional columns
- NONE (output schema is fixed)

### reconciliation_report.csv (Stage 1)
Purpose
- Per SourceFile + YearMonth reconciliation summary.

Required columns (from code)
- SourceFile
- YearMonth
- OpeningBalance
- SumAmount
- ClosingBalance
- Delta
- OK

Optional columns
- NONE (output schema is fixed)

### classified_transactions_v3.csv (Stage 2)
Purpose
- Classified transaction ledger with economic, managerial, and audit fields.

Required columns (from file inspection)
| Column | Description |
|---|---|
| Date | Transaction date |
| YearMonth | YYYY-MM bucket |
| Description | Raw description |
| Amount | Signed amount |
| Balance | Balance from statement (if present) |
| Withdrawals | Withdrawals from statement (if present) |
| Deposits | Deposits from statement (if present) |
| RowsMerged | Rows merged during cleaning |
| SourceFile | Source filename |
| RowOrder | Original row order |
| Txn_ID | Stable SHA-1 identifier |
| Record_Type | TRANSACTION or SUMMARY |
| Flow_Nature | INCOME, EXPENSE, TRANSFER, NON-CASH |
| Cashflow_Statement | OPERATING, INVESTING, FINANCING, TRANSFER, NON-CASH |
| Economic_Purpose_L1 | Economic category (L1) |
| Economic_Purpose_L2 | Economic category (L2) |
| Asset_Context | GENERAL, PROPERTY, CAR, FINANCIAL, UNKNOWN |
| Stability_Class | STRUCTURAL_RECURRING, SEMI_RECURRING, VARIABLE, ONE_OFF |
| Baseline_Eligible | Boolean |
| Event_Tag | NONE, RENOVATION, PROPERTY_ACQ, TAX_EVENT |
| Bank_Rail | GIRO, FAST, PAYNOW, NETS, ATM, CHEQUE, CARD, OTHER |
| Rule_ID | Rule identifier (R00...R16) |
| Rule_Explanation | Rule rationale |
| Managerial_Purpose_L1 | Managerial category (L1) |
| Managerial_Purpose_L2 | Managerial category (L2) |
| Is_CC_Settlement | Boolean |
| Cashflow_Section | Alias of Cashflow_Statement |
| Category_L1 | Alias of Economic_Purpose_L1 |
| Category_L2 | Alias of Economic_Purpose_L2 |
| Instrument | Alias of Bank_Rail |
| Counterparty_Norm | Uppercase description |
| Counterparty_Core | First 80 chars of Counterparty_Norm |
| Was_Overridden | Boolean |
| Override_ID_Applied | Override row ID |
| Override_Reason | Override rationale (appended) |

Optional columns
- Any extra columns present in the input CSV are preserved by auto_classify_transactions.py.

### overrides.xlsx (Stage 2 - schema only)
Purpose
- Manual per-Txn_ID overrides for classification outputs.

Sheet: Overrides
Required columns (enforced in code)
- Txn_ID
- Cashflow_Statement
- Economic_Purpose_L1
- Economic_Purpose_L2
- Managerial_Purpose_L1
- Managerial_Purpose_L2
- Baseline_Eligible
- Override_Reason
- Enabled

Optional columns
- NONE (missing columns are added as blanks by code)

Sheet: Column_Desc
- UNKNOWN (present in file; not referenced in code)

## Allowed values for key fields

Cashflow_Section (also Cashflow_Statement)
- OPERATING
- INVESTING
- FINANCING
- TRANSFER
- NON-CASH

Record_Type
- TRANSACTION
- SUMMARY

Flow_Nature
- INCOME
- EXPENSE
- TRANSFER
- NON-CASH

## Data invariants enforced by code (explicit)

Stage 1 - clean_bank_statement.py
- Input CSVs must contain columns: Date, Description, Withdrawals, Deposits, Balance.
- Repeated header rows, currency header rows, and fully blank rows are removed.
- Amount is computed as Deposits minus Withdrawals (never left NaN when one side exists).
- YearMonth is derived from parsed Date, else from filename year-month if available.
- Txn_ID must be non-blank; blank Txn_IDs raise ValueError.
- Txn_ID must be unique per output file; duplicates raise ValueError.
- If base_key + row_fingerprint duplicates exist (indistinguishable rows), raise ValueError.
- combined_cleaned.csv is checked for duplicate Txn_IDs; duplicates raise ValueError.

Stage 2 - auto_classify_transactions.py
- Amount must exist; missing Amount raises ValueError.
- Txn_ID must be present and unique; blanks or duplicates raise ValueError.
- Overrides: duplicate Txn_ID rows in overrides.xlsx raise ValueError.
- Overrides: only Enabled == TRUE/1/YES/Y rows are applied.

Stage 3 - dashboard_app.py
- Required columns: Amount, Cashflow_Section or Cashflow_Statement, and Date or YearMonth.
- Unrecognized Cashflow_Section values cause a hard failure.
- Invalid boolean values for Is_CC_Settlement or Baseline_Eligible raise ValueError.

## Exclusions from analytics (default behavior)
- SUMMARY rows are excluded when the dashboard filter "Exclude Balance B/F (Summary)" is checked (default on).
  - Summary flag is True if Record_Type == SUMMARY or Category_L2 == BALANCE_BF.
- TRANSFER rows are excluded when "Exclude Transfers" is checked (default on).
- NON-CASH rows are excluded unless "Include NON-CASH section" is checked (default off).

UNKNOWN
- Any exclusions outside of the dashboard filters above are not implemented in the current codebase.
