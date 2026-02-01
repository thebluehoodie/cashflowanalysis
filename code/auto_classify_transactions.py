#!/usr/bin/env python3
"""
auto_classify_transactions.py  (FP&A taxonomy rewrite + overrides)

Deterministic, explainable transaction classifier for personal FP&A-grade cashflow analysis.

Key FP&A invariants enforced:
- Bank rail (PAYNOW/FAST/GIRO/etc.) is NOT economic meaning.
- Transfers are neutralized (Cashflow_Statement=TRANSFER) and excluded from baseline.
- Salary / income can never be classified as lifestyle.
- Balance B/F is Non-Cash and excluded from analytics.
- Trust Bank OTHR Transfer is always an internal fund transfer (reallocation only).
- Cheque Withdrawal and DR CO CHARGES are treated as property downpayment (Investing / Housing / Property_Purchase).

Overrides:
- Optional overrides.xlsx keyed by Txn_ID
- Only columns provided (non-blank) override classifier outputs
- Adds audit columns: Was_Overridden, Override_ID_Applied, Override_Reason

IO:
- Reads CLASSIFY_INPUT_CSV from .env (or env)
- Writes classified_transactions_v3.csv to CLASSIFY_OUTPUT_DIR

Override env vars (optional):
- CLASSIFY_OVERRIDE_XLSX   (full path to overrides.xlsx)
- CLASSIFY_OVERRIDE_DIR    (dir containing overrides.xlsx)

Performance:
- O(n * R) row-wise regex evaluation. For typical personal volumes this is fine.
"""

from __future__ import annotations

import os
import re
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, List, Tuple

import pandas as pd
from dotenv import load_dotenv


# ======================================================
# CONFIG (keep explicit + auditable)
# ======================================================

SALARY_EMPLOYERS = [
    "HP", "MICROSOFT", "ABBOTT", "CHANGI AIRPORT GROUP", "KERING"
]

INSURERS = [
    "AIA", "PRUDENTIAL", "GREAT EASTERN", "NTUC",
    "MANULIFE", "AVIVA", "AXA", "HSBC LIFE"
]

# Internal/self-controlled entities. Keep conservative; you can extend later.
SELF_ENTITIES = [
    "WEILUN", "SAM", "SAMANTHA", "SAMANTHA SEAH",
    "TRUST BANK",
    "3493939244",  # User-confirmed: personal MBK account
    "3491284038",  # User-confirmed: personal MBK account
]

CC_ISSUER_PATTERNS: Dict[str, List[str]] = {
    "CITI": [r"\bCITI\b"],
    "SCB":  [r"\bSCB\b", r"\bSTANDARD\s+CHARTERED\b"],
    "HSBC": [r"\bHSBC\b"],
    "UOB":  [r"\bUOB\b"],
    "OCBC": [r"\bOCBC\b"],
    "AMEX": [r"\bAMEX\b", r"\bAMERICAN\s+EXPRESS\b"],
}

RAILS: Dict[str, str] = {
    "GIRO":   r"\bGIRO\b",
    "FAST":   r"\bFAST\b",
    "PAYNOW": r"\bPAYNOW\b",
    "NETS":   r"\bNETS\b",
    "ATM":    r"\bATM\b|\bCASH\s+WITHDRAWAL\b",
    "CHEQUE": r"\bCHEQUE\b",
    "CARD":   r"\bBILL\s+PAYMENT\b|\bMBK-\w+\s+CC\b|\bUOB\s+CARDS\b|\bCARD(S)?\b",
}

MANAGERIAL_DERIVE_MAP: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("NON-CASH", "BALANCE_BF"): ("NON-CASH", "BALANCE_BF"),
    ("INCOME", "SALARY"): ("INCOME", "SALARY"),
    ("INCOME", "INTEREST"): ("INCOME", "INTEREST"),
    ("TRANSFER", "INTERNAL_TRANSFER"): ("TRANSFER", "INTERNAL_TRANSFER"),
    ("HOUSING", "PROPERTY_PURCHASE"): ("HOUSING", "PROPERTY_PURCHASE"),
    ("TAXES", "IRAS_TAX"): ("TAXES", "IRAS_TAX"),
    ("DEBT_SERVICE", "MORTGAGE_PAYMENT"): ("DEBT_SERVICE", "MORTGAGE_PAYMENT"),
    ("DEBT_SERVICE", "CAR_LOAN_PAYMENT"): ("DEBT_SERVICE", "CAR_LOAN_PAYMENT"),
    ("HOUSING", "RENOVATION"): ("HOUSING", "RENOVATION"),
    ("HOUSING", "HOA_CONDO_FEES"): ("HOUSING", "HOA_CONDO_FEES"),
    ("INCOME", "INSURANCE_PAYOUT"): ("INCOME", "INSURANCE_PAYOUT"),
    ("INSURANCE", "PREMIUM"): ("INSURANCE", "PREMIUM"),
    ("DEBT_SERVICE", "CREDIT_CARD_SETTLEMENT_CITI"): ("LIFESTYLE", "CREDIT_CARD_SPEND_PROXY"),
    ("DEBT_SERVICE", "CREDIT_CARD_SETTLEMENT_SCB"): ("LIFESTYLE", "CREDIT_CARD_SPEND_PROXY"),
    ("DEBT_SERVICE", "CREDIT_CARD_SETTLEMENT_HSBC"): ("LIFESTYLE", "CREDIT_CARD_SPEND_PROXY"),
    ("DEBT_SERVICE", "CREDIT_CARD_SETTLEMENT_UOB"): ("LIFESTYLE", "CREDIT_CARD_SPEND_PROXY"),
    ("DEBT_SERVICE", "CREDIT_CARD_SETTLEMENT_OCBC"): ("LIFESTYLE", "CREDIT_CARD_SPEND_PROXY"),
    ("DEBT_SERVICE", "CREDIT_CARD_SETTLEMENT_AMEX"): ("LIFESTYLE", "CREDIT_CARD_SPEND_PROXY"),
    ("INCOME", "OTHER_INCOME"): ("INCOME", "OTHER_INCOME"),
    ("LIFESTYLE", "DISCRETIONARY"): ("LIFESTYLE", "DISCRETIONARY"),
    ("NON-CASH", "ACCOUNTING_ADJUSTMENT"): ("NON-CASH", "ACCOUNTING_ADJUSTMENT"),
    # R17+ new mappings
    ("SAVINGS_INVESTING", "SRS_CONTRIBUTION"): ("SAVINGS", "SRS_CONTRIBUTION"),
    ("SAVINGS_INVESTING", "CPF_VOLUNTARY"): ("SAVINGS", "CPF_VOLUNTARY"),
    ("HOUSING", "TOWN_COUNCIL_FEES"): ("HOUSING", "TOWN_COUNCIL_FEES"),
    ("INCOME", "GOVT_PAYOUT"): ("INCOME", "GOVT_PAYOUT"),
    ("LIFESTYLE", "CASH_WITHDRAWAL"): ("LIFESTYLE", "CASH_WITHDRAWAL"),
    ("LIFESTYLE", "TELECOM"): ("LIFESTYLE", "TELECOM"),
    ("LIFESTYLE", "TRANSIT"): ("LIFESTYLE", "TRANSIT"),
}


# ======================================================
# SEMANTIC PATTERNS (economic meaning)
# ======================================================

BALANCE_PATTERNS = [r"\bBALANCE\s+B/F\b"]

INTEREST_PATTERNS = [
    r"\bINTEREST\s+CREDIT\b",
    r"\bBONUS\s+INTEREST\b",
]

SALARY_PATTERNS = [
    r"\bSALARY\s+PAYMENT\b",
    r"\bGIRO\s+SALA\b",
]

TAX_PATTERNS = [
    r"\bIRAS\b",
    r"\bINCOME\s+TAX\b",
    r"\bPROPERTY\s+TAX\b",
    r"\bITX\b",
    r"\bPTXP\b",
]

MORTGAGE_PATTERNS = [
    r"\bTRF\.\s*WD\.\s*LOANS\b",
    r"\bWD\.\s*LOANS\b",
    r"\bMORTGAGE\b",
    r"\bHOUSING\s+LOAN\b",
]

