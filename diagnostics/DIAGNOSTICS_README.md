# Classification Diagnostics

FP&A-grade diagnostic tool for analyzing transaction classification quality.

## Purpose

This tool generates evidence artifacts to answer: **"Is this dashboard number real behavior, or a classification artifact?"**

It focuses on fallback rule pressure from:
- **R14_OTHER_INCOME**: Catches all unmapped inflows → `INCOME/OTHER_INCOME`
- **R15_GENERIC_OUTFLOW**: Catches all unmapped outflows → `LIFESTYLE/DISCRETIONARY`

## Usage

```bash
# Basic usage
python code/classification_diagnostics.py \
    --input "cashflow analysis/UOB/CSV/output/classified_transactions_v3.csv" \
    --output-dir "diagnostics"

# With overrides analysis
python code/classification_diagnostics.py \
    --input "cashflow analysis/UOB/CSV/output/classified_transactions_v3.csv" \
    --overrides "data/overrides/overrides.xlsx" \
    --output-dir "diagnostics"

# Include transfers and non-cash in analysis
python code/classification_diagnostics.py \
    --input "cashflow analysis/UOB/CSV/output/classified_transactions_v3.csv" \
    --output-dir "diagnostics" \
    --include-transfers \
    --include-non-cash
```

## CLI Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--input` | Yes | Path to classified_transactions_v3.csv |
| `--output-dir` | Yes | Directory for output CSVs |
| `--overrides` | No | Path to overrides.xlsx |
| `--include-transfers` | No | Include TRANSFER section (default: excluded) |
| `--include-non-cash` | No | Include NON-CASH section (default: excluded) |

## Output Files

### 1. rule_impact_summary.csv

**Purpose**: Quantify transaction volume and dollar value by classification rule.

| Column | Description |
|--------|-------------|
| Rule_ID | Classification rule (R00-R16) |
| Txn_Count | Number of transactions |
| Txn_Pct | % of total transactions |
| Inflow_Total | Sum of positive amounts |
| Outflow_Total | Absolute sum of negative amounts |
| Net_Impact | Inflow_Total - Outflow_Total |
| Inflow_Pct | % of total inflows |
| Outflow_Pct | % of total outflows |
| Abs_Impact_Rank | Rank by absolute net impact |

**Interpretation**:
- If R14 or R15 rank in top 5 by impact → classification gaps exist
- High Txn_Pct for fallback rules suggests patterns worth extracting

### 2. fallback_pressure_report.csv

**Purpose**: Determine if fallback rules are handling disproportionate cashflow.

| Column | Description |
|--------|-------------|
| Rule_ID | R14_OTHER_INCOME or R15_GENERIC_OUTFLOW |
| Direction | inflow / outflow |
| Txn_Count | Transaction count |
| Dollar_Value | Absolute dollar sum |
| Pct_of_Direction | % of total inflows (R14) or outflows (R15) |
| Severity | OK / WARNING / CRITICAL |
| Threshold_Warning | Warning threshold (%) |
| Threshold_Critical | Critical threshold (%) |
| Top_Description_1..5 | Top 5 descriptions by count |
| Top_Description_1..5_Count | Transaction counts |
| Top_Description_1..5_Dollars | Dollar values |
| Top_Concentration_Pct | % of fallback from single top description |

**Severity Thresholds**:

| Rule | WARNING | CRITICAL |
|------|---------|----------|
| R14_OTHER_INCOME | >15% of inflows | >25% of inflows |
| R15_GENERIC_OUTFLOW | >30% of outflows | >50% of outflows |

**Interpretation**:
- CRITICAL → classification has significant gaps; rule changes needed
- WARNING → review top descriptions for rule candidates
- High concentration (>20%) → obvious pattern for new rule

### 3. category_anomaly_report.csv

**Purpose**: Drilldown on transactions hitting fallback rules.

