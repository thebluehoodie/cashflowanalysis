# Cashflow Pipeline Technical Documentation

## Overview
This document describes the current, production behavior of the classification pipeline and Dash dashboard as implemented in:
- `code/auto_classify_transactions.py`
- `code/dashboard_app.py`

It is documentation-only and reflects existing logic. No refactors or behavioral changes are assumed.

---

## auto_classify_transactions.py

### 1. Purpose & System Role
- Purpose: Deterministic, explainable transaction classifier for FP&A-grade personal cashflow analysis, with audit-friendly overrides.
- Pipeline role: Consumes cleaned transaction CSVs, assigns economic and managerial taxonomy, applies overrides, writes a classified CSV.

**Input ? Processing ? Output (ASCII flow)**
```
[CLASSIFY_INPUT_CSV]
        |
        v
   pd.read_csv
        |
        v
  ensure_txn_id
        |
        v
  classify_row (rule priority)
        |
        v
  derive columns + back-compat columns
        |
        v
  load_overrides (.xlsx)
        |
        v
  apply_overrides (Txn_ID)
        |
        v
[CLASSIFY_OUTPUT_DIR]/classified_transactions_v3.csv
```

### 2. Entry Points & Configuration
**Entry point**
- `main()`
  - Loads `.env` using `load_dotenv()`.
  - If `RUN_SELF_CHECKS == "1"`, runs internal self-checks and exits early.
  - Reads input CSV, classifies, writes output CSV.

**Environment variables**
- Required:
  - `CLASSIFY_INPUT_CSV`: path to classified input CSV.
  - `CLASSIFY_OUTPUT_DIR`: output directory for classified CSV.
- Optional overrides:
  - `CLASSIFY_OVERRIDE_XLSX`: absolute path to overrides.xlsx.
  - `CLASSIFY_OVERRIDE_DIR`: directory containing overrides.xlsx.
- Optional test mode:
  - `RUN_SELF_CHECKS=1`: runs `_self_check()` and exits.

**Runtime assumptions**
- Python 3.x
- pandas
- python-dotenv
- openpyxl or equivalent Excel reader via pandas for overrides.xlsx

### 3. Data Pipeline Walkthrough (Step-by-Step)
1. **Read input CSV** from `CLASSIFY_INPUT_CSV`.
2. **Amount coercion**: `Amount` is required; coerced to numeric with `errors="coerce"` and filled with 0.0.
3. **Description handling**: `Description` is coerced to string if present; otherwise created as empty string.
4. **Txn_ID creation**:
   - If missing or blank per row, a stable SHA1 hash is generated from: `Date`, `YearMonth`, `Amount`, `Description`, `SourceFile`, `RowOrder`.
   - Final assertion ensures no blanks remain.
5. **Classification** via `classify_row(desc, amount)` in strict priority order.
6. **Column expansion**: dataclass fields are expanded into columns.
7. **Backward-compatible columns** added: `Cashflow_Section`, `Category_L1`, `Category_L2`, `Instrument`.
8. **Counterparty normalization**: `Counterparty_Norm` and `Counterparty_Core` (80-char prefix).
9. **Overrides**:
   - `load_overrides()` loads Excel rows keyed by `Txn_ID` and pre-derives managerial columns when econ overrides are present.
   - `apply_overrides()` applies non-blank override fields and performs last-mile managerial derivation only if needed.
10. **Write output CSV** to `CLASSIFY_OUTPUT_DIR/classified_transactions_v3.csv`.

### 4. Schema Contract (Output Columns)
The classifier preserves input columns and adds/overwrites the following columns. Required by dashboard are noted.