MCST_PATTERNS = [
    r"\bMCST\b",
    r"\bMANAGEMENT\s+CORP\b",
]

PROPERTY_DOWNPAYMENT_PATTERNS = [
    r"\bCHEQUE\s+WITHDRAWAL\b",
    r"\bDR\s+CO\s+CHARGES\b",
    r"\bCO-\d{6}-\d{3}\b",
]

RENOVATION_PATTERNS = [
    r"\bBUILD\s+BUILT\b",
    r"\bRENOV\b",
    r"\bCONTRACTOR\b",
    r"\bCARPENTRY\b",
]

CAR_FINANCE_PATTERNS = [
    r"\bHONG\s+LEONG\s+FINANCE\b",
    r"\bHLF-\d+\b",
]

TRANSFER_PATTERNS = [
    r"\bFUNDS\s+TRF\b",
    r"\bTRANSFER\b",
    r"\bOTHR\s+TRANSFER\b",
]

TRUST_BANK_INTERNAL_PATTERNS = [
    r"\bTRUST\s+BANK\b.*\bOTHR\s+TRANSFER\b",
    r"\bOTHR\s+TRANSFER\b.*\bTRUST\s+BANK\b",
]

INS_INFLOW_MARKERS = [r"\bINWARD\s+CR\b", r"\bCR\s*-\s*GIRO\b"]
INS_OUTFLOW_MARKERS = [r"\bINWARD\s+DR\b", r"\bDR\s*-\s*GIRO\b"]

# ======================================================
# R17+ PATTERNS (new rules to reduce fallback pressure)
# ======================================================

# R17: SRS (Supplementary Retirement Scheme) contributions
SRS_PATTERNS = [
    r"\bSRS\s+CONT\b",
    r"\(SRS\)",
    r"\bSRS\s+CONTRIBUTION\b",
]

# R18: Voluntary CPF (Central Provident Fund) top-ups
CPF_PATTERNS = [
    r"\bCENTRAL\s+PROVIDENT\s+FU\b",
    r"\bCPF\s+TOP[- ]?UP\b",
    r"\bCPF\s+CONTRIBUTION\b",
]

# R19: Town Council / HDB conservancy fees
TOWN_COUNCIL_PATTERNS = [
    r"TOWNCOUNCIL",  # Matches WESTCOASTTOWNCOUNCIL, ANGMOKIOTOWNCOUNCIL etc.
    r"\bTOWN\s+COUNCIL\b",
    r"\bTC\s+S&CC\b",  # Town Council Service & Conservancy Charges
]

# R20: Government payouts (inflows)
GOVT_PAYOUT_PATTERNS = [
    r"\bGOVT?\s+PAYOUT\b",
    r"\bGOV\s+GOV\b",
    r"\bGST\s+VOUCHER\b",
    r"\bCDC\s+VOUCHER\b",
]

# R21: ATM cash withdrawals (explicit rule)
ATM_WITHDRAWAL_PATTERNS = [
    r"\bCASH\s+WITHDRAWAL\b",
    r"\bATM\s+WITHDRAWAL\b",
    r"\bCASH\s+WITHDRAWAL-ATM\b",
    r"\bCASH\s+WITHDRAWAL-SATM\b",
]

# R22: Telecom utilities
TELECOM_PATTERNS = [
    r"\bSINGTEL\b",
    r"\bSTARHUB\b",
    r"\bM1\s+LIMITED\b",
    r"\bM1\b(?!\s*\d)",  # M1 not followed by digit (to avoid model numbers)
    r"\bSIMBA\s+TELECOM\b",
    r"\bTPG\s+TELECOM\b",
]

# R23: Public transit
TRANSIT_PATTERNS = [
    r"\bTRANSIT\b",
    r"\bSMRT\b",
    r"\bSBS\s+TRANSIT\b",
    r"\bEZ-?LINK\b",
    r"\bCONSUMER\s+TRANSIT\b",
]

# R24: Bank fees and charges
BANK_FEE_PATTERNS = [
    r"\bCHEQUE\s+CHARGES\b",
    r"\bBANK\s+FEE\b",
    r"\bSERVICE\s+CHARGE\b",
    r"\bMONTHLY\s+FEE\b",
]


# ======================================================
# TAXONOMY TYPES (for clarity + consistency)
# ======================================================

FLOW_NATURE = {
    "INCOME": "INCOME",
    "EXPENSE": "EXPENSE",
    "TRANSFER": "TRANSFER",
    "NON_CASH": "NON-CASH",
}

CFS = {
    "OPERATING": "OPERATING",
    "INVESTING": "INVESTING",
    "FINANCING": "FINANCING",
    "TRANSFER": "TRANSFER",
    "NON_CASH": "NON-CASH",
}

EP_L1 = {
    "INCOME": "INCOME",
    "LIFESTYLE": "LIFESTYLE",
    "HOUSING": "HOUSING",
    "TAXES": "TAXES",
    "INSURANCE": "INSURANCE",
    "DEBT_SERVICE": "DEBT_SERVICE",
    "SAVINGS_INVESTING": "SAVINGS_INVESTING",
    "FEES": "FEES",
    "TRANSFER": "TRANSFER",
    "NON_CASH": "NON-CASH",
    "UNKNOWN": "UNKNOWN",
}

ASSET_CTX = {
    "GENERAL": "GENERAL",
    "PROPERTY": "PROPERTY",
    "CAR": "CAR",
    "FINANCIAL": "FINANCIAL",
    "UNKNOWN": "UNKNOWN",
}

STABILITY = {
    "STRUCTURAL": "STRUCTURAL_RECURRING",
    "SEMI": "SEMI_RECURRING",
    "VARIABLE": "VARIABLE",
    "ONE_OFF": "ONE_OFF",
}

EVENT_TAG = {
    "NONE": "NONE",
    "RENOVATION": "RENOVATION",
    "PROPERTY_ACQ": "PROPERTY_ACQ",
    "TAX_EVENT": "TAX_EVENT",
}


# ======================================================
# HELPERS
# ======================================================

def norm(x) -> str:
    return str(x).upper().strip() if pd.notna(x) else ""

def compile_patterns(raw_patterns: List[str]) -> List[re.Pattern]:
    return [re.compile(p) for p in raw_patterns]

def has_any(text: str, patterns: List[re.Pattern]) -> bool:
    return any(p.search(text) for p in patterns)

def infer_bank_rail(desc_u: str) -> str:
    for rail, pat in RAILS.items():
        if re.search(pat, desc_u):
            return rail
    return "OTHER"

def detect_cc_issuer(desc_u: str) -> Optional[str]:
    for issuer, pats in CC_ISSUER_PATTERNS.items():
        if any(re.search(p, desc_u) for p in pats):
            return issuer
    return None

def contains_any_token(desc_u: str, tokens: List[str]) -> bool:
    return any(t.upper() in desc_u for t in tokens)

def _collapse_ws(s: str) -> str:
    return " ".join(s.split())

def _canon_text(s: object) -> str:
    if pd.isna(s):
        return ""
    return _collapse_ws(str(s)).upper()

def _canon_date(d: object) -> str:
    if pd.isna(d):
        return ""
    s = str(d).strip()
    if s == "":
        return ""
    ts = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if pd.isna(ts):
        return _collapse_ws(s)
    return ts.strftime("%Y-%m-%d")

def _canon_amount_cents(a: object) -> str:
    if pd.isna(a):
        raise ValueError("Missing Amount for Txn_ID.")
    s = str(a).strip()
    if s == "":
        raise ValueError("Missing Amount for Txn_ID.")
    try:
        v = float(s)
    except ValueError as exc:
        raise ValueError(f"Invalid Amount for Txn_ID: {a}") from exc
    return str(int(round(v * 100)))

def _canon_yearmonth(ym: object) -> str:
    if pd.isna(ym):
        return ""
    return _collapse_ws(str(ym))

