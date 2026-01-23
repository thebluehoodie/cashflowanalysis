#!/usr/bin/env python3
"""
migrate_overrides.py

Migrates overrides.xlsx from old RowOrder-based Txn_IDs to new occurrence-based Txn_IDs.

Usage:
  python migrate_overrides.py \\
      --old_txn_csv "classified_transactions_v3_old.csv" \\
      --new_txn_csv "classified_transactions_v3_new.csv" \\
      --old_overrides "overrides.xlsx" \\
      --output "overrides_migrated.xlsx"

The script matches transactions using semantic key (Date, Amount, Description, SourceFile, Balance)
and remaps Txn_IDs in the override file.

HARDENING: All match keys are canonically normalized to prevent false mismatches.
"""

import argparse
import hashlib
import pandas as pd
from pathlib import Path
from typing import Dict, Tuple


def _canon_date(s: str) -> str:
    """Canonicalize date string to YYYY-MM-DD format."""
    try:
        ts = pd.to_datetime(s, errors="coerce", dayfirst=True)
        if pd.isna(ts):
            return ""
        return ts.strftime("%Y-%m-%d")
    except Exception:
        return ""


def _canon_amount_cents(a) -> str:
    """Canonicalize amount to integer cents string."""
    try:
        v = float(a)
    except (ValueError, TypeError):
        return "0"
    return str(int(round(v * 100)))


def _normalize_str(s: str) -> str:
    """Normalize string by collapsing whitespace."""
    return " ".join(str(s).split())


def _collapse_ws(s: str) -> str:
    """Collapse all whitespace to single spaces."""
    return " ".join(s.split())


def _canon_text(s: str) -> str:
    """Canonicalize text by normalizing and uppercasing."""
    normalized = _normalize_str(s)
    return normalized.upper()


def normalize_for_matching(s: str) -> str:
    """Normalize string for fuzzy matching (legacy - use _canon_text instead)."""
    return str(s).strip().upper()


def create_match_key(row: pd.Series, include_balance: bool = True) -> str:
    """
    Create semantic matching key from transaction attributes.

    Uses: Date | Amount | Description | SourceFile | Balance (if include_balance=True)

    HARDENING: All fields canonically normalized to prevent false mismatches:
    - Date → YYYY-MM-DD
    - Amount → integer cents
    - Balance → integer cents
    - Description → UPPER + collapse whitespace
    - SourceFile → UPPER + collapse whitespace
    """
    date = _canon_date(row.get("Date", ""))
    if date == "":
        date = "NA"

    amount = _canon_amount_cents(row.get("Amount", "0"))

    desc = _canon_text(row.get("Description", ""))
    source = _canon_text(row.get("SourceFile", ""))

    if include_balance:
        balance_val = row.get("Balance", None)
        balance = _canon_amount_cents(balance_val) if pd.notna(balance_val) else "NaN"
        return f"{date}|{amount}|{desc}|{source}|{balance}"
    else:
        return f"{date}|{amount}|{desc}|{source}"


