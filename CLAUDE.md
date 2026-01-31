# CLAUDE.md - FP&A Cashflow Classification Pipeline

This file provides AI assistants and developers with essential context for working with this audit-grade FP&A personal cashflow classification system.

## Project Overview

### Purpose

This repository implements a **3-stage pipeline** for cleaning, classifying, and visualizing personal bank transactions with FP&A (Financial Planning & Analysis) semantics:

| Stage | Script | Purpose | Output |
|-------|--------|---------|--------|
| **Stage 1** | `clean_bank_statement.py` | Clean raw bank CSVs, generate order-independent Txn_IDs | `combined_cleaned.csv` |
| **Stage 2** | `auto_classify_transactions.py` | Apply deterministic rule-based classification (R00-R16) | `classified_transactions_v3.csv` |
| **Stage 3** | `dashboard_app.py` | Interactive Dash UI for cashflow analytics | Web app at `localhost:8050` |

### Key Principles

- **Audit-grade**: Every transaction has a `Rule_ID` and `Rule_Explanation` for traceability
- **Deterministic**: Regex/token-based rules (not ML) ensure reproducible, explainable results
- **Order-independent**: Txn_IDs are mathematically proven stable regardless of row ordering
- **FP&A semantics**: Bank rails (GIRO/FAST) ≠ economic meaning; transfers are neutralized

### Dependencies

**Runtime**: Python 3.10+

**Core packages** (see `code/requirements.txt`):
```
pandas>=2.2.0
numpy>=2.0.0
matplotlib>=3.8.0
python-dotenv>=1.0.0
openpyxl>=3.1.0
dash>=2.0.0
plotly>=5.0.0
```

---

## Directory Structure

```
cashflowanalysis/
├── code/                           # Main application code
│   ├── clean_bank_statement.py     # Stage 1: CSV cleaning & Txn_ID generation
│   ├── auto_classify_transactions.py # Stage 2: Rule-based classification
│   ├── dashboard_app.py            # Stage 3: Dash analytics UI
│   ├── classification_diagnostics.py # Diagnostic artifact generation
│   ├── txn_id_stability_check.py   # Order-independence validation tool
│   ├── migrate_overrides.py        # Override migration between Txn_ID schemes
│   ├── run_analysis.py             # Legacy YoY/MoM analysis
│   ├── analytics/                  # Analytics module (io, transforms, charts)
│   ├── requirements.txt            # Python dependencies
│   └── .env                        # Environment configuration
├── docs/                           # Specification documents
│   ├── RUNBOOK.md                  # CLI usage and troubleshooting
│   ├── DATA_CONTRACT.md            # Input/output schema definitions
│   ├── TXN_ID_SPEC.md              # Txn_ID algorithm specification
│   ├── CLASSIFIER_RULEBOOK.md      # Complete rule reference (R00-R16)
│   └── DASHBOARD_SEMANTICS.md      # Dashboard filter/KPI definitions
├── diagnostics/                    # Diagnostic output artifacts
│   ├── DIAGNOSTICS_README.md       # Interpretation guide
│   ├── rule_impact_summary.csv     # Dollar impact by rule
│   ├── fallback_pressure_report.csv # R14/R15 severity analysis
│   ├── category_anomaly_report.csv # Detailed fallback drilldown
│   └── override_masking_report.csv # Override coverage analysis
├── documentation/                  # Technical documentation
│   └── cashflow_pipeline_technical_documentation.md
└── HARDENING_SUMMARY.md           # Audit-grade implementation notes
```

---

## Classification Logic

### Rule Chain (R00-R16)

The classifier applies **17 deterministic rules** in strict priority order. First match wins.