def _mk_row_fingerprint(row: pd.Series) -> str:
    """
    Generate deterministic row fingerprint from stable content fields.

    Used as final tie-breaker for occurrence index assignment (NOT part of Txn_ID hash).
    This ensures deterministic ordering even when all other tie-breakers are identical.

    CRITICAL: All numeric fields canonicalized to integer cents to prevent
    floating-point string variations from affecting determinism.

    Fields included (all stable, content-based):
    - Date, YearMonth, Amount, Description, SourceFile (base_key fields)
    - Balance, Withdrawals, Deposits (additional numeric differentiators)

    Returns:
        40-character SHA-1 hex hash of canonicalized row content
    """
    date = _canon_date(row.get("Date", ""))
    if date == "":
        date = "NA"

    year_month = _canon_yearmonth(row.get("YearMonth", ""))

    # HARDENING: Canonicalize ALL numeric fields to integer cents
    amount = _canon_amount_cents(row.get("Amount", "0"))

    desc = _canon_text(row.get("Description", ""))
    source = _canon_text(row.get("SourceFile", ""))

    # Additional fields for fingerprint (canonicalized to cents)
    balance_val = row.get("Balance", None)
    balance = _canon_amount_cents(balance_val) if pd.notna(balance_val) else "NaN"

    withdrawals_val = row.get("Withdrawals", None)
    withdrawals = _canon_amount_cents(withdrawals_val) if pd.notna(withdrawals_val) else "NaN"

    deposits_val = row.get("Deposits", None)
    deposits = _canon_amount_cents(deposits_val) if pd.notna(deposits_val) else "NaN"

    # Concatenate all fields
    fingerprint_key = "|".join([
        date, year_month, amount, desc, source,
        balance, withdrawals, deposits
    ])

    return hashlib.sha1(fingerprint_key.encode("utf-8")).hexdigest()

def _mk_txn_id(row: pd.Series, occurrence_index: int) -> str:
    """
    Generate Txn_ID with occurrence index.

    New scheme (order-independent):
    - base_key = Date | YearMonth | Amount_cents | Description | SourceFile
    - RowOrder REMOVED from base_key for order independence
    - occurrence_index added for disambiguation when base_keys collide

    Args:
        row: Transaction row with required fields
        occurrence_index: 1-based index within duplicate base_key group

    Returns:
        40-character SHA-1 hex hash
    """
    date = _canon_date(row.get("Date", ""))
    if date == "":
        date = "NA"

    year_month = _canon_yearmonth(row.get("YearMonth", ""))
    if year_month == "":
        raise ValueError("Missing YearMonth for Txn_ID.")

    amount_cents = _canon_amount_cents(row.get("Amount", ""))
    desc = _canon_text(row.get("Description", ""))

    source = _canon_text(row.get("SourceFile", ""))
    if source == "":
        raise ValueError("Missing SourceFile for Txn_ID.")

    # Build base_key WITHOUT RowOrder
    base_key = "|".join([date, year_month, amount_cents, desc, source])

    # Append occurrence index
    occ_suffix = f"OCC{occurrence_index:03d}"
    raw_key = f"{base_key}|{occ_suffix}"

    return hashlib.sha1(raw_key.encode("utf-8")).hexdigest()


def _generate_occurrence_indices(df: pd.DataFrame) -> pd.Series:
    """
    Assign occurrence index (1-based) to each row within base_key groups.

    For transactions with identical base_key (Date, YearMonth, Amount, Description, SourceFile),
    assign deterministic occurrence indices based on tie-breaker sort:
      1. Balance (ascending, NaN last)
      2. Withdrawals (ascending, NaN last)
      3. Deposits (ascending, NaN last)
      4. Amount (ascending, NaN last)
      5. row_fingerprint (ascending) - final deterministic tie-breaker

    Note: RowOrder has been REMOVED to achieve true order-independence.
    row_fingerprint ensures deterministic ordering even when all other fields are identical.

    HARDENING: Detects true indistinguishable duplicates (where row_fingerprint is identical
    within a base_key group) and raises ValueError to prevent silent instability.

    Returns:
        pd.Series of occurrence indices (1, 2, 3, ...) aligned with df index

    Raises:
        ValueError: If indistinguishable duplicate transactions are detected
    """
    df_work = df.copy()

    # Build base_key for grouping
    df_work["_base_key"] = (
        df_work["Date"].apply(_canon_date).replace("", "NA") + "|" +
        df_work["YearMonth"].apply(_canon_yearmonth) + "|" +
        df_work["Amount"].apply(lambda x: str(int(round(float(x) * 100)))) + "|" +
        df_work["Description"].apply(_canon_text) + "|" +
        df_work["SourceFile"].apply(_canon_text)
    )

    # Generate row fingerprint as final deterministic tie-breaker
    df_work["_row_fingerprint"] = df_work.apply(_mk_row_fingerprint, axis=1)

    # HARDENING: Detect true indistinguishable duplicates
    # Check for rows with identical base_key AND identical row_fingerprint
    dup_check = df_work.groupby(["_base_key", "_row_fingerprint"]).size()
    true_dups = dup_check[dup_check > 1]

    if not true_dups.empty:
        # Find sample rows for error message
        first_dup_key = true_dups.index[0]
        dup_rows = df_work[
            (df_work["_base_key"] == first_dup_key[0]) &
            (df_work["_row_fingerprint"] == first_dup_key[1])
        ]

        error_msg = (
            f"FATAL: Indistinguishable duplicate transactions detected.\n"
            f"Txn_ID cannot be made stable when rows are identical across ALL content fields.\n"
            f"\nDetected {len(true_dups)} duplicate groups affecting {true_dups.sum()} rows.\n"
            f"\nSample duplicate group (base_key + fingerprint identical):\n"
            f"{dup_rows[['Date', 'Amount', 'Description', 'SourceFile', 'Balance']].to_string(index=False)}\n"
            f"\nThis indicates:\n"
            f"  - Duplicate bank statement ingestion, OR\n"
            f"  - True duplicate transactions with no differentiating fields\n"
            f"\nAction required:\n"
            f"  - Review source CSV files for duplicates\n"
            f"  - Add manual differentiating field if these are truly distinct transactions"
        )
        raise ValueError(error_msg)

    # Sort by tie-breaker within each base_key group
    # Use stable mergesort for consistency
    df_sorted = df_work.sort_values(
        by=["_base_key", "Balance", "Withdrawals", "Deposits", "Amount", "_row_fingerprint"],
        kind="mergesort",
        na_position="last"
    ).reset_index(drop=False)

    # Assign occurrence index within each base_key group
    df_sorted["_occurrence_index"] = df_sorted.groupby("_base_key").cumcount() + 1

    # Restore original index order and return occurrence series
    df_sorted = df_sorted.set_index("index").sort_index()

    return df_sorted["_occurrence_index"]