def match_old_to_new(old_df: pd.DataFrame, new_df: pd.DataFrame) -> pd.DataFrame:
    """
    Match transactions across Txn_ID schemes using semantic key.

    Returns: DataFrame with columns [Old_Txn_ID, New_Txn_ID, Match_Confidence, Match_Key, Match_Method]
    """
    # Create match keys with Balance to disambiguate collisions
    old_df["_match_key"] = old_df.apply(lambda r: create_match_key(r, include_balance=True), axis=1)
    new_df["_match_key"] = new_df.apply(lambda r: create_match_key(r, include_balance=True), axis=1)

    # Also create fallback keys without Balance
    old_df["_match_key_nobal"] = old_df.apply(lambda r: create_match_key(r, include_balance=False), axis=1)
    new_df["_match_key_nobal"] = new_df.apply(lambda r: create_match_key(r, include_balance=False), axis=1)

    # Build lookup maps for new dataset
    new_map_with_balance = new_df.set_index("_match_key")["Txn_ID"].to_dict()
    new_map_no_balance = new_df.groupby("_match_key_nobal")["Txn_ID"].apply(list).to_dict()

    # Also build reverse lookup for diagnostics
    old_details = old_df.set_index("_match_key")[["Date", "Amount", "Description", "SourceFile", "Balance"]].to_dict('index')

    matches = []

    for idx, old_row in old_df.iterrows():
        old_txn_id = old_row["Txn_ID"]
        match_key_with_bal = old_row["_match_key"]
        match_key_no_bal = old_row["_match_key_nobal"]

        # Try exact match with Balance first
        if match_key_with_bal in new_map_with_balance:
            matches.append({
                "Old_Txn_ID": old_txn_id,
                "New_Txn_ID": new_map_with_balance[match_key_with_bal],
                "Match_Confidence": "EXACT",
                "Match_Method": "Semantic_Key_With_Balance",
                "Match_Key": match_key_with_bal,
                "Date": old_row["Date"],
                "Amount": old_row["Amount"],
                "Description": str(old_row["Description"])[:80],
                "SourceFile": old_row["SourceFile"],
                "Balance": old_row.get("Balance", "NaN")
            })
        # Fallback: try match without Balance
        elif match_key_no_bal in new_map_no_balance:
            candidates = new_map_no_balance[match_key_no_bal]
            if len(candidates) == 1:
                # Unique match without Balance
                matches.append({
                    "Old_Txn_ID": old_txn_id,
                    "New_Txn_ID": candidates[0],
                    "Match_Confidence": "HIGH",
                    "Match_Method": "Semantic_Key_No_Balance",
                    "Match_Key": match_key_no_bal,
                    "Date": old_row["Date"],
                    "Amount": old_row["Amount"],
                    "Description": str(old_row["Description"])[:80],
                    "SourceFile": old_row["SourceFile"],
                    "Balance": old_row.get("Balance", "NaN")
                })
            else:
                # Multiple candidates - ambiguous
                matches.append({
                    "Old_Txn_ID": old_txn_id,
                    "New_Txn_ID": None,
                    "Match_Confidence": "AMBIGUOUS",
                    "Match_Method": f"Multiple_Candidates_{len(candidates)}",
                    "Match_Key": match_key_no_bal,
                    "Date": old_row["Date"],
                    "Amount": old_row["Amount"],
                    "Description": str(old_row["Description"])[:80],
                    "SourceFile": old_row["SourceFile"],
                    "Balance": old_row.get("Balance", "NaN")
                })
        else:
            # No match found
            matches.append({
                "Old_Txn_ID": old_txn_id,
                "New_Txn_ID": None,
                "Match_Confidence": "UNMATCHED",
                "Match_Method": None,
                "Match_Key": match_key_with_bal,
                "Date": old_row["Date"],
                "Amount": old_row["Amount"],
                "Description": str(old_row["Description"])[:80],
                "SourceFile": old_row["SourceFile"],
                "Balance": old_row.get("Balance", "NaN")
            })

    return pd.DataFrame(matches)