| Priority | Rule_ID | Trigger | Cashflow_Section | Baseline |
|:--------:|---------|---------|------------------|:--------:|
| 0 | `R00_BALANCE_BF` | Description = "BALANCE B/F" | NON-CASH | No |
| 1 | `R01_SALARY` | Inflow + employer tokens | OPERATING | Yes |
| 2 | `R02_INTEREST` | Inflow + "INTEREST CREDIT" | OPERATING | Yes |
| 3 | `R03_TRUST_INTERNAL` | "Trust Bank OTHR Transfer" | TRANSFER | No |
| 4 | `R04_PROPERTY_DOWNPAYMENT` | Outflow + cheque patterns | INVESTING | No |
| 5 | `R05_TAX` | Outflow + "IRAS" / tax patterns | OPERATING | Yes |
| 6 | `R06_MORTGAGE` | Outflow + "WD. LOANS" / mortgage | FINANCING | Yes |
| 7 | `R07_CAR_LOAN` | Outflow + car finance patterns | FINANCING | Yes |
| 8 | `R08_RENOVATION` | Outflow + renovation keywords | INVESTING | No |
| 9 | `R09_MCST` | Outflow + "MCST" / condo fees | OPERATING | Yes |
| 10 | `R10_INS_IN` | Insurer token + inflow markers | OPERATING | No |
| 11 | `R11_INS_OUT` | Insurer token + outflow | OPERATING | Yes |
| 12 | `R12_CC_SETTLEMENT` | Outflow + CC issuer + "BILL PAYMENT" | FINANCING | Yes |
| 13 | `R13_INTERNAL_TRANSFER` | Transfer rail + self-entity tokens | TRANSFER | No |
| 14 | `R14_OTHER_INCOME` | Inflow fallback (unmatched) | OPERATING | No |
| 15 | `R15_GENERIC_OUTFLOW` | Outflow fallback (unmatched) | OPERATING | No |
| 16 | `R16_ZERO_ADJ` | Amount = 0 | NON-CASH | No |

### Adding New Rules (R17+)

**Step-by-step process:**

1. **Define patterns** in `auto_classify_transactions.py` (lines ~112-175):
   ```python
   NEW_PATTERNS = [r"\bKEYWORD1\b", r"\bKEYWORD2\b"]
   ```

2. **Compile patterns** (lines ~530-542):
   ```python
   P_NEW = compile_patterns(NEW_PATTERNS)
   ```

3. **Add rule in `classify_row()`** (lines ~572-915) — **placement is critical**:
   ```python
   # Insert BEFORE R14/R15 fallbacks, AFTER more specific rules
   if amount < 0 and has_any(d, P_NEW):
       return ClassResult(
           rule_id="R17_NEW_RULE",
           rule_explanation="Matched NEW_PATTERNS; classified as ...",
           # ... other taxonomy fields
       )
   ```

4. **Update managerial derivation** (optional, lines ~83-105):
   ```python
   MANAGERIAL_DERIVE_MAP[("ECON_L1", "ECON_L2")] = ("MGR_L1", "MGR_L2")
   ```

5. **Document** in `docs/CLASSIFIER_RULEBOOK.md` and `docs/DATA_CONTRACT.md`

6. **Test** by adding a case to `_self_check()` (lines ~1195-1245)

### Placement Rationale

| Tier | Rules | Rationale |
|------|-------|-----------|
| **Tier 0** | R00 | Summary rows must never be classified as transactions |
| **Tier 1** | R01-R02 | Income protected from misclassification as lifestyle |
| **Tier 2** | R03, R13 | Transfers neutralized before economic classification |
| **Tier 3** | R04-R12 | Specific patterns before fallback rules |
| **Tier 4** | R14-R16 | Conservative fallbacks for unmatched transactions |

**Key invariants:**
- Salary/income rules **before** transfer rules (protection)
- Specific patterns **before** generic fallbacks
- CC settlement **before** generic transfers (pattern overlap)

---

## Diagnostics Workflow

### Generated Artifacts

Run `classification_diagnostics.py` to generate four CSV reports:

| Artifact | Purpose | Key Columns |
|----------|---------|-------------|
| `rule_impact_summary.csv` | Dollar impact by rule | `Rule_ID`, `Net_Impact`, `Txn_Count`, `Abs_Impact_Rank` |
| `fallback_pressure_report.csv` | R14/R15 severity analysis | `Severity`, `Pct_of_Direction`, `Top_Description_1..5` |
| `category_anomaly_report.csv` | Fallback transaction drilldown | `Recurrence_Pattern`, `Suggested_Category`, `Bank_Rail_Breakdown` |
| `override_masking_report.csv` | Override coverage analysis | `Override_Pct`, `Top_Override_Magnets` |

### Severity Thresholds