def ensure_txn_id(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure Txn_ID exists. If missing, create stable hash from key fields using occurrence-based scheme.
    """
    df = df.copy()
    if "Txn_ID" not in df.columns:
        df["Txn_ID"] = ""

    # Ensure RowOrder exists (needed for tie-breaker sort fallback)
    if "RowOrder" not in df.columns:
        df["RowOrder"] = range(len(df))
    else:
        row_missing = df["RowOrder"].isna() | df["RowOrder"].astype(str).str.strip().eq("")
        if row_missing.any():
            df.loc[row_missing, "RowOrder"] = df.loc[row_missing].index

    # Generate Txn_IDs for rows that are missing them
    txn_series = df["Txn_ID"]
    missing_mask = txn_series.isna() | txn_series.astype(str).str.strip().eq("")

    if missing_mask.any():
        # Generate occurrence indices for ALL rows (needed for deterministic sorting)
        df["_occurrence_index"] = _generate_occurrence_indices(df)

        # Generate Txn_IDs only for missing rows
        df.loc[missing_mask, "Txn_ID"] = df.loc[missing_mask].apply(
            lambda row: _mk_txn_id(row, row["_occurrence_index"]), axis=1
        )

        # Drop helper column
        df = df.drop(columns=["_occurrence_index"])

    # HARDENING: Explicit invariant assertions
    # 1. No blank Txn_IDs
    final_mask = df["Txn_ID"].isna() | df["Txn_ID"].astype(str).str.strip().eq("")
    if final_mask.any():
        sample = df.loc[final_mask, ["Date", "Amount", "Description", "SourceFile"]].head(5)
        raise ValueError(f"FATAL: Blank Txn_IDs detected:\n{sample.to_string(index=False)}")

    # 2. Txn_ID uniqueness must equal row count
    txn_id_count = len(df)
    txn_id_unique = df["Txn_ID"].nunique()
    if txn_id_unique != txn_id_count:
        raise ValueError(
            f"FATAL: Txn_ID uniqueness violation. "
            f"Expected {txn_id_count} unique Txn_IDs, got {txn_id_unique}. "
            f"Difference: {txn_id_count - txn_id_unique} duplicates."
        )

    # 3. Legacy duplicate check (for detailed diagnostics)
    dup_mask = df["Txn_ID"].duplicated(keep=False)
    if dup_mask.any():
        sample = df.loc[dup_mask, ["Txn_ID", "Date", "Amount", "Description", "SourceFile", "Balance"]].head(10)
        raise ValueError(f"CRITICAL: Txn_ID collision after occurrence index assignment:\n{sample.to_string(index=False)}")

    return df


# ======================================================
# COMPILED PATTERNS (compile once)
# ======================================================

P_BALANCE = compile_patterns(BALANCE_PATTERNS)
P_INTEREST = compile_patterns(INTEREST_PATTERNS)
P_SALARY = compile_patterns(SALARY_PATTERNS)
P_TAX = compile_patterns(TAX_PATTERNS)
P_MORTGAGE = compile_patterns(MORTGAGE_PATTERNS)
P_MCST = compile_patterns(MCST_PATTERNS)
P_PROP_DOWN = compile_patterns(PROPERTY_DOWNPAYMENT_PATTERNS)
P_RENOV = compile_patterns(RENOVATION_PATTERNS)
P_CARFIN = compile_patterns(CAR_FINANCE_PATTERNS)
P_TRANSFER = compile_patterns(TRANSFER_PATTERNS)
P_TRUST_INTERNAL = compile_patterns(TRUST_BANK_INTERNAL_PATTERNS)
P_INS_IN = compile_patterns(INS_INFLOW_MARKERS)
P_INS_OUT = compile_patterns(INS_OUTFLOW_MARKERS)

# R17+ compiled patterns
P_SRS = compile_patterns(SRS_PATTERNS)
P_CPF = compile_patterns(CPF_PATTERNS)
P_TOWN_COUNCIL = compile_patterns(TOWN_COUNCIL_PATTERNS)
P_GOVT_PAYOUT = compile_patterns(GOVT_PAYOUT_PATTERNS)
P_ATM = compile_patterns(ATM_WITHDRAWAL_PATTERNS)
P_TELECOM = compile_patterns(TELECOM_PATTERNS)
P_TRANSIT = compile_patterns(TRANSIT_PATTERNS)
P_BANK_FEES = compile_patterns(BANK_FEE_PATTERNS)


# ======================================================
# CLASSIFICATION RESULT
# ======================================================

@dataclass(frozen=True)
class ClassResult:
    record_type: str
    flow_nature: str
    cashflow_statement: str
    econ_l1: str
    econ_l2: str
    asset_context: str
    stability_class: str
    baseline_eligible: bool
    event_tag: str
    bank_rail: str
    rule_id: str
    rule_explanation: str
    managerial_l1: str
    managerial_l2: str
    is_cc_settlement: bool


# ======================================================
# CLASSIFIER (single-pass, priority-ordered)
# ======================================================

def classify_row(desc: str, amount: float) -> ClassResult:
    d = norm(desc)
    rail = infer_bank_rail(d)

    # 0) Balance B/F / Summary (Non-Cash)
    if has_any(d, P_BALANCE):
        return ClassResult(
            record_type="SUMMARY",
            flow_nature=FLOW_NATURE["NON_CASH"],
            cashflow_statement=CFS["NON_CASH"],
            econ_l1=EP_L1["NON_CASH"],
            econ_l2="BALANCE_BF",
            asset_context=ASSET_CTX["UNKNOWN"],
            stability_class=STABILITY["ONE_OFF"],
            baseline_eligible=False,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R00_BALANCE_BF",
            rule_explanation="Balance B/F is a non-cash summary line; excluded from cashflow analytics.",
            managerial_l1="NON-CASH",
            managerial_l2="BALANCE_BF",
            is_cc_settlement=False,
        )

    # 1) Salary / Employment income (hard-protect)
    if amount > 0 and (has_any(d, P_SALARY) or contains_any_token(d, SALARY_EMPLOYERS)):
        employer = next((e for e in SALARY_EMPLOYERS if e.upper() in d), "EMPLOYER")
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["INCOME"],
            cashflow_statement=CFS["OPERATING"],
            econ_l1=EP_L1["INCOME"],
            econ_l2="SALARY",
            asset_context=ASSET_CTX["GENERAL"],
            stability_class=STABILITY["STRUCTURAL"],
            baseline_eligible=True,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R01_SALARY",
            rule_explanation=f"Detected salary income (employer token: {employer}). Income can never be classified as lifestyle.",
            managerial_l1="INCOME",
            managerial_l2="SALARY",
            is_cc_settlement=False,
        )

    # 2) Interest income
    if amount > 0 and has_any(d, P_INTEREST):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["INCOME"],
            cashflow_statement=CFS["OPERATING"],
            econ_l1=EP_L1["INCOME"],
            econ_l2="INTEREST",
            asset_context=ASSET_CTX["FINANCIAL"],
            stability_class=STABILITY["SEMI"],
            baseline_eligible=True,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R02_INTEREST",
            rule_explanation="Interest credited (bank/bonus interest). Operating income.",
            managerial_l1="INCOME",
            managerial_l2="INTEREST",
            is_cc_settlement=False,
        )

    # 3) Explicit Trust Bank internal transfers (authoritative user rule)
    if has_any(d, P_TRUST_INTERNAL):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["TRANSFER"],
            cashflow_statement=CFS["TRANSFER"],
            econ_l1=EP_L1["TRANSFER"],
            econ_l2="INTERNAL_TRANSFER",
            asset_context=ASSET_CTX["GENERAL"],
            stability_class=STABILITY["STRUCTURAL"],
            baseline_eligible=False,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R03_TRUST_INTERNAL",
            rule_explanation="Trust Bank OTHR Transfer is internal inter-bank fund reallocation; neutralized as Transfer.",
            managerial_l1="TRANSFER",
            managerial_l2="INTERNAL_TRANSFER",
            is_cc_settlement=False,
        )

    # 4) Property purchase downpayment / completion-related (Cheque / DR CO CHARGES)
    if amount < 0 and has_any(d, P_PROP_DOWN):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["EXPENSE"],
            cashflow_statement=CFS["INVESTING"],
            econ_l1=EP_L1["HOUSING"],
            econ_l2="PROPERTY_PURCHASE",
            asset_context=ASSET_CTX["PROPERTY"],
            stability_class=STABILITY["ONE_OFF"],
            baseline_eligible=False,
            event_tag=EVENT_TAG["PROPERTY_ACQ"],
            bank_rail=rail,
            rule_id="R04_PROPERTY_DOWNPAYMENT",
            rule_explanation="Cheque/DR CO CHARGES treated as property downpayment (cash -> property asset). Investing cashflow.",
            managerial_l1="HOUSING",
            managerial_l2="PROPERTY_PURCHASE",
            is_cc_settlement=False,
        )

    # 5) Taxes (IRAS etc.)
    if amount < 0 and has_any(d, P_TAX):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["EXPENSE"],
            cashflow_statement=CFS["OPERATING"],
            econ_l1=EP_L1["TAXES"],
            econ_l2="IRAS_TAX",
            asset_context=ASSET_CTX["GENERAL"],
            stability_class=STABILITY["SEMI"],
            baseline_eligible=True,
            event_tag=EVENT_TAG["TAX_EVENT"],
            bank_rail=rail,
            rule_id="R05_TAX",
            rule_explanation="IRAS-related tax payment. Operating cashflow.",
            managerial_l1="TAXES",
            managerial_l2="IRAS_TAX",
            is_cc_settlement=False,
        )

    # 6) Mortgage payments (financing)
    if amount < 0 and has_any(d, P_MORTGAGE):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["EXPENSE"],
            cashflow_statement=CFS["FINANCING"],
            econ_l1=EP_L1["DEBT_SERVICE"],
            econ_l2="MORTGAGE_PAYMENT",
            asset_context=ASSET_CTX["PROPERTY"],
            stability_class=STABILITY["STRUCTURAL"],
            baseline_eligible=True,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R06_MORTGAGE",
            rule_explanation="Detected mortgage/housing loan payment. Financing cashflow (debt service).",
            managerial_l1="DEBT_SERVICE",
            managerial_l2="MORTGAGE_PAYMENT",
            is_cc_settlement=False,
        )

    # 7) Car financing
    if amount < 0 and has_any(d, P_CARFIN):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["EXPENSE"],
            cashflow_statement=CFS["FINANCING"],
            econ_l1=EP_L1["DEBT_SERVICE"],
            econ_l2="CAR_LOAN_PAYMENT",
            asset_context=ASSET_CTX["CAR"],
            stability_class=STABILITY["STRUCTURAL"],
            baseline_eligible=True,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R07_CAR_LOAN",
            rule_explanation="Detected car loan payment. Financing cashflow (debt service).",
            managerial_l1="DEBT_SERVICE",
            managerial_l2="CAR_LOAN_PAYMENT",
            is_cc_settlement=False,
        )

    # 8) Renovation / property capex improvements (investing)
    if amount < 0 and has_any(d, P_RENOV):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["EXPENSE"],
            cashflow_statement=CFS["INVESTING"],
            econ_l1=EP_L1["HOUSING"],
            econ_l2="RENOVATION",
            asset_context=ASSET_CTX["PROPERTY"],
            stability_class=STABILITY["ONE_OFF"],
            baseline_eligible=False,
            event_tag=EVENT_TAG["RENOVATION"],
            bank_rail=rail,
            rule_id="R08_RENOVATION",
            rule_explanation="Renovation/capex improvement detected. Investing cashflow (property).",
            managerial_l1="HOUSING",
            managerial_l2="RENOVATION",
            is_cc_settlement=False,
        )

    # 9) MCST / condo maintenance (operating housing)
    if amount < 0 and has_any(d, P_MCST):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["EXPENSE"],
            cashflow_statement=CFS["OPERATING"],
            econ_l1=EP_L1["HOUSING"],
            econ_l2="HOA_CONDO_FEES",
            asset_context=ASSET_CTX["PROPERTY"],
            stability_class=STABILITY["SEMI"],
            baseline_eligible=True,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R09_MCST",
            rule_explanation="MCST/condo maintenance fees are operating housing costs (not lifestyle).",
            managerial_l1="HOUSING",
            managerial_l2="HOA_CONDO_FEES",
            is_cc_settlement=False,
        )

    # 10) Insurance flows
    if contains_any_token(d, INSURERS):
        if amount > 0 and has_any(d, P_INS_IN):
            return ClassResult(
                record_type="TRANSACTION",
                flow_nature=FLOW_NATURE["INCOME"],
                cashflow_statement=CFS["OPERATING"],
                econ_l1=EP_L1["INCOME"],
                econ_l2="INSURANCE_PAYOUT",
                asset_context=ASSET_CTX["GENERAL"],
                stability_class=STABILITY["VARIABLE"],
                baseline_eligible=False,
                event_tag=EVENT_TAG["NONE"],
                bank_rail=rail,
                rule_id="R10_INS_IN",
                rule_explanation="Insurer-related inflow (refund/payout). Treated as operating income.",
                managerial_l1="INCOME",
                managerial_l2="INSURANCE_PAYOUT",
                is_cc_settlement=False,
            )

        if amount < 0:
            return ClassResult(
                record_type="TRANSACTION",
                flow_nature=FLOW_NATURE["EXPENSE"],
                cashflow_statement=CFS["OPERATING"],
                econ_l1=EP_L1["INSURANCE"],
                econ_l2="PREMIUM",
                asset_context=ASSET_CTX["GENERAL"],
                stability_class=STABILITY["STRUCTURAL"],
                baseline_eligible=True,
                event_tag=EVENT_TAG["NONE"],
                bank_rail=rail,
                rule_id="R11_INS_OUT",
                rule_explanation="Insurer-related outflow treated as insurance premium (operating).",
                managerial_l1="INSURANCE",
                managerial_l2="PREMIUM",
                is_cc_settlement=False,
            )

    # 11) Credit card settlement (economic view decision can change later)
    issuer = detect_cc_issuer(d)
    if amount < 0 and issuer and ("BILL PAYMENT" in d or "CC" in d or "CARDS" in d):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["EXPENSE"],
            cashflow_statement=CFS["FINANCING"],
            econ_l1=EP_L1["DEBT_SERVICE"],
            econ_l2=f"CREDIT_CARD_SETTLEMENT_{issuer}",
            asset_context=ASSET_CTX["GENERAL"],
            stability_class=STABILITY["SEMI"],
            baseline_eligible=True,
            event_tag=EVENT_TAG["NONE"],
            bank_rail="CARD",
            rule_id="R12_CC_SETTLEMENT",
            rule_explanation="Credit card settlement is liability repayment; classify as financing (debt service).",
            managerial_l1="LIFESTYLE",
            managerial_l2="CREDIT_CARD_SPEND_PROXY",
            is_cc_settlement=True,
        )

    # 12) Internal transfers by self-entity tokens (generic)
    if (has_any(d, P_TRANSFER) or rail in ("FAST", "PAYNOW", "GIRO")) and contains_any_token(d, SELF_ENTITIES):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["TRANSFER"],
            cashflow_statement=CFS["TRANSFER"],
            econ_l1=EP_L1["TRANSFER"],
            econ_l2="INTERNAL_TRANSFER",
            asset_context=ASSET_CTX["GENERAL"],
            stability_class=STABILITY["STRUCTURAL"],
            baseline_eligible=False,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R13_INTERNAL_TRANSFER",
            rule_explanation="Detected self-controlled transfer (ownership unchanged). Neutralized as Transfer.",
            managerial_l1="TRANSFER",
            managerial_l2="INTERNAL_TRANSFER",
            is_cc_settlement=False,
        )

    # ======================================================
    # R17+ NEW RULES (to reduce fallback pressure)
    # ======================================================

    # Priority 17: SRS (Supplementary Retirement Scheme) contributions
    if amount < 0 and has_any(d, P_SRS):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["EXPENSE"],
            cashflow_statement=CFS["INVESTING"],
            econ_l1=EP_L1["SAVINGS_INVESTING"],
            econ_l2="SRS_CONTRIBUTION",
            asset_context=ASSET_CTX["FINANCIAL"],
            stability_class=STABILITY["SEMI"],
            baseline_eligible=True,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R17_SRS_CONTRIBUTION",
            rule_explanation="SRS contribution detected. Investing cashflow (retirement savings).",
            managerial_l1="SAVINGS",
            managerial_l2="SRS_CONTRIBUTION",
            is_cc_settlement=False,
        )

    # Priority 18: CPF voluntary top-ups
    if amount < 0 and has_any(d, P_CPF):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["EXPENSE"],
            cashflow_statement=CFS["INVESTING"],
            econ_l1=EP_L1["SAVINGS_INVESTING"],
            econ_l2="CPF_VOLUNTARY",
            asset_context=ASSET_CTX["FINANCIAL"],
            stability_class=STABILITY["SEMI"],
            baseline_eligible=True,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R18_CPF_VOLUNTARY",
            rule_explanation="Voluntary CPF top-up detected. Investing cashflow (retirement savings).",
            managerial_l1="SAVINGS",
            managerial_l2="CPF_VOLUNTARY",
            is_cc_settlement=False,
        )

    # Priority 19: Town Council / conservancy fees
    if amount < 0 and has_any(d, P_TOWN_COUNCIL):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["EXPENSE"],
            cashflow_statement=CFS["OPERATING"],
            econ_l1=EP_L1["HOUSING"],
            econ_l2="TOWN_COUNCIL_FEES",
            asset_context=ASSET_CTX["PROPERTY"],
            stability_class=STABILITY["STRUCTURAL"],
            baseline_eligible=True,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R19_TOWN_COUNCIL",
            rule_explanation="Town Council / conservancy fees detected. Operating housing expense.",
            managerial_l1="HOUSING",
            managerial_l2="TOWN_COUNCIL_FEES",
            is_cc_settlement=False,
        )

    # Priority 20: Government payouts (inflows only)
    if amount > 0 and has_any(d, P_GOVT_PAYOUT):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["INCOME"],
            cashflow_statement=CFS["OPERATING"],
            econ_l1=EP_L1["INCOME"],
            econ_l2="GOVT_PAYOUT",
            asset_context=ASSET_CTX["GENERAL"],
            stability_class=STABILITY["VARIABLE"],
            baseline_eligible=False,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R20_GOVT_PAYOUT",
            rule_explanation="Government payout/grant detected. Operating income (non-baseline).",
            managerial_l1="INCOME",
            managerial_l2="GOVT_PAYOUT",
            is_cc_settlement=False,
        )

    # Priority 21: ATM cash withdrawals
    if amount < 0 and has_any(d, P_ATM):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["EXPENSE"],
            cashflow_statement=CFS["OPERATING"],
            econ_l1=EP_L1["LIFESTYLE"],
            econ_l2="CASH_WITHDRAWAL",
            asset_context=ASSET_CTX["GENERAL"],
            stability_class=STABILITY["VARIABLE"],
            baseline_eligible=False,
            event_tag=EVENT_TAG["NONE"],
            bank_rail="ATM",
            rule_id="R21_ATM_WITHDRAWAL",
            rule_explanation="ATM cash withdrawal detected. Operating lifestyle expense (cash-based spending).",
            managerial_l1="LIFESTYLE",
            managerial_l2="CASH_WITHDRAWAL",
            is_cc_settlement=False,
        )

    # Priority 22: Telecom utilities
    if amount < 0 and has_any(d, P_TELECOM):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["EXPENSE"],
            cashflow_statement=CFS["OPERATING"],
            econ_l1=EP_L1["LIFESTYLE"],
            econ_l2="TELECOM",
            asset_context=ASSET_CTX["GENERAL"],
            stability_class=STABILITY["STRUCTURAL"],
            baseline_eligible=True,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R22_TELECOM",
            rule_explanation="Telecom bill detected. Operating lifestyle expense (utilities).",
            managerial_l1="LIFESTYLE",
            managerial_l2="TELECOM",
            is_cc_settlement=False,
        )

    # Priority 23: Public transit
    if amount < 0 and has_any(d, P_TRANSIT):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["EXPENSE"],
            cashflow_statement=CFS["OPERATING"],
            econ_l1=EP_L1["LIFESTYLE"],
            econ_l2="TRANSIT",
            asset_context=ASSET_CTX["GENERAL"],
            stability_class=STABILITY["STRUCTURAL"],
            baseline_eligible=True,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R23_TRANSIT",
            rule_explanation="Public transit expense detected. Operating lifestyle expense.",
            managerial_l1="LIFESTYLE",
            managerial_l2="TRANSIT",
            is_cc_settlement=False,
        )

    # Priority 24: Bank fees and charges
    if amount < 0 and has_any(d, P_BANK_FEES):
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["EXPENSE"],
            cashflow_statement=CFS["OPERATING"],
            econ_l1=EP_L1["FEES"],
            econ_l2="BANK_FEES",
            asset_context=ASSET_CTX["GENERAL"],
            stability_class=STABILITY["STRUCTURAL"],
            baseline_eligible=True,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R24_BANK_FEES",
            rule_explanation="Bank fees/charges detected. Operating fees expense.",
            managerial_l1="FEES",
            managerial_l2="BANK_FEES",
            is_cc_settlement=False,
        )

    # ======================================================
    # FALLBACK RULES (R14-R16)
    # ======================================================

    # 13) Generic inflows (fallback)
    if amount > 0:
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["INCOME"],
            cashflow_statement=CFS["OPERATING"],
            econ_l1=EP_L1["INCOME"],
            econ_l2="OTHER_INCOME",
            asset_context=ASSET_CTX["GENERAL"],
            stability_class=STABILITY["VARIABLE"],
            baseline_eligible=False,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R14_OTHER_INCOME",
            rule_explanation="Unmapped inflow treated as other operating income (review later if needed).",
            managerial_l1="INCOME",
            managerial_l2="OTHER_INCOME",
            is_cc_settlement=False,
        )

    # 14) Generic outflows (fallback)
    if amount < 0:
        return ClassResult(
            record_type="TRANSACTION",
            flow_nature=FLOW_NATURE["EXPENSE"],
            cashflow_statement=CFS["OPERATING"],
            econ_l1=EP_L1["LIFESTYLE"],
            econ_l2="DISCRETIONARY",
            asset_context=ASSET_CTX["GENERAL"],
            stability_class=STABILITY["VARIABLE"],
            baseline_eligible=False,
            event_tag=EVENT_TAG["NONE"],
            bank_rail=rail,
            rule_id="R15_GENERIC_OUTFLOW",
            rule_explanation="Unmapped outflow treated as lifestyle discretionary (conservative fallback).",
            managerial_l1="LIFESTYLE",
            managerial_l2="DISCRETIONARY",
            is_cc_settlement=False,
        )

    # 15) Zero amount (rare)
    return ClassResult(
        record_type="TRANSACTION",
        flow_nature=FLOW_NATURE["NON_CASH"],
        cashflow_statement=CFS["NON_CASH"],
        econ_l1=EP_L1["NON_CASH"],
        econ_l2="ACCOUNTING_ADJUSTMENT",
        asset_context=ASSET_CTX["UNKNOWN"],
        stability_class=STABILITY["ONE_OFF"],
        baseline_eligible=False,
        event_tag=EVENT_TAG["NONE"],
        bank_rail=rail,
        rule_id="R16_ZERO_ADJ",
        rule_explanation="Zero-amount row treated as non-cash adjustment (should be rare).",
        managerial_l1="NON-CASH",
        managerial_l2="ACCOUNTING_ADJUSTMENT",
        is_cc_settlement=False,
    )


# ======================================================
# OVERRIDES (XLSX)
# ======================================================

OVERRIDE_SHEET = "Overrides"
OVERRIDE_REQUIRED_COLS = [
    "Txn_ID",
    "Cashflow_Statement",
    "Economic_Purpose_L1",
    "Economic_Purpose_L2",
    "Managerial_Purpose_L1",
    "Managerial_Purpose_L2",
    "Baseline_Eligible",
    "Override_Reason",
    "Enabled",
]

OVERRIDE_FIELDS = [
    "Cashflow_Statement",
    "Economic_Purpose_L1",
    "Economic_Purpose_L2",
    "Managerial_Purpose_L1",
    "Managerial_Purpose_L2",
    "Baseline_Eligible",
    "Override_Reason",
]

def load_overrides() -> pd.DataFrame:
    """
    Load overrides.xlsx if configured. Returns empty df if not present/configured.
    """
    override_xlsx = os.getenv("CLASSIFY_OVERRIDE_XLSX", "").strip()
    override_dir = os.getenv("CLASSIFY_OVERRIDE_DIR", "").strip()

    path: Optional[Path] = None
    if override_xlsx:
        path = Path(override_xlsx)
    elif override_dir:
        path = Path(override_dir) / "overrides.xlsx"

    if not path:
        return pd.DataFrame(columns=OVERRIDE_REQUIRED_COLS)

    if not path.exists():
        # Keep non-fatal: no overrides yet
        return pd.DataFrame(columns=OVERRIDE_REQUIRED_COLS)

    ov = pd.read_excel(path, sheet_name=OVERRIDE_SHEET)

    # Basic shape enforcement
    for c in OVERRIDE_REQUIRED_COLS:
        if c not in ov.columns:
            ov[c] = pd.NA

    # Normalize booleans
    ov["Enabled"] = ov["Enabled"].astype(str).str.upper().isin(["TRUE", "1", "YES", "Y"])
    # Baseline_Eligible can be blank; preserve NA
    def _parse_bool_or_na(x):
        if pd.isna(x) or str(x).strip() == "":
            return pd.NA
        return str(x).strip().upper() in ["TRUE", "1", "YES", "Y"]
    ov["Baseline_Eligible"] = ov["Baseline_Eligible"].apply(_parse_bool_or_na)

    # Drop rows without Txn_ID
    ov["Txn_ID"] = ov["Txn_ID"].astype(str).str.strip()
    ov = ov[ov["Txn_ID"].str.len() > 0].copy()

    # Keep enabled rows only
    ov = ov[ov["Enabled"] == True].copy()

    # Create an audit ID per override row
    ov = ov.reset_index(drop=True)
    ov["Override_ID"] = ov.index.map(lambda i: f"OVR_{i+1:04d}")

    # Index by Txn_ID (enforce uniqueness)
    dup = ov["Txn_ID"].duplicated(keep=False)
    if dup.any():
        dups = ov.loc[dup, "Txn_ID"].tolist()
        raise ValueError(f"Duplicate Txn_ID in overrides.xlsx (must be unique): {dups[:10]}")

    # ------------------------------------------------------
    # Derive managerial fields inside overrides table
    # Only when:
    # - Managerial fields are blank, AND
    # - Economic fields are provided in the override row
    # This avoids per-row derivation work during apply_overrides().
    # ------------------------------------------------------
    def _has_value(cell) -> bool:
        if pd.isna(cell):
            return False
        s = str(cell).strip()
        return not (s == "" or s.upper() in ["(BLANK)", "BLANK"])

    # Normalize economic text (if present) for mapping consistency
    for c in ["Economic_Purpose_L1", "Economic_Purpose_L2", "Managerial_Purpose_L1", "Managerial_Purpose_L2"]:
        if c in ov.columns:
            ov[c] = ov[c].apply(lambda x: str(x).strip().upper() if _has_value(x) else x)

    derived_mgr_l1 = []
    derived_mgr_l2 = []

    for _, r in ov.iterrows():
        econ_l1 = r.get("Economic_Purpose_L1", pd.NA)
        econ_l2 = r.get("Economic_Purpose_L2", pd.NA)

        mgr_l1 = r.get("Managerial_Purpose_L1", pd.NA)
        mgr_l2 = r.get("Managerial_Purpose_L2", pd.NA)

        econ_ready = _has_value(econ_l1) and _has_value(econ_l2)
        mgr_l1_missing = not _has_value(mgr_l1)
        mgr_l2_missing = not _has_value(mgr_l2)

        if econ_ready and (mgr_l1_missing or mgr_l2_missing):
            d_l1, d_l2 = MANAGERIAL_DERIVE_MAP.get(
                (str(econ_l1).strip().upper(), str(econ_l2).strip().upper()),
                (str(econ_l1).strip().upper(), str(econ_l2).strip().upper()),
            )
            derived_mgr_l1.append(d_l1 if mgr_l1_missing else str(mgr_l1).strip().upper())
            derived_mgr_l2.append(d_l2 if mgr_l2_missing else str(mgr_l2).strip().upper())
        else:
            derived_mgr_l1.append(str(mgr_l1).strip().upper() if _has_value(mgr_l1) else pd.NA)
            derived_mgr_l2.append(str(mgr_l2).strip().upper() if _has_value(mgr_l2) else pd.NA)

    ov["Managerial_Purpose_L1"] = derived_mgr_l1
    ov["Managerial_Purpose_L2"] = derived_mgr_l2

    return ov



def apply_overrides(df: pd.DataFrame, ov: pd.DataFrame) -> pd.DataFrame:
    """
    Apply overrides by Txn_ID. Only non-blank cells override.
    Adds: Was_Overridden, Override_ID_Applied, Override_Reason.
    """
    def _override_has_value(o_row: pd.Series, col: str) -> bool:
        if col not in o_row:
            return False
        val = o_row[col]
        if pd.isna(val):
            return False
        raw = str(val).strip()
        if raw == "" or raw.upper() in ["(BLANK)", "BLANK"]:
            return False
        return True

    df = df.copy()
    df["Was_Overridden"] = False
    df["Override_ID_Applied"] = ""
    # Keep existing Override_Reason if present, otherwise init
    if "Override_Reason" not in df.columns:
        df["Override_Reason"] = ""

    if ov is None or ov.empty:
        return df

    ov_map = ov.set_index("Txn_ID")

    # Apply row-wise (still fine for personal volumes)
    for i, row in df.iterrows():
        txn_id = str(row.get("Txn_ID", "")).strip()
        if not txn_id or txn_id not in ov_map.index:
            continue

        o = ov_map.loc[txn_id]
        mgr_l1_provided = _override_has_value(o, "Managerial_Purpose_L1")
        mgr_l2_provided = _override_has_value(o, "Managerial_Purpose_L2")

        # override specific fields if provided
        for col in OVERRIDE_FIELDS:
            if col not in o:
                continue
            val = o[col]
            if pd.isna(val) or str(val).strip() == "" or str(val).strip().upper() in ["(BLANK)", "BLANK"]:
                continue
            # Map override col names to df col names
            if col == "Managerial_Purpose_L1":
                df.at[i, "Managerial_Purpose_L1"] = str(val).strip().upper()
            elif col == "Managerial_Purpose_L2":
                df.at[i, "Managerial_Purpose_L2"] = str(val).strip().upper()
            elif col == "Override_Reason":
                # Append to existing reason for audit safety.
                new_reason = str(val).strip()
                existing = str(df.at[i, "Override_Reason"]).strip()
                df.at[i, "Override_Reason"] = f"{existing} | {new_reason}" if existing else new_reason
            elif col == "Baseline_Eligible":
                # could be NA -> skip; else bool
                if pd.isna(val):
                    continue
                df.at[i, "Baseline_Eligible"] = bool(val)
            else:
                # Cashflow_Statement / Economic_Purpose_L1/L2
                df.at[i, col] = str(val).strip().upper()

        econ_l1 = str(df.at[i, "Economic_Purpose_L1"]).strip().upper()
        econ_l2 = str(df.at[i, "Economic_Purpose_L2"]).strip().upper()
        cashflow_stmt = str(df.at[i, "Cashflow_Statement"]).strip().upper()
        # Derive from final economic purpose; credit card prefix match is issuer-agnostic.
        if econ_l1 == "DEBT_SERVICE" and econ_l2.startswith("CREDIT_CARD_SETTLEMENT"):
            derived = ("LIFESTYLE", "CREDIT_CARD_SPEND_PROXY")
        else:
            derived = MANAGERIAL_DERIVE_MAP.get((econ_l1, econ_l2), (econ_l1, econ_l2))
        # Transfer short-circuit (business invariant).
        if cashflow_stmt == "TRANSFER":
            derived = ("TRANSFER", "INTERNAL_TRANSFER")

        if not mgr_l1_provided:
            df.at[i, "Managerial_Purpose_L1"] = derived[0]
        if not mgr_l2_provided:
            df.at[i, "Managerial_Purpose_L2"] = derived[1]

        df.at[i, "Was_Overridden"] = True
        df.at[i, "Override_ID_Applied"] = str(o.get("Override_ID", "")).strip()

    return df


# ======================================================
# PIPELINE
# ======================================================

def classify_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if "Amount" not in df.columns:
        raise ValueError("Missing required column: Amount")

    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0.0)
    df["Description"] = df.get("Description", "").astype(str)

    df = ensure_txn_id(df)

    results = df.apply(
        lambda r: classify_row(r["Description"], float(r["Amount"])),
        axis=1
    )

    # Expand dataclass fields
    df["Record_Type"] = results.map(lambda x: x.record_type)
    df["Flow_Nature"] = results.map(lambda x: x.flow_nature)
    df["Cashflow_Statement"] = results.map(lambda x: x.cashflow_statement)
    df["Economic_Purpose_L1"] = results.map(lambda x: x.econ_l1)
    df["Economic_Purpose_L2"] = results.map(lambda x: x.econ_l2)
    df["Asset_Context"] = results.map(lambda x: x.asset_context)
    df["Stability_Class"] = results.map(lambda x: x.stability_class)
    df["Baseline_Eligible"] = results.map(lambda x: bool(x.baseline_eligible))
    df["Event_Tag"] = results.map(lambda x: x.event_tag)
    df["Bank_Rail"] = results.map(lambda x: x.bank_rail)
    df["Rule_ID"] = results.map(lambda x: x.rule_id)
    df["Rule_Explanation"] = results.map(lambda x: x.rule_explanation)

    df["Managerial_Purpose_L1"] = results.map(lambda x: x.managerial_l1)
    df["Managerial_Purpose_L2"] = results.map(lambda x: x.managerial_l2)
    df["Is_CC_Settlement"] = results.map(lambda x: bool(x.is_cc_settlement))

    # Backward-compatible columns for existing dashboard_app.py
    df["Cashflow_Section"] = df["Cashflow_Statement"]
    df["Category_L1"] = df["Economic_Purpose_L1"]
    df["Category_L2"] = df["Economic_Purpose_L2"]
    df["Instrument"] = df["Bank_Rail"]

    # Counterparty fields (keep simple for now)
    df["Counterparty_Norm"] = df["Description"].astype(str).str.upper()
    if "Counterparty_Core" not in df.columns:
        df["Counterparty_Core"] = df["Counterparty_Norm"].str.slice(0, 80)

    # Apply overrides last (wins deterministically)
    ov = load_overrides()
    df = apply_overrides(df, ov)

    return df


# ======================================================
# ENTRY POINT
# ======================================================

def _self_check() -> None:
    df = pd.DataFrame(
        [
            {
                "Date": "2024-01-01",
                "YearMonth": "2024-01",
                "Amount": 1200.0,
                "Description": "FAST TRANSFER WEILUN",
                "SourceFile": "test.csv",
                "RowOrder": 1,
                "Txn_ID": "",
                "Balance": 5000.0,
                "Withdrawals": 0.0,
                "Deposits": 1200.0,
            },
            {
                "Date": "2024-01-02",
                "YearMonth": "2024-01",
                "Amount": -50.0,
                "Description": "MISC EXPENSE",
                "SourceFile": "test.csv",
                "RowOrder": 2,
                "Balance": 4950.0,
                "Withdrawals": 50.0,
                "Deposits": 0.0,
            },
        ]
    )

    base = classify_df(df)

    assert base["Txn_ID"].astype(str).str.strip().ne("").all()
    transfer_row = base[base["Description"].str.upper() == "FAST TRANSFER WEILUN"].iloc[0]
    assert transfer_row["Cashflow_Statement"] == "TRANSFER"
    assert transfer_row["Managerial_Purpose_L1"] == "TRANSFER"

    txn_id = base[base["Description"].str.upper() == "MISC EXPENSE"]["Txn_ID"].iloc[0]
    ov = pd.DataFrame(
        [
            {
                "Txn_ID": txn_id,
                "Cashflow_Statement": "",
                "Economic_Purpose_L1": "HOUSING",
                "Economic_Purpose_L2": "RENOVATION",
                "Managerial_Purpose_L1": "",
                "Managerial_Purpose_L2": "",
                "Baseline_Eligible": pd.NA,
                "Override_Reason": "Test override",
                "Enabled": True,
            }
        ]
    )
    out = apply_overrides(base, ov)
    row = out[out["Description"].str.upper() == "MISC EXPENSE"].iloc[0]
    assert row["Managerial_Purpose_L1"] == "HOUSING"
    assert row["Managerial_Purpose_L2"] == "RENOVATION"

    # ======================================================
    # R17+ RULE TESTS
    # ======================================================

    # Test R17: SRS contribution
    result = classify_row("MISC DEBIT (SRS) PIB2111215947733448 SRS CONT 180544711", -15300.0)
    assert result.rule_id == "R17_SRS_CONTRIBUTION", f"Expected R17, got {result.rule_id}"
    assert result.cashflow_statement == "INVESTING"
    assert result.econ_l2 == "SRS_CONTRIBUTION"

    # Test R18: CPF voluntary top-up
    result = classify_row("PAYNOW-FAST PIB2212048050781800 CENTRAL PROVIDENT FU OTHR QARR3509001840826488", -8000.0)
    assert result.rule_id == "R18_CPF_VOLUNTARY", f"Expected R18, got {result.rule_id}"
    assert result.cashflow_statement == "INVESTING"
    assert result.econ_l2 == "CPF_VOLUNTARY"

    # Test R19: Town Council fees
    result = classify_row("INWARD DR - GIRO TCSC C91528519610 WESTCOASTTOWNCOUNCIL WA-GDED-0000015339733", -49.1)
    assert result.rule_id == "R19_TOWN_COUNCIL", f"Expected R19, got {result.rule_id}"
    assert result.cashflow_statement == "OPERATING"
    assert result.econ_l2 == "TOWN_COUNCIL_FEES"

    # Test R20: Government payout (inflow)
    result = classify_row("INWARD CR - GIRO OTHR OTHER GOVT PAYOUT GOVT PAYOUT", 187.5)
    assert result.rule_id == "R20_GOVT_PAYOUT", f"Expected R20, got {result.rule_id}"
    assert result.cashflow_statement == "OPERATING"
    assert result.econ_l2 == "GOVT_PAYOUT"

    # Test R21: ATM withdrawal
    result = classify_row("CASH WITHDRAWAL-ATM 79608204", -100.0)
    assert result.rule_id == "R21_ATM_WITHDRAWAL", f"Expected R21, got {result.rule_id}"
    assert result.bank_rail == "ATM"
    assert result.econ_l2 == "CASH_WITHDRAWAL"

    # Test R22: Telecom
    result = classify_row("BILL PAYMENT MBK-SINGTEL 35044279", -57.57)
    assert result.rule_id == "R22_TELECOM", f"Expected R22, got {result.rule_id}"
    assert result.econ_l2 == "TELECOM"

    # Test R23: Transit
    result = classify_row("NETS DEBIT-CONSUMER TRANSIT LI10565000 79608204", -60.0)
    assert result.rule_id == "R23_TRANSIT", f"Expected R23, got {result.rule_id}"
    assert result.econ_l2 == "TRANSIT"

    # Test MBK self-transfer (caught by R13 after adding account to SELF_ENTITIES)
    result = classify_row("Funds Transfer mBK-3493939244", -1000.0)
    assert result.rule_id == "R13_INTERNAL_TRANSFER", f"Expected R13, got {result.rule_id}"
    assert result.cashflow_statement == "TRANSFER"
    assert result.econ_l2 == "INTERNAL_TRANSFER"

    # Test R24: Bank fees
    result = classify_row("Cheque Charges", -0.75)
    assert result.rule_id == "R24_BANK_FEES", f"Expected R24, got {result.rule_id}"
    assert result.cashflow_statement == "OPERATING"
    assert result.econ_l2 == "BANK_FEES"

    print("R17+ rule tests passed.")


def main():
    load_dotenv()

    if os.getenv("RUN_SELF_CHECKS") == "1":
        _self_check()
        print("Self-checks passed.")
        return

    input_csv = os.getenv("CLASSIFY_INPUT_CSV")
    output_dir = os.getenv("CLASSIFY_OUTPUT_DIR")

    if not input_csv or not output_dir:
        raise ValueError("Check CLASSIFY_INPUT_CSV and CLASSIFY_OUTPUT_DIR in .env")

    in_path = Path(input_csv)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(in_path)
    df_out = classify_df(df)

    output_path = out_dir / "classified_transactions_v3.csv"
    df_out.to_csv(output_path, index=False)

    print(f"Classification complete -> {output_path}")


if __name__ == "__main__":
    main()
