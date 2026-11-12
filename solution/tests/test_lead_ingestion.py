"""Unit tests for lead ingestion: validation, rejections, warnings, and merging."""
from __future__ import annotations

import csv
import json
import sqlite3
import pytest
from pathlib import Path
from solution.db import init_db
from solution.lead_ingestion import LeadIngestor
from solution.schema_loader import SchemaCatalog
from solution.crypto import hash_phone


@pytest.fixture
def db_and_ingestor(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    catalog = SchemaCatalog()
    init_db(conn, catalog)
    ingestor = LeadIngestor(conn, catalog)
    return conn, ingestor, catalog, tmp_path


def test_schema_order_rejection_and_blank_rows(db_and_ingestor):
    conn, ingestor, catalog, tmp_path = db_and_ingestor
    cursor = conn.cursor()

    csv_file = tmp_path / "batch_001.csv"
    # Row 1: Valid row
    # Row 2: Blank row (should be skipped)
    # Row 3: Missing customer_name (first required field in motor_renewal schema)
    # Row 4: Invalid phone (second required field in motor_renewal schema)
    # Row 5: Missing policy_number (third required field)
    # Row 6: Valid row but invalid optional expiry and invalid optional language (2 warnings!)
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["customer_name", "phone", "policy_number", "policy_expiry", "premium_amount", "preferred_language"])
        writer.writerow(["Aarav Sharma", "9876543210", "POL101", "2026-10-15", "₹12,500", "en"])
        writer.writerow(["", "  ", "", "", "", ""])  # blank row
        writer.writerow(["", "9876543210", "POL102", "2026-10-15", "1000", "en"]) # missing name
        writer.writerow(["Diya Patel", "5123456789", "POL103", "2026-10-15", "1000", "en"]) # invalid phone
        writer.writerow(["Rohan Gupta", "9876543211", "", "2026-10-15", "1000", "en"]) # missing policy_number
        writer.writerow(["Kavya Nair", "9876543212", "POL105", "31/02/2026", "5000", "Hindi"]) # 2 field warnings

    ingestor.ingest_lead_file(csv_file, "motor_renewal", "drop_1", "batch_001")

    # Check stats
    cursor.execute("SELECT stat_key, stat_value FROM pipeline_stats")
    stats = {r["stat_key"]: r["stat_value"] for r in cursor.fetchall()}

    # 5 non-blank rows
    assert stats["lead_rows_total"] == 5
    # 3 rejected rows
    assert stats["lead_rows_rejected"] == 3
    # 2 accepted rows: 1 had 2 warnings (invalid date + invalid enum)
    assert stats["field_warnings"] == 2

    # Check rejections
    cursor.execute("SELECT row_number, reason FROM rejections ORDER BY row_number")
    rejections = cursor.fetchall()
    assert len(rejections) == 3
    # Row 2 in non-blank rows is the missing name row
    assert rejections[0]["row_number"] == 2
    assert rejections[0]["reason"] == "missing_required:customer_name"
    # Row 3 in non-blank rows is the invalid phone row
    assert rejections[1]["row_number"] == 3
    assert rejections[1]["reason"] == "invalid_phone"
    # Row 4 in non-blank rows is the missing policy_number row
    assert rejections[2]["row_number"] == 4
    assert rejections[2]["reason"] == "missing_required:policy_number"


def test_duplicate_lead_merging_last_write_wins(db_and_ingestor):
    conn, ingestor, catalog, tmp_path = db_and_ingestor
    cursor = conn.cursor()

    file1 = tmp_path / "batch_001.csv"
    with open(file1, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["customer_name", "phone", "policy_number", "premium_amount", "preferred_language"])
        writer.writerow(["Aarav Sharma", "+91 98765 43210", "POL-ORIG", "10000", "ta"])

    file2 = tmp_path / "batch_002.csv"
    with open(file2, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # Uses alias headers!
        writer.writerow(["Insured Name", "Mobile No", "Policy No", "Premium", "Language"])
        # Same phone, updated premium (12000), but language is empty (null)!
        writer.writerow(["Aarav S.", "9876543210", "POL-UPDATED", "12000", ""])

    ingestor.ingest_lead_file(file1, "motor_renewal", "drop_1", "batch_001")
    ingestor.ingest_lead_file(file2, "motor_renewal", "drop_1", "batch_002")

    # Check leads count
    cursor.execute("SELECT * FROM leads")
    leads = cursor.fetchall()
    assert len(leads) == 1

    lead = leads[0]
    # Permanent batch_id must remain batch_001
    assert lead["batch_id"] == "batch_001"
    # External ref updated to POL-UPDATED (last write wins)
    assert lead["external_ref"] == "POL-UPDATED"

    # Optional data
    opt = json.loads(lead["optional_data"])
    assert opt["premium_amount"] == 12000.00
    # Language was null in second file, so original 'ta' is preserved!
    assert opt["preferred_language"] == "ta"

    cursor.execute("SELECT stat_value FROM pipeline_stats WHERE stat_key = 'duplicate_lead_rows_merged'")
    dup_count = cursor.fetchone()["stat_value"]
    assert dup_count == 1