| Rule | WARNING | CRITICAL |
|------|:-------:|:--------:|
| `R14_OTHER_INCOME` | >15% of inflows | >25% of inflows |
| `R15_GENERIC_OUTFLOW` | >30% of outflows | >50% of outflows |

### Signals Requiring Rule Additions

1. **CRITICAL severity** in `fallback_pressure_report.csv` — immediate review needed
2. **High concentration** (>20%) in single description — obvious pattern extraction
3. **RECURRING pattern** with significant dollar impact in `category_anomaly_report.csv`
4. **`Suggested_Category` populated** — regex hints for common patterns (SINGTEL→TELECOM, GRAB→RIDESHARE)
5. **Override magnets** (≥3 overrides for same description) — systematic misclassification

### Interpreting Diagnostics

**Decision framework:**

```
Is this dashboard number real behavior or a classification artifact?

1. Check rule_impact_summary.csv
   → R14/R15 in top 5 by dollar impact? → Likely artifact

2. Check fallback_pressure_report.csv
   → CRITICAL severity? → Significant classification gaps
   → High concentration? → Extractable pattern for new rule

3. Check category_anomaly_report.csv
   → Suggested_Category hints? → Direct rule candidate
   → RECURRING + high $? → Strong rule candidate

4. Check override_masking_report.csv
   → Override magnets? → Convert to rules
```

---

## Testing & Verification

### Built-in Validation

**Stage 1 (`clean_bank_statement.py`):**
- No blank Txn_IDs
- Txn_ID uniqueness per file and combined
- Balance reconciliation (opening + sum = closing)
- True duplicate detection with explicit errors

**Stage 2 (`auto_classify_transactions.py`):**
- Txn_ID uniqueness post-override
- Override duplicate detection
- FP&A invariant checks (salary protection, transfer short-circuit)

### Self-Check Mode

Run classifier with built-in tests:

```bash
cd code
export RUN_SELF_CHECKS=1
python auto_classify_transactions.py
```

Tests Txn_ID generation, transfer classification, and override application.

### Txn_ID Stability Validation

Verify order-independence:

```bash
python code/txn_id_stability_check.py --csv "path/to/combined_cleaned.csv"
```

- Recomputes Txn_IDs on shuffled data
- Verifies 100% match with original
- Exit code 0 = success, 1 = failure

### Rerun Classification + Diagnostics

After any rule change:

```bash
# 1. Rerun classification
python code/auto_classify_transactions.py

# 2. Regenerate diagnostics
python code/classification_diagnostics.py \
    --input "path/to/classified_transactions_v3.csv" \
    --output-dir "diagnostics/"

# 3. Compare before/after fallback pressure
```

---

## Common Workflows

### Run Stage 2 Classification

**From code directory (loads `.env`):**
```bash
cd code
python auto_classify_transactions.py
```

**With explicit paths:**
```bash
export CLASSIFY_INPUT_CSV="/path/to/combined_cleaned.csv"
export CLASSIFY_OUTPUT_DIR="/path/to/output"
python code/auto_classify_transactions.py
```

**With overrides:**
```bash
export CLASSIFY_OVERRIDE_XLSX="/path/to/overrides.xlsx"
python code/auto_classify_transactions.py
```

### Run Diagnostics

**Basic:**
```bash
python code/classification_diagnostics.py \
    --input "path/to/classified_transactions_v3.csv" \
    --output-dir "diagnostics/"
```

**With override analysis:**
```bash
python code/classification_diagnostics.py \
    --input "path/to/classified_transactions_v3.csv" \
    --overrides "path/to/overrides.xlsx" \
    --output-dir "diagnostics/" \
    --include-transfers \
    --include-non-cash
```

### Update and Validate Rule Changes

1. **Edit** `auto_classify_transactions.py` — add pattern + rule in `classify_row()`
2. **Rerun classification:**
   ```bash
   python code/auto_classify_transactions.py
   ```
3. **Rerun diagnostics:**
   ```bash
   python code/classification_diagnostics.py --input ... --output-dir diagnostics/
   ```
4. **Verify improvement:**
   - Fallback pressure should decrease
   - New rule should appear in `rule_impact_summary.csv`
5. **Update documentation** in `docs/CLASSIFIER_RULEBOOK.md`

### Run Dashboard

```bash
python code/dashboard_app.py
# Opens at http://127.0.0.1:8050
```