**Core output columns**
| Column | Meaning | Derived | Required by dashboard | Notes |
|---|---|---:|---:|---|
| `Record_Type` | TRANSACTION or SUMMARY | Yes | Yes | From rule result |
| `Flow_Nature` | INCOME/EXPENSE/TRANSFER/NON-CASH | Yes | No | From rule result |
| `Cashflow_Statement` | OPERATING/INVESTING/FINANCING/TRANSFER/NON-CASH | Yes | Yes | Primary dashboard section |
| `Economic_Purpose_L1` | Economic category (L1) | Yes | Yes | Used as Category_L1 alias |
| `Economic_Purpose_L2` | Economic category (L2) | Yes | Yes | Used as Category_L2 alias |
| `Asset_Context` | GENERAL/PROPERTY/CAR/FINANCIAL/UNKNOWN | Yes | No | Rule result |
| `Stability_Class` | STRUCTURAL/SEMI/VARIABLE/ONE_OFF | Yes | No | Rule result |
| `Baseline_Eligible` | Eligible for baseline KPIs | Yes | No | Rule result or overrides |
| `Event_Tag` | NONE/RENOVATION/PROPERTY_ACQ/TAX_EVENT | Yes | No | Rule result |
| `Bank_Rail` | GIRO/FAST/PAYNOW/NETS/ATM/CHEQUE/CARD/OTHER | Yes | Yes | Used as Instrument alias |
| `Rule_ID` | Rule identifier | Yes | Yes | Audit field |
| `Rule_Explanation` | Rule rationale | Yes | No | Audit field |
| `Managerial_Purpose_L1` | Managerial category (L1) | Yes | No | Derived or overridden |
| `Managerial_Purpose_L2` | Managerial category (L2) | Yes | No | Derived or overridden |
| `Is_CC_Settlement` | Credit card settlement flag | Yes | No | From rule result |
| `Cashflow_Section` | Back-compat alias of Cashflow_Statement | Yes | Yes | Used by dashboard |
| `Category_L1` | Back-compat alias of Economic_Purpose_L1 | Yes | Yes | Used by dashboard |
| `Category_L2` | Back-compat alias of Economic_Purpose_L2 | Yes | Yes | Used by dashboard |
| `Instrument` | Back-compat alias of Bank_Rail | Yes | Yes | Used by dashboard |
| `Counterparty_Norm` | Uppercase description | Yes | No | Derived |
| `Counterparty_Core` | First 80 chars of Counterparty_Norm | Yes | Yes | Dashboard drill |
| `Was_Overridden` | Override applied flag | Yes | Yes | Audit field |
| `Override_ID_Applied` | Override row ID | Yes | Yes | Audit field |
| `Override_Reason` | Override rationale | Yes | Yes | Appended, audit-safe |

**Required input columns**
- `Amount` (required)
- `Description` (optional; created if missing)
- `Date`, `YearMonth`, `SourceFile`, `RowOrder` (used for Txn_ID hash if present)

### 5. Classification Rules & Priority
Rules are evaluated in order; first match wins.