def migrate_overrides(old_txn_csv: Path, new_txn_csv: Path, old_overrides_xlsx: Path, output_path: Path):
    """
    Main migration function.

    Args:
        old_txn_csv: Path to classified transactions with old Txn_IDs
        new_txn_csv: Path to classified transactions with new Txn_IDs
        old_overrides_xlsx: Path to existing overrides.xlsx
        output_path: Path for migrated overrides output
    """
    print(f"Loading old transactions from: {old_txn_csv}")
    old_df = pd.read_csv(old_txn_csv)

    print(f"Loading new transactions from: {new_txn_csv}")
    new_df = pd.read_csv(new_txn_csv)

    print(f"Loading old overrides from: {old_overrides_xlsx}")
    old_ov = pd.read_excel(old_overrides_xlsx, sheet_name="Overrides")

    print(f"\nTransaction counts:")
    print(f"  Old dataset: {len(old_df)} rows")
    print(f"  New dataset: {len(new_df)} rows")
    print(f"  Overrides:   {len(old_ov)} rows")

    # Create match mapping
    print("\nMatching old→new Txn_IDs...")
    match_df = match_old_to_new(old_df, new_df)

    # Merge overrides with match mapping
    print("Remapping override Txn_IDs...")
    old_ov_enriched = old_ov.merge(
        match_df,
        left_on="Txn_ID",
        right_on="Old_Txn_ID",
        how="left"
    )

    # Backup old Txn_ID and replace with new
    old_ov_enriched["Old_Txn_ID_Backup"] = old_ov_enriched["Txn_ID"]
    old_ov_enriched["Txn_ID"] = old_ov_enriched["New_Txn_ID"].fillna(old_ov_enriched["Txn_ID"])

    # Add migration status
    old_ov_enriched["Migration_Status"] = old_ov_enriched["Match_Confidence"].fillna("UNMATCHED")

    # Select output columns (original columns + audit trail)
    original_cols = old_ov.columns.tolist()
    audit_cols = ["Migration_Status", "Old_Txn_ID_Backup", "Match_Key", "Date", "Amount", "Description", "SourceFile"]
    output_cols = original_cols + audit_cols

    new_ov = old_ov_enriched[output_cols]

    # Write output
    print(f"\nWriting migrated overrides to: {output_path}")
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        new_ov.to_excel(writer, sheet_name="Overrides", index=False)

    # Report results
    print("\n" + "="*60)
    print("MIGRATION SUMMARY")
    print("="*60)
    print(f"Total overrides:        {len(old_ov)}")
    print(f"Exact matches:          {(new_ov['Migration_Status'] == 'EXACT').sum()}")
    print(f"High confidence:        {(new_ov['Migration_Status'] == 'HIGH').sum()}")
    print(f"Ambiguous:              {(new_ov['Migration_Status'] == 'AMBIGUOUS').sum()}")
    print(f"Unmatched:              {(new_ov['Migration_Status'] == 'UNMATCHED').sum()}")

    # Export ambiguous matches for manual review
    ambiguous = new_ov[new_ov["Migration_Status"] == "AMBIGUOUS"]
    if not ambiguous.empty:
        print("\n" + "!"*60)
        print("WARNING: Ambiguous matches detected!")
        print("!"*60)
        ambiguous_export = "migration_ambiguous.csv"
        ambiguous[["Old_Txn_ID_Backup", "Date", "Amount", "Description", "SourceFile", "Balance", "Override_Reason", "Match_Method"]].to_csv(
            ambiguous_export, index=False
        )
        print(f"\nAmbiguous overrides exported to: {ambiguous_export}")
        print("\nMultiple candidate matches found - manual review required:")
        for idx, row in ambiguous.iterrows():
            print(f"\n  Old_Txn_ID: {row['Old_Txn_ID_Backup']}")
            print(f"  Date: {row['Date']}, Amount: {row['Amount']}")
            print(f"  Description: {row['Description']}")
            print(f"  Reason: {row.get('Override_Reason', 'N/A')}")
            print(f"  Match_Method: {row['Match_Method']}")

    # Export unmatched for manual review
    unmatched = new_ov[new_ov["Migration_Status"] == "UNMATCHED"]
    if not unmatched.empty:
        print("\n" + "!"*60)
        print("WARNING: Unmatched overrides detected!")
        print("!"*60)
        unmatched_export = "migration_unmatched.csv"
        unmatched[["Old_Txn_ID_Backup", "Date", "Amount", "Description", "SourceFile", "Balance", "Override_Reason"]].to_csv(
            unmatched_export, index=False
        )
        print(f"\nUnmatched overrides exported to: {unmatched_export}")
        print("\nNo matching transactions found - manual review required:")
        for idx, row in unmatched.iterrows():
            print(f"\n  Old_Txn_ID: {row['Old_Txn_ID_Backup']}")
            print(f"  Date: {row['Date']}, Amount: {row['Amount']}")
            print(f"  Description: {row['Description']}")
            print(f"  Reason: {row.get('Override_Reason', 'N/A')}")

    if ambiguous.empty and unmatched.empty:
        print("\nAll overrides matched successfully!")

    print(f"\n{'='*60}")
    print(f"Output written to: {output_path}")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Migrate overrides.xlsx from old RowOrder-based to new occurrence-based Txn_IDs"
    )
    parser.add_argument(
        "--old_txn_csv",
        required=True,
        help="Path to classified transactions CSV with old Txn_IDs"
    )
    parser.add_argument(
        "--new_txn_csv",
        required=True,
        help="Path to classified transactions CSV with new Txn_IDs"
    )
    parser.add_argument(
        "--old_overrides",
        required=True,
        help="Path to existing overrides.xlsx"
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output path for migrated overrides.xlsx"
    )

    args = parser.parse_args()

    # Convert to Path objects
    old_txn = Path(args.old_txn_csv)
    new_txn = Path(args.new_txn_csv)
    old_ov = Path(args.old_overrides)
    output = Path(args.output)

    # Validate inputs exist
    if not old_txn.exists():
        raise FileNotFoundError(f"Old transactions file not found: {old_txn}")
    if not new_txn.exists():
        raise FileNotFoundError(f"New transactions file not found: {new_txn}")
    if not old_ov.exists():
        raise FileNotFoundError(f"Old overrides file not found: {old_ov}")

    # Run migration
    migrate_overrides(old_txn, new_txn, old_ov, output)


if __name__ == "__main__":
    main()
