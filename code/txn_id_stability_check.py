#!/usr/bin/env python3
"""
txn_id_stability_check.py

Validates that Txn_ID generation is order-independent and deterministic.

Usage:
  python txn_id_stability_check.py --csv "path/to/combined_cleaned.csv"

The script:
1. Loads the CSV with existing Txn_IDs
2. Recomputes Txn_IDs on original row order
3. Recomputes Txn_IDs on shuffled row order (fixed seed)
4. Validates:
   - Original Txn_IDs match recomputed Txn_IDs (original order)
   - Recomputed Txn_IDs are identical between original and shuffled order
   - No duplicate Txn_IDs exist
   - Base_key collision groups are properly differentiated

Exits with code 0 on success, 1 on failure.
"""

import argparse
import hashlib
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Tuple


def _normalize_str(s: str) -> str:
    """Normalize string by collapsing whitespace."""
    return " ".join(str(s).split())


def _collapse_ws(s: str) -> str:
    """Collapse all whitespace to single spaces."""
    return " ".join(s.split())


def _canon_date(s: str) -> str:
    """Canonicalize date string to YYYY-MM-DD format."""
    try:
        ts = pd.to_datetime(s, errors="coerce", dayfirst=True)
        if pd.isna(ts):
            return ""
        return ts.strftime("%Y-%m-%d")
    except Exception:
        return ""


def _canon_desc(s: str) -> str:
    """Canonicalize description by normalizing and uppercasing."""
    normalized = _normalize_str(s)
    return normalized.upper()


def _mk_row_fingerprint(row: pd.Series) -> str:
    """
    Generate deterministic row fingerprint from stable content fields.

    Used as final tie-breaker for occurrence index assignment (NOT part of Txn_ID hash).

    Fields included (all stable, content-based):
    - Date, YearMonth, Amount, Description, SourceFile (base_key fields)
    - Balance, Withdrawals, Deposits (additional numeric differentiators)

    Returns:
        40-character SHA-1 hex hash of canonicalized row content
    """
    date = _canon_date(row.get("Date", ""))
    if date == "":
        date = "NA"

    year_month = _collapse_ws(_normalize_str(row.get("YearMonth", "")))
    amount = str(row.get("Amount", "0"))
    desc = _canon_desc(row.get("Description", ""))
    source = _canon_desc(row.get("SourceFile", ""))
    balance = str(row.get("Balance", "NaN"))
    withdrawals = str(row.get("Withdrawals", "NaN"))
    deposits = str(row.get("Deposits", "NaN"))

    fingerprint_key = "|".join([
        date, year_month, amount, desc, source,
        balance, withdrawals, deposits
    ])

    return hashlib.sha1(fingerprint_key.encode("utf-8")).hexdigest()


def _generate_occurrence_indices(df: pd.DataFrame) -> pd.Series:
    """
    Assign occurrence indices to transactions with identical base_keys.

    Tie-breaker sort priority (all order-independent):
      1. Balance (ascending, NaN last)
      2. Withdrawals (ascending, NaN last)
      3. Deposits (ascending, NaN last)
      4. Amount (ascending, NaN last)
      5. row_fingerprint (ascending) - final deterministic tie-breaker

    Returns:
        pd.Series: 1-based occurrence index for each row
    """
    df_work = df.copy()

    # Create base_key (excluding RowOrder!)
    df_work["_base_key"] = (
        df_work["Date"].apply(_canon_date).replace("", "NA") + "|" +
        df_work["YearMonth"].apply(lambda x: _collapse_ws(_normalize_str(x))) + "|" +
        df_work["Amount"].apply(lambda x: str(int(round(float(x) * 100)))) + "|" +
        df_work["Description"].apply(_canon_desc) + "|" +
        df_work["SourceFile"].apply(_canon_desc)
    )

    # Create row fingerprint (deterministic tie-breaker)
    df_work["_row_fingerprint"] = df_work.apply(_mk_row_fingerprint, axis=1)

    # Sort by base_key + tie-breakers (NO RowOrder!)
    df_sorted = df_work.sort_values(
        by=["_base_key", "Balance", "Withdrawals", "Deposits", "Amount", "_row_fingerprint"],
        kind="mergesort",  # Stable sort
        na_position="last"
    ).reset_index(drop=False)

    # Assign occurrence indices within each base_key group
    df_sorted["_occurrence_index"] = df_sorted.groupby("_base_key").cumcount() + 1

    # Restore original index order
    df_sorted = df_sorted.set_index("index").sort_index()

    return df_sorted["_occurrence_index"]