**Rule table (priority order)**
| Priority | Rule_ID | Trigger | Flow_Nature | Cashflow_Statement | Economic L1/L2 | Managerial L1/L2 | Baseline | Rationale |
|---:|---|---|---|---|---|---|---:|---|
| 0 | R00_BALANCE_BF | Description matches BALANCE B/F | NON-CASH | NON-CASH | NON-CASH / BALANCE_BF | NON-CASH / BALANCE_BF | False | Balance summary line |
| 1 | R01_SALARY | Amount>0 and salary tokens/employer | INCOME | OPERATING | INCOME / SALARY | INCOME / SALARY | True | Salary is protected from lifestyle |
| 2 | R02_INTEREST | Amount>0 and interest patterns | INCOME | OPERATING | INCOME / INTEREST | INCOME / INTEREST | True | Bank interest income |
| 3 | R03_TRUST_INTERNAL | Trust Bank OTHR transfer patterns | TRANSFER | TRANSFER | TRANSFER / INTERNAL_TRANSFER | TRANSFER / INTERNAL_TRANSFER | False | Always internal reallocation |
| 4 | R04_PROPERTY_DOWNPAYMENT | Amount<0 and cheque/DR CO charges | EXPENSE | INVESTING | HOUSING / PROPERTY_PURCHASE | HOUSING / PROPERTY_PURCHASE | False | Property downpayment |
| 5 | R05_TAX | Amount<0 and tax patterns | EXPENSE | OPERATING | TAXES / IRAS_TAX | TAXES / IRAS_TAX | True | Tax payments |
| 6 | R06_MORTGAGE | Amount<0 and mortgage patterns | EXPENSE | FINANCING | DEBT_SERVICE / MORTGAGE_PAYMENT | DEBT_SERVICE / MORTGAGE_PAYMENT | True | Debt service |
| 7 | R07_CAR_LOAN | Amount<0 and car finance patterns | EXPENSE | FINANCING | DEBT_SERVICE / CAR_LOAN_PAYMENT | DEBT_SERVICE / CAR_LOAN_PAYMENT | True | Debt service |
| 8 | R08_RENOVATION | Amount<0 and renovation patterns | EXPENSE | INVESTING | HOUSING / RENOVATION | HOUSING / RENOVATION | False | Capex improvement |
| 9 | R09_MCST | Amount<0 and MCST patterns | EXPENSE | OPERATING | HOUSING / HOA_CONDO_FEES | HOUSING / HOA_CONDO_FEES | True | Operating housing cost |
| 10a | R10_INS_IN | Insurer token + inflow markers | INCOME | OPERATING | INCOME / INSURANCE_PAYOUT | INCOME / INSURANCE_PAYOUT | False | Insurance payout |
| 10b | R11_INS_OUT | Insurer token + amount<0 | EXPENSE | OPERATING | INSURANCE / PREMIUM | INSURANCE / PREMIUM | True | Insurance premium |
| 11 | R12_CC_SETTLEMENT | Amount<0 + issuer + CC tokens | EXPENSE | FINANCING | DEBT_SERVICE / CREDIT_CARD_SETTLEMENT_* | LIFESTYLE / CREDIT_CARD_SPEND_PROXY | True | Liability repayment (proxy for spend) |
| 12 | R13_INTERNAL_TRANSFER | SELF_ENTITIES + transfer/rail | TRANSFER | TRANSFER | TRANSFER / INTERNAL_TRANSFER | TRANSFER / INTERNAL_TRANSFER | False | Self-controlled transfer (any sign) |
| 13 | R14_OTHER_INCOME | Amount>0 fallback | INCOME | OPERATING | INCOME / OTHER_INCOME | INCOME / OTHER_INCOME | False | Conservative inflow fallback |
| 14 | R15_GENERIC_OUTFLOW | Amount<0 fallback | EXPENSE | OPERATING | LIFESTYLE / DISCRETIONARY | LIFESTYLE / DISCRETIONARY | False | Conservative outflow fallback |
| 15 | R16_ZERO_ADJ | Amount==0 fallback | NON-CASH | NON-CASH | NON-CASH / ACCOUNTING_ADJUSTMENT | NON-CASH / ACCOUNTING_ADJUSTMENT | False | Zero-amount adjustment |

**Overlaps / shadowing**
- Salary and interest rules are evaluated before transfer logic, preserving income precedence.
- Trust Bank internal transfers (R03) are evaluated before generic internal transfer rule (R13).
- Credit card settlement (R12) is evaluated before generic transfers and generic outflows.

### 6. Overrides Logic Deep Dive
**Overrides file**
- File: `overrides.xlsx`
- Sheet: `Overrides`
- Key: `Txn_ID`
- Required columns are enforced; missing columns are added as empty.
- Duplicate `Txn_ID` rows raise a ValueError.
- Only rows with `Enabled == TRUE/1/YES/Y` are used.

**Override columns (must exist)**
`Txn_ID`, `Cashflow_Statement`, `Economic_Purpose_L1`, `Economic_Purpose_L2`,
`Managerial_Purpose_L1`, `Managerial_Purpose_L2`, `Baseline_Eligible`, `Override_Reason`, `Enabled`

**Partial override behavior**
- Each override field is applied only if the cell is non-blank (not NA, not "", not BLANK/(BLANK)).
- Overrides apply column-by-column; no row is wiped.
- `Baseline_Eligible`: blank leaves the classifier output; TRUE/FALSE overwrites.

**Managerial derivation**
- In `load_overrides()`:
  - Economic and managerial fields are normalized to uppercase/trim.
  - If econ L1/L2 is provided and managerial is blank, managerial is derived using:
    - Credit card prefix: DEBT_SERVICE + CREDIT_CARD_SETTLEMENT* ? (LIFESTYLE, CREDIT_CARD_SPEND_PROXY)
    - Else MANAGERIAL_DERIVE_MAP, fallback to econ pair.
- In `apply_overrides()`:
  - After applying overrides, last-mile derivation occurs only if managerial is still missing.
  - Transfer short-circuit: if final Cashflow_Statement == TRANSFER, managerial is (TRANSFER, INTERNAL_TRANSFER).
  - If override explicitly provides L1 or L2, those values are not overwritten.