| Column | Description |
|--------|-------------|
| Anomaly_Type | OTHER_INCOME or DISCRETIONARY |
| Description_Norm | Normalized description (uppercase, collapsed) |
| Counterparty_Core | First 80 chars of counterparty (if available) |
| Rule_ID | R14 or R15 |
| Txn_Count | Number of occurrences |
| Total_Amount | Sum of amounts (signed) |
| Avg_Amount | Mean amount |
| First_YearMonth | Earliest occurrence |
| Last_YearMonth | Latest occurrence |
| Months_Span | Month range (inclusive) |
| Unique_Months | Distinct months with transactions |
| Recurrence_Pattern | ONE_OFF / SPORADIC / RECURRING |
| Bank_Rail_Breakdown | Dollar breakdown by rail (e.g., "GIRO:$1234\|FAST:$567") |
| Suggested_Category | Regex-based hint (HINT ONLY - does not modify data) |

**Recurrence Pattern Definitions**:
- **ONE_OFF**: Single occurrence
- **RECURRING**: 3+ occurrences AND 70%+ month coverage
- **SPORADIC**: 2+ occurrences AND 30%+ month coverage

**Interpretation**:
- RECURRING patterns with high $ impact → strong rule candidates
- Suggested_Category != blank → consider adding specific rule
- Look for patterns like SINGTEL, TOWNCOUNCIL in DISCRETIONARY

### 4. override_masking_report.csv

**Purpose**: Detect if manual overrides are hiding systematic rule failures.

| Metric | Description |
|--------|-------------|
| Overrides_Available | True/False |
| Total_Overrides_Enabled | Count from overrides.xlsx |
| Transactions_Overridden | Count where Was_Overridden=True |
| Override_Pct | % of transactions overridden |
| Top_Override_Magnets | Descriptions with >=2 overrides (rule candidates) |
| Note_Original_Rule | Disclaimer about rule inference |

**Important Limitation**:
The script **cannot infer the original Rule_ID before an override was applied**. All Rule_ID values in the data reflect the current (post-override) classification. If you need to track rule changes, consider adding an `Original_Rule_ID` column to the classifier output.

**Interpretation**:
- Override magnets with >=3 occurrences → convert to rules
- High Override_Pct (>5%) → review override strategy

## Decision Framework

```
Is this dashboard number real behavior, or a classification artifact?

1. Check rule_impact_summary.csv
   → If R14/R15 in top 5 by dollar impact → ARTIFACT

2. Check fallback_pressure_report.csv
   → CRITICAL severity → ARTIFACT (significant gaps)
   → High concentration → obvious rule candidate

3. Check category_anomaly_report.csv
   → Find specific descriptions inflating the category
   → If Suggested_Category != blank → MISCLASSIFICATION
   → If RECURRING + high $ → strong rule candidate

4. Check override_masking_report.csv
   → Override magnets with >=3 → convert to rules
```

## Common Misclassifications to Look For

### In DISCRETIONARY (R15 fallback)
| Pattern | Should Be |
|---------|-----------|
| SINGTEL, STARHUB, M1 | UTILITIES/TELECOM |
| SP SERVICES | UTILITIES/ELECTRIC_GAS |
| TOWNCOUNCIL, TCSC | HOUSING/TOWN_COUNCIL_FEES |
| TRANSITLINK, EZLINK | TRANSPORT/PUBLIC_TRANSIT |
| GRAB, GOJEK | TRANSPORT/RIDESHARE |
| NETFLIX, SPOTIFY | SUBSCRIPTIONS/ENTERTAINMENT |

### In OTHER_INCOME (R14 fallback)
| Pattern | Should Be |
|---------|-----------|
| REFUND | INCOME/REFUND (or expense offset) |
| CASHBACK | INCOME/CASHBACK |
| DIVIDEND | INCOME/DIVIDEND |
| Transfers to self | TRANSFER/INTERNAL_TRANSFER |

## Workflow

1. Run diagnostics
2. Review fallback_pressure_report for severity
3. Examine category_anomaly_report for top patterns
4. Check override_masking_report for systematic gaps
5. Document findings before proposing rule changes
6. After rule changes, re-run diagnostics to verify improvement

## Base Filter (FP&A Semantics)

The script uses these exclusion rules by default:
- **SUMMARY rows**: Record_Type == "SUMMARY"
- **BALANCE_BF**: Category_L2 == "BALANCE_BF"
- **NON-CASH section**: Cashflow_Section == "NON-CASH" (unless `--include-non-cash`)
- **TRANSFER section**: Cashflow_Section == "TRANSFER" (unless `--include-transfers`)

This ensures analysis reflects **net economic cashflow**, not accounting artifacts.