def _mk_txn_id(row: pd.Series, occurrence_index: int) -> str:
    """
    Generate deterministic Txn_ID from base_key + occurrence index.

    Formula: sha1(base_key|OCC{occurrence_index:03d})

    Args:
        row: Transaction row
        occurrence_index: 1-based occurrence counter for this base_key

    Returns:
        40-character SHA-1 hex hash
    """
    date = _canon_date(row.get("Date", ""))
    if date == "":
        date = "NA"

    year_month = _collapse_ws(_normalize_str(row.get("YearMonth", "")))
    amount_cents = str(int(round(float(row.get("Amount", 0)) * 100)))
    desc = _canon_desc(row.get("Description", ""))
    source = _canon_desc(row.get("SourceFile", ""))

    base_key = "|".join([date, year_month, amount_cents, desc, source])
    occ_suffix = f"OCC{occurrence_index:03d}"
    txn_key = f"{base_key}|{occ_suffix}"

    return hashlib.sha1(txn_key.encode("utf-8")).hexdigest()


def compute_txn_ids(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute Txn_IDs for all rows in DataFrame.

    Args:
        df: DataFrame with required columns (Date, YearMonth, Amount, Description, SourceFile, Balance, Withdrawals, Deposits)

    Returns:
        DataFrame with added Txn_ID column
    """
    df_out = df.copy()

    # Generate occurrence indices
    occurrence_indices = _generate_occurrence_indices(df_out)

    # Generate Txn_IDs
    txn_ids = []
    for idx, row in df_out.iterrows():
        occ_idx = occurrence_indices.loc[idx]
        txn_id = _mk_txn_id(row, occ_idx)
        txn_ids.append(txn_id)

    df_out["Txn_ID"] = txn_ids

    return df_out


def create_stable_join_key(row: pd.Series) -> str:
    """
    Create stable join key for matching rows across different orderings.

    Uses: Date | Amount | Description | SourceFile | Balance

    This is the semantic identity of a transaction (excluding Txn_ID).
    """
    date = _canon_date(row.get("Date", ""))
    amount = str(row.get("Amount", ""))
    desc = _canon_desc(row.get("Description", ""))
    source = _canon_desc(row.get("SourceFile", ""))
    balance = str(row.get("Balance", "NaN"))

    return f"{date}|{amount}|{desc}|{source}|{balance}"


def validate_txn_id_stability(csv_path: Path) -> Tuple[bool, dict]:
    """
    Validate Txn_ID stability across different row orderings.

    Args:
        csv_path: Path to CSV file with existing Txn_IDs

    Returns:
        Tuple of (success: bool, report: dict)
    """
    print("="*80)
    print("TXN_ID STABILITY VALIDATION")
    print("="*80)
    print(f"Input CSV: {csv_path}\n")

    # Load original CSV
    df_original = pd.read_csv(csv_path)
    print(f"Loaded {len(df_original)} rows\n")

    # Verify required columns exist
    required_cols = ["Date", "YearMonth", "Amount", "Description", "SourceFile",
                     "Balance", "Withdrawals", "Deposits", "Txn_ID"]
    missing_cols = [col for col in required_cols if col not in df_original.columns]
    if missing_cols:
        return False, {"error": f"Missing required columns: {missing_cols}"}

    # Create stable join key for matching rows
    df_original["_join_key"] = df_original.apply(create_stable_join_key, axis=1)

    # Check for duplicate join keys
    dup_join_keys = df_original[df_original["_join_key"].duplicated(keep=False)]
    if not dup_join_keys.empty:
        print(f"WARNING: {len(dup_join_keys)} rows have duplicate semantic keys")
        print("This is expected for base_key collision groups with identical Balance values.\n")

    # Test 1: Recompute Txn_IDs on original order
    print("-"*80)
    print("TEST 1: Recompute Txn_IDs on original row order")
    print("-"*80)

    df_recomputed_original = compute_txn_ids(df_original.drop(columns=["Txn_ID", "_join_key"]))
    df_recomputed_original["_join_key"] = df_recomputed_original.apply(create_stable_join_key, axis=1)

    # Match by join key
    merged_original = df_original.merge(
        df_recomputed_original[["_join_key", "Txn_ID"]],
        on="_join_key",
        suffixes=("_original", "_recomputed"),
        how="inner"
    )

    matches_original = (merged_original["Txn_ID_original"] == merged_original["Txn_ID_recomputed"]).sum()
    mismatches_original = len(merged_original) - matches_original

    print(f"Matched rows: {len(merged_original)}/{len(df_original)}")
    print(f"Identical Txn_IDs: {matches_original}/{len(merged_original)}")
    print(f"Mismatches: {mismatches_original}")

    if mismatches_original > 0:
        print("\nERROR: Txn_IDs do not match original values!")
        print("\nSample mismatches:")
        mismatches = merged_original[merged_original["Txn_ID_original"] != merged_original["Txn_ID_recomputed"]]
        print(mismatches[["Date", "Amount", "Description", "Balance", "Txn_ID_original", "Txn_ID_recomputed"]].head(10))
        return False, {
            "test": "original_order_recompute",
            "matches": matches_original,
            "mismatches": mismatches_original,
            "total": len(merged_original)
        }

    print("PASS: Recomputed Txn_IDs match original values\n")

    # Test 2: Recompute Txn_IDs on shuffled order
    print("-"*80)
    print("TEST 2: Recompute Txn_IDs on shuffled row order (seed=42)")
    print("-"*80)

    df_shuffled = df_original.drop(columns=["Txn_ID", "_join_key"]).sample(frac=1, random_state=42).reset_index(drop=True)
    df_recomputed_shuffled = compute_txn_ids(df_shuffled)
    df_recomputed_shuffled["_join_key"] = df_recomputed_shuffled.apply(create_stable_join_key, axis=1)

    # Match shuffled results to original via join key
    merged_shuffled = df_recomputed_original.merge(
        df_recomputed_shuffled[["_join_key", "Txn_ID"]],
        on="_join_key",
        suffixes=("_original_order", "_shuffled_order"),
        how="inner"
    )

    matches_shuffled = (merged_shuffled["Txn_ID_original_order"] == merged_shuffled["Txn_ID_shuffled_order"]).sum()
    mismatches_shuffled = len(merged_shuffled) - matches_shuffled

    print(f"Matched rows: {len(merged_shuffled)}/{len(df_shuffled)}")
    print(f"Identical Txn_IDs: {matches_shuffled}/{len(merged_shuffled)}")
    print(f"Mismatches: {mismatches_shuffled}")

    if mismatches_shuffled > 0:
        print("\nERROR: Txn_IDs differ between original and shuffled order!")
        print("\nSample mismatches:")
        mismatches = merged_shuffled[merged_shuffled["Txn_ID_original_order"] != merged_shuffled["Txn_ID_shuffled_order"]]
        print(mismatches[["Date", "Amount", "Description", "Balance", "Txn_ID_original_order", "Txn_ID_shuffled_order"]].head(10))
        return False, {
            "test": "shuffled_order_stability",
            "matches": matches_shuffled,
            "mismatches": mismatches_shuffled,
            "total": len(merged_shuffled)
        }

    print("PASS: Txn_IDs are identical between original and shuffled order\n")

    # Test 3: Check for duplicate Txn_IDs
    print("-"*80)
    print("TEST 3: Check for duplicate Txn_IDs")
    print("-"*80)

    duplicate_txn_ids = df_recomputed_original[df_recomputed_original["Txn_ID"].duplicated(keep=False)]

    print(f"Total Txn_IDs: {len(df_recomputed_original)}")
    print(f"Unique Txn_IDs: {df_recomputed_original['Txn_ID'].nunique()}")
    print(f"Duplicates: {len(duplicate_txn_ids)}")

    if not duplicate_txn_ids.empty:
        print("\nERROR: Duplicate Txn_IDs detected!")
        print("\nSample duplicates:")
        print(duplicate_txn_ids[["Date", "Amount", "Description", "Balance", "Txn_ID"]].head(10))
        return False, {
            "test": "duplicate_check",
            "duplicates": len(duplicate_txn_ids),
            "total": len(df_recomputed_original)
        }

    print("PASS: All Txn_IDs are unique\n")

    # Test 4: Analyze base_key collision groups
    print("-"*80)
    print("TEST 4: Analyze base_key collision groups")
    print("-"*80)

    # Create base_key for analysis
    df_analysis = df_recomputed_original.copy()
    df_analysis["_base_key"] = (
        df_analysis["Date"].apply(_canon_date).replace("", "NA") + "|" +
        df_analysis["YearMonth"].apply(lambda x: _collapse_ws(_normalize_str(x))) + "|" +
        df_analysis["Amount"].apply(lambda x: str(int(round(float(x) * 100)))) + "|" +
        df_analysis["Description"].apply(_canon_desc) + "|" +
        df_analysis["SourceFile"].apply(_canon_desc)
    )

    collision_groups = df_analysis.groupby("_base_key").size()
    collisions = collision_groups[collision_groups > 1]

    print(f"Total base_keys: {len(collision_groups)}")
    print(f"Collision groups (size > 1): {len(collisions)}")

    if len(collisions) > 0:
        print(f"\nCollision group sizes:")
        print(collisions.value_counts().sort_index())

        print(f"\nSample collision groups:")
        for base_key in collisions.head(5).index:
            group = df_analysis[df_analysis["_base_key"] == base_key]
            print(f"\nBase_key: {base_key[:80]}...")
            print(f"Group size: {len(group)}")
            print(group[["Date", "Amount", "Description", "Balance", "Txn_ID"]].to_string(index=False))

            # Verify Txn_IDs are unique within collision group
            if group["Txn_ID"].nunique() != len(group):
                print("ERROR: Collision group has duplicate Txn_IDs!")
                return False, {
                    "test": "collision_group_uniqueness",
                    "base_key": base_key,
                    "group_size": len(group),
                    "unique_txn_ids": group["Txn_ID"].nunique()
                }
    else:
        print("No collision groups detected (all base_keys are unique)")

    print("\nPASS: All collision groups have unique Txn_IDs\n")

    # Final summary
    print("="*80)
    print("VALIDATION SUMMARY")
    print("="*80)
    print("RESULT: ALL TESTS PASSED")
    print(f"\nTotal transactions: {len(df_original)}")
    print(f"Unique Txn_IDs: {df_recomputed_original['Txn_ID'].nunique()}")
    print(f"Base_key collision groups: {len(collisions)}")
    print(f"\nTxn_ID generation is ORDER-INDEPENDENT and DETERMINISTIC")
    print("="*80 + "\n")

    return True, {
        "total_transactions": len(df_original),
        "unique_txn_ids": df_recomputed_original["Txn_ID"].nunique(),
        "collision_groups": len(collisions),
        "all_tests_passed": True
    }


def main():
    parser = argparse.ArgumentParser(
        description="Validate Txn_ID stability and order-independence"
    )
    parser.add_argument(
        "--csv",
        required=True,
        help="Path to CSV file with Txn_IDs (e.g., combined_cleaned.csv)"
    )

    args = parser.parse_args()
    csv_path = Path(args.csv)

    if not csv_path.exists():
        print(f"ERROR: File not found: {csv_path}")
        exit(1)

    success, report = validate_txn_id_stability(csv_path)

    if not success:
        print(f"\nVALIDATION FAILED")
        print(f"Error details: {report}")
        exit(1)

    exit(0)


if __name__ == "__main__":
    main()