**Audit fields**
- `Was_Overridden` is True only for rows that match an override.
- `Override_ID_Applied` is assigned from override row order.
- `Override_Reason` appends new reason to existing with separator `" | "`.

### 7. Dashboard Consumption Logic
The classifier output is designed to be compatible with the dashboard via:
- `Cashflow_Section` ? `Cashflow_Statement`
- `Category_L1` ? `Economic_Purpose_L1`
- `Category_L2` ? `Economic_Purpose_L2`
- `Instrument` ? `Bank_Rail`
- `Counterparty_Core` for drilldowns

---

## dashboard_app.py

### 1. Purpose & System Role
- Purpose: Dash dashboard for personal FP&A cashflow analytics.
- Pipeline role: Consumes classified CSV output (or compatible schema) and provides filtering, KPI reporting, and charts.

**Input ? Processing ? Output (ASCII flow)**
```
[ANALYSIS_INPUT_CSV]
        |
        v
   pd.read_csv
        |
        v
  harmonize_schema
        |
        v
     build_app
        |
        v
  Dash web UI (charts + table)
```

### 2. Entry Points & Configuration
**Entry point**
- `main()`
  - Loads settings from `.env`.
  - Reads CSV, harmonizes schema, starts Dash app.

**Environment variables**
- Required:
  - `ANALYSIS_INPUT_CSV`: path to classified transactions CSV.
- Optional:
  - `DASH_HOST` (default: 127.0.0.1)
  - `DASH_PORT` (default: 8050)

**Runtime assumptions**
- Python 3.x
- pandas, numpy
- python-dotenv
- Dash v2+ (`dash`, `dash_table`)
- plotly.express

### 3. Data Pipeline Walkthrough (Step-by-Step)
1. **Read CSV** from `ANALYSIS_INPUT_CSV`.
2. **Harmonize schema**:
   - `Amount` required; coerced to numeric.
   - `YearMonth` created from `Date` if missing; else set to `UNKNOWN` if not available.
   - Canonical columns created if missing: `Cashflow_Section`, `Category_L1`, `Category_L2`, `Instrument`, `Flow_Nature`, `Record_Type`, `Counterparty_Core`, `Counterparty_Norm`, `Description`.
   - Aliases are applied from classifier v3 or older names.
   - Counterparty normalization derived from `Description` if missing.
   - Flags are computed: `Is_Summary`, `Is_TransferSection`, `Is_Inflow`, `Is_Outflow`, `AbsAmount`.
3. **Dash UI**:
   - Filters: YearMonth range, section, categories, transfer exclusion, summary exclusion.
   - KPIs: Operating income/spend/net, investing, financing, net cashflow.
   - Charts: net cashflow line, stacked income/spend by Category_L2, drilldown bar, recurring candidates.
   - Transaction explorer: filterable, sortable table with last 500 rows.

### 4. Schema Contract (Dashboard Canonical Columns)
**Canonical columns produced by `harmonize_schema()`**
| Column | Meaning | Derived | Required by dashboard | Notes |
|---|---|---:|---:|---|
| `Amount` | Signed transaction amount | Yes (coerced) | Yes | Only strict requirement |
| `YearMonth` | YYYY-MM bucket | Yes | Yes | Derived from `Date` if needed |
| `Description` | Raw description | Yes if missing | Yes | Used in search |
| `Cashflow_Section` | OPERATING/INVESTING/FINANCING/TRANSFER | Yes | Yes | Primary filter |
| `Category_L1` | Economic L1 | Yes | Yes | Primary filter |
| `Category_L2` | Economic L2 | Yes | Yes | Primary filter |
| `Instrument` | Bank rail / instrument | Yes | Yes | Drilldown option |
| `Flow_Nature` | INCOME/EXPENSE/TRANSFER/NON-CASH | Yes | No | Optional display |
| `Record_Type` | TRANSACTION/SUMMARY | Yes | No | For summary exclusion |
| `Counterparty_Norm` | Uppercase description | Yes | No | Derived if missing |
| `Counterparty_Core` | Counterparty short label | Yes | Yes | Drilldown option |
| `Is_Summary` | Summary row flag | Yes | Yes | Used to exclude balance B/F |
| `Is_TransferSection` | Transfer row flag | Yes | Yes | Used to exclude transfers |
| `Is_Inflow` | Amount > 0 | Yes | No | Convenience flag |
| `Is_Outflow` | Amount < 0 | Yes | No | Convenience flag |
| `AbsAmount` | Absolute amount | Yes | Yes | Used in charts |

