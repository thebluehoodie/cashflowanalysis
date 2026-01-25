# Txn_ID Specification

Scope
- Generation and validation implemented in:
  - code/clean_bank_statement.py: _mk_txn_id, _generate_occurrence_indices, _mk_row_fingerprint
  - code/auto_classify_transactions.py: ensure_txn_id, _mk_txn_id, _generate_occurrence_indices, _mk_row_fingerprint

## Exact algorithm (plain language)
1) Canonicalize key fields
- Date: parse to YYYY-MM-DD; if blank, use "NA".
- YearMonth: collapse whitespace; must be present (error if blank).
- Amount: parse to float; convert to integer cents string (error if missing or invalid).
- Description: collapse whitespace and uppercase.
- SourceFile: collapse whitespace and uppercase; must be present (error if blank).

2) Build base_key (order-independent)
- base_key = Date | YearMonth | Amount_cents | Description | SourceFile

3) Assign occurrence_index within each base_key group (1-based)
- Within each base_key group, sort deterministically by:
  1. Balance (ascending, NaN last)
  2. Withdrawals (ascending, NaN last)
  3. Deposits (ascending, NaN last)
  4. Amount (ascending, NaN last)
  5. row_fingerprint (ascending)
- occurrence_index = 1, 2, 3, ... in this sorted order.

4) Build raw_key with occurrence index
- raw_key = base_key | OCC###
  - OCC### is 3-digit, zero-padded occurrence index (e.g., OCC001).

5) Hash to Txn_ID
- Txn_ID = SHA-1 hex digest of raw_key.

## Source fields used
Base key fields (hashed)
- Date (canonicalized)
- YearMonth (canonicalized)
- Amount (integer cents)
- Description (uppercased, whitespace-collapsed)
- SourceFile (uppercased, whitespace-collapsed)

Occurrence index inputs (used for ordering, not hashed)
- Balance
- Withdrawals
- Deposits
- Amount
- row_fingerprint

Row fingerprint inputs (for deterministic ordering only)
- Date, YearMonth, Amount, Description, SourceFile
- Balance, Withdrawals, Deposits
- All numeric fields canonicalized to integer cents
- row_fingerprint = SHA-1 hash of the concatenated, canonicalized content fields

## Order-independence rationale
- RowOrder is explicitly removed from base_key.
- Occurrence indices are assigned using deterministic content-based tie-breakers, not input row order.
- Stable mergesort is used for sorting to preserve deterministic outcomes.
- row_fingerprint provides a final deterministic tie-breaker when all other fields match.

## Failure modes and explicit rejections
- Missing YearMonth -> ValueError ("Missing YearMonth for Txn_ID.").
- Missing SourceFile -> ValueError ("Missing SourceFile for Txn_ID.").
- Missing or invalid Amount -> ValueError ("Missing Amount for Txn_ID." or "Invalid Amount for Txn_ID").
- True duplicates (identical base_key and row_fingerprint within a group) -> ValueError with diagnostics.
- Blank Txn_IDs after generation -> ValueError.
- Txn_ID uniqueness violation (duplicate hashes) -> ValueError.
- Combined output duplicate Txn_IDs (combined_cleaned.csv) -> ValueError.

## Where this is implemented
- code/clean_bank_statement.py
  - _mk_txn_id
  - _generate_occurrence_indices
  - _mk_row_fingerprint
- code/auto_classify_transactions.py
  - ensure_txn_id
  - _mk_txn_id
  - _generate_occurrence_indices
  - _mk_row_fingerprint

UNKNOWN
- There is no explicit collision handling beyond uniqueness checks; behavior on a real SHA-1 collision is not defined in code.