### Migrate Overrides (After Txn_ID Scheme Change)

```bash
python code/migrate_overrides.py \
    --old_txn_csv "old_classified.csv" \
    --new_txn_csv "new_classified.csv" \
    --old_overrides "overrides.xlsx" \
    --output "overrides_migrated.xlsx"
```

---

## Coding Conventions for Rule Additions

### Regex/Pattern Matching

**Pattern definition style:**
```python
# Use raw strings with word boundaries
MY_PATTERNS = [
    r"\bEXACT_KEYWORD\b",           # Exact word match
    r"PARTIAL.*MATCH",               # Partial with wildcard
    r"(OPTION1|OPTION2)\s+SUFFIX",   # Alternatives
]
P_MY = compile_patterns(MY_PATTERNS)  # Compile once at module load
```

**Pattern usage:**
```python
if has_any(normalized_desc, P_MY):    # Check if any pattern matches
    ...
if contains_any_token(desc, TOKENS):  # Check for token presence
    ...
```

### Rule Implementation Style

```python
# Priority N: R17_MY_RULE
if amount < 0 and has_any(d, P_MY):
    return ClassResult(
        record_type="TRANSACTION",
        flow_nature=FLOW_NATURE["EXPENSE"],
        cashflow_statement=CFS["OPERATING"],  # or FINANCING/INVESTING
        econ_l1=EP_L1["CATEGORY"],
        econ_l2="SPECIFIC_SUBCATEGORY",
        asset_context=ASSET_CTX["NONE"],
        stability_class=STABILITY["RECURRING"],  # or VARIABLE/LUMPY
        baseline_eligible=True,  # False for transfers/non-cash
        event_tag=EVENT_TAG["NONE"],
        bank_rail=rail,
        rule_id="R17_MY_RULE",
        rule_explanation="Detected MY_PATTERNS; classified as CATEGORY.",
        managerial_l1="MGR_CATEGORY",
        managerial_l2="MGR_SUBCATEGORY",
        is_cc_settlement=False,
    )
```

### Rule Placement Conventions

1. **Insert before fallbacks** (R14/R15) — fallbacks should only catch unmatched transactions
2. **Insert after more specific rules** — avoid shadowing existing patterns
3. **Group related rules** — keep mortgage/car loan/renovation together
4. **Comment the priority number** — `# Priority N: R17_MY_RULE`

### Naming Conventions

| Element | Convention | Example |
|---------|------------|---------|
| Rule ID | `R##_SNAKE_CASE` | `R17_UTILITIES_TELECOM` |
| Pattern list | `DOMAIN_PATTERNS` | `TELECOM_PATTERNS` |
| Compiled pattern | `P_DOMAIN` | `P_TELECOM` |
| Economic L2 | `SPECIFIC_SUBCATEGORY` | `TELECOM_BILL` |

### Testing New Rules

Add test case to `_self_check()`:

```python
# Test R17 classification
test_desc = "GIRO SINGTEL BILL PAYMENT"
result = classify_row(test_desc, -150.00)
assert result.rule_id == "R17_UTILITIES_TELECOM", f"Expected R17, got {result.rule_id}"
```

---

## Quick Reference

### Key Files

| Task | File |
|------|------|
| Add classification rule | `code/auto_classify_transactions.py` |
| Document rule | `docs/CLASSIFIER_RULEBOOK.md` |
| Check schema | `docs/DATA_CONTRACT.md` |
| Run diagnostics | `code/classification_diagnostics.py` |
| Validate Txn_IDs | `code/txn_id_stability_check.py` |
| Configure paths | `code/.env` |

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `CLASSIFY_INPUT_CSV` | Input for Stage 2 |
| `CLASSIFY_OUTPUT_DIR` | Output directory |
| `CLASSIFY_OVERRIDE_XLSX` | Path to overrides.xlsx |
| `RUN_SELF_CHECKS=1` | Run built-in tests |
| `DASH_HOST` / `DASH_PORT` | Dashboard binding |

### Diagnostic Severity Actions

| Severity | Action |
|----------|--------|
| **OK** | Monitor; no immediate action |
| **WARNING** | Review top descriptions; consider new rules |
| **CRITICAL** | Immediate review; significant classification gaps |
