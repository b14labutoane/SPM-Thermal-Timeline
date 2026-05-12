#!/usr/bin/env python3
"""One-time migration: read the Excel data file and insert into PostgreSQL.

Usage:
    python3 migrate_excel_to_pg.py [path/to/spm_data.xlsx]

Default path: generated/data/spm_data.xlsx

Requires: pandas, openpyxl, psycopg2-binary
"""

import sys
import os

import pandas as pd
import psycopg2

from db_config import DB_CONFIG


# Column mapping from Excel source names to PostgreSQL column names
_EXCEL_TO_PG = {
    # Temperature sheet
    "TEMPERATURE": "temperature",
    "Timestamp_NoTZ": "timestamp",
    "STATION_KEY": "station_key",
    # Cover History sheet
    "COVER_STATUS_ID": "cover_status_id",
    # Cover Status sheet
    "COVER_STATUS_DESCRIPTION": "cover_status_description",
}


def _rename_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename Excel columns to match PostgreSQL column names."""
    rename = {}
    for col in df.columns:
        if col in _EXCEL_TO_PG:
            rename[col] = _EXCEL_TO_PG[col]
    return df.rename(columns=rename)


def migrate(excel_path: str):
    """Read Excel file and insert all sheets into PostgreSQL."""
    if not os.path.exists(excel_path):
        print(f"[ERROR] File not found: {excel_path}")
        sys.exit(1)

    print(f"[1/4] Reading Excel file: {excel_path}")
    sheets = pd.read_excel(excel_path, sheet_name=None)
    print(f"       Sheets found: {list(sheets.keys())}")

    # Connect to PostgreSQL
    print(f"[2/4] Connecting to PostgreSQL database '{DB_CONFIG['database']}'...")
    conn = psycopg2.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        dbname=DB_CONFIG["database"],
    )
    cur = conn.cursor()

    # Clear existing data (idempotent migration)
    for table in ["temperature", "cover_history", "cover_status"]:
        cur.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
    conn.commit()
    print("[2/4] Cleared existing data.")

    # Classify sheets by their columns (same logic as original data_processor)
    temperature_df = None
    cover_history_df = None
    cover_status_df = None

    temp_cols = {"temperature", "timestamp", "station_key"}
    cover_hist_cols = {"cover_status_id", "timestamp", "station_key"}
    status_ref_cols = {"cover_status_id", "cover_status_description"}

    for _name, df in sheets.items():
        df = _rename_columns(df)
        cols = set(df.columns)

        if temp_cols.issubset(cols):
            temperature_df = df
        elif cover_hist_cols.issubset(cols):
            cover_history_df = df
        elif status_ref_cols.issubset(cols):
            cover_status_df = df
        else:
            if cover_status_df is None:
                cover_status_df = df

    # Insert temperature data
    if temperature_df is not None:
        print(f"[3/4] Inserting {len(temperature_df)} temperature rows...")
        for _, row in temperature_df.iterrows():
            cur.execute(
                "INSERT INTO temperature (temperature, timestamp, station_key) VALUES (%s, %s, %s)",
                (float(row["temperature"]), row["timestamp"], str(row["station_key"])),
            )
        conn.commit()
        print(f"       Inserted {len(temperature_df)} temperature rows.")
    else:
        print("[3/4] No temperature sheet found — skipping.")

    # Insert cover history
    if cover_history_df is not None:
        print(f"[3/4] Inserting {len(cover_history_df)} cover history rows...")
        for _, row in cover_history_df.iterrows():
            cur.execute(
                "INSERT INTO cover_history (cover_status_id, timestamp, station_key) VALUES (%s, %s, %s)",
                (int(row["cover_status_id"]), row["timestamp"], str(row["station_key"])),
            )
        conn.commit()
        print(f"       Inserted {len(cover_history_df)} cover history rows.")
    else:
        print("[3/4] No cover history sheet found — skipping.")

    # Insert cover status reference
    if cover_status_df is not None:
        print(f"[3/4] Inserting {len(cover_status_df)} cover status rows...")
        for _, row in cover_status_df.iterrows():
            cur.execute(
                "INSERT INTO cover_status (cover_status_id, cover_status_description) VALUES (%s, %s)",
                (int(row["cover_status_id"]), str(row["cover_status_description"])),
            )
        conn.commit()
        print(f"       Inserted {len(cover_status_df)} cover status rows.")
    else:
        print("[3/4] No cover status sheet found — skipping.")

    # Verify
    print("[4/4] Verifying row counts...")
    for table in ["temperature", "cover_history", "cover_status"]:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        print(f"       {table}: {count} rows")

    cur.close()
    conn.close()
    print("\nMigration complete!")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "generated/data/spm_data.xlsx"
    migrate(path)