**Alias mapping used**
- `Cashflow_Statement` ? `Cashflow_Section`
- `Economic_Purpose_L1` ? `Category_L1`
- `Economic_Purpose_L2` ? `Category_L2`
- `Bank_Rail` ? `Instrument`

### 5. Classification Rules & Priority
Not applicable in this file. It consumes classifier output; no new rule ordering is applied.

### 6. Overrides Logic Deep Dive
Not applicable in this file. Overrides are handled upstream.

### 7. Dashboard Consumption Logic
- Filters on `YearMonth`, `Cashflow_Section`, `Category_L1`, `Category_L2`.
- Optional exclusion of transfers and summary rows.
- Aggregations:
  - KPI totals by section and net cashflow.
  - Income/Spend stacks by `Category_L2`.
  - Drilldown bar by selected dimension (Category_L2, Counterparty_Core, Instrument).
  - Recurring candidates grouped by `Category_L2` using months_present and coefficient of variation.

**Known failure modes**
- Missing `Amount` raises ValueError.
- Missing `Date` and `YearMonth` results in all rows assigned to `UNKNOWN` bucket.
- Empty datasets yield empty graphs with "no data" titles.

---

## Findings & Risks
1. **Correctness risks**
   - Overrides can change economic and cashflow fields; no invariant enforcement beyond transfer short-circuit and managerial derivation.
   - Transfer classification now matches regardless of sign; this is intended but can classify inflows/outflows as transfers when tokens match.
2. **Schema mismatch risks**
   - Dashboard expects classifier v3-style columns or aliases; schema drift is mitigated but unknown column names may be dropped.
3. **Performance risks**
   - `classify_df` uses row-wise `apply` and overrides use `iterrows`, which can be slow on very large datasets.
4. **Maintainability risks**
   - Hardcoded token lists (employers, insurers, patterns) can drift over time.
   - MANAGERIAL_DERIVE_MAP must stay aligned with classifier outputs.
5. **Dependency / environment risks**
   - Requires pandas, python-dotenv, dash, plotly, numpy.
   - Excel override loading requires pandas Excel engine (openpyxl).
6. **Testing gaps**
   - Only minimal inline `_self_check()` exists; no automated test suite.

---

## Improvement Roadmap (No Rewrite)

### P0 (Must-fix before extending dashboard)
1. **auto_classify_transactions.py / apply_overrides**
   - What: Add optional invariant validation checks for salary, balance B/F, and transfers after overrides.
   - Why: Prevent silent violations of FP&A invariants.
   - Minimal patch: Validate `Rule_ID`-based invariants post-override and raise error on violation.
   - Acceptance: Overrides that violate invariants raise ValueError; compliant overrides pass.

### P1 (Should-fix for robustness)
1. **auto_classify_transactions.py / ensure_txn_id**
   - What: Add deterministic salt or more fields if collisions are observed in practice.
   - Why: Guard against rare hash collisions in long-term datasets.
   - Minimal patch: Add account or statement identifier if available.
   - Acceptance: No duplicate Txn_IDs in a known sample.
2. **dashboard_app.py / harmonize_schema**
   - What: Explicitly log or surface schema gaps instead of silent fill defaults.
   - Why: Improves audit traceability.
   - Minimal patch: Add warnings when defaults are used for canonical columns.
   - Acceptance: Logs emitted when defaults substituted.

### P2 (Nice-to-have enhancements)
1. **auto_classify_transactions.py / overrides**
   - What: Add optional validation report showing applied override fields per Txn_ID.
   - Why: Auditability and QA.
   - Minimal patch: Write a small CSV report if enabled by env var.
   - Acceptance: Report file lists override diffs.
2. **dashboard_app.py / UI**
   - What: Add user toggle to include/exclude non-baseline items.
   - Why: Baseline vs total analysis toggling.
   - Minimal patch: Add filter on `Baseline_Eligible` where present.
   - Acceptance: UI filter updates KPIs and charts.

---

## Source References
- `code/auto_classify_transactions.py`
- `code/dashboard_app.py`
