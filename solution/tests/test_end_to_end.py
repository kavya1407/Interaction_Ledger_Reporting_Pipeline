"""End-to-end integration and idempotency tests on synthetic data."""
from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
import sys
import pytest
from pathlib import Path
from solution.cli import ingest_drop, generate_reports
from solution.config import REPO_ROOT


def test_end_to_end_pipeline_and_idempotency(tmp_path):
    data_dir = tmp_path / "data"
    # Generate fresh small test dataset on the fly
    gen_script = REPO_ROOT / "assignment-a-data" / "tools" / "generate_data.py"
    subprocess.run(
        [sys.executable, str(gen_script), "--seed", "42", "--scale", "small", "--out", str(data_dir)],
        check=True,
        capture_output=True,
    )

    db_path = tmp_path / "test_pipeline.db"
    reports_dir = tmp_path / "reports"

    # Step 1: Ingest Drop 1
    ingest_drop(data_dir / "drop_1", db_path=db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT stat_key, stat_value FROM pipeline_stats")
    stats_after_drop1 = {r["stat_key"]: r["stat_value"] for r in cursor.fetchall()}
    assert stats_after_drop1["lead_rows_total"] > 0
    assert stats_after_drop1["events_total"] > 0
    conn.close()

    # Step 2: Ingest Drop 2
    ingest_drop(data_dir / "drop_2", db_path=db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT stat_key, stat_value FROM pipeline_stats")
    stats_after_drop2 = {r["stat_key"]: r["stat_value"] for r in cursor.fetchall()}
    assert stats_after_drop2["lead_rows_total"] > stats_after_drop1["lead_rows_total"]
    assert stats_after_drop2["events_total"] > stats_after_drop1["events_total"]
    conn.close()

    # Step 3: Test Idempotency: Re-ingest Drop 1 and Drop 2
    ingest_drop(data_dir / "drop_1", db_path=db_path)
    ingest_drop(data_dir / "drop_2", db_path=db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT stat_key, stat_value FROM pipeline_stats")
    stats_after_reingest = {r["stat_key"]: r["stat_value"] for r in cursor.fetchall()}
    # Idempotency guarantee: stats MUST NOT CHANGE!
    assert stats_after_reingest == stats_after_drop2
    conn.close()

    # Step 4: Generate reports
    generate_reports(reports_dir, db_path=db_path)

    # Verify all 5 report files exist
    assert (reports_dir / "batch_summary.csv").exists()
    assert (reports_dir / "daily_overview.csv").exists()
    assert (reports_dir / "lead_report.csv").exists()
    assert (reports_dir / "rejections.csv").exists()
    assert (reports_dir / "data_quality.json").exists()

    # Verify batch_summary.csv header and sorting
    with open(reports_dir / "batch_summary.csv", "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert reader[0] == ["workflow_id", "batch_id", "total_leads", "yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed"]
        # Verify sorted by workflow_id, batch_id
        keys = [(r[0], r[1]) for r in reader[1:]]
        assert keys == sorted(keys)

    # Verify daily_overview.csv header and sorting
    with open(reports_dir / "daily_overview.csv", "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert reader[0] == ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"]
        keys = [(r[0], r[1]) for r in reader[1:]]
        assert keys == sorted(keys)
        # Check connectivity rate formatting (4 decimals)
        for row in reader[1:]:
            rate = row[4]
            assert len(rate.split(".")[1]) == 4

    # Verify lead_report.csv header and sorting
    with open(reports_dir / "lead_report.csv", "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert reader[0] == ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls", "connected_calls", "last_call_at", "current_disposition", "ever_success"]
        keys = [(r[0], r[1]) for r in reader[1:]]
        assert keys == sorted(keys)
        for row in reader[1:]:
            # Check phone_masked starts with +91******
            assert row[3].startswith("+91******")
            # Check ever_success is 'true' or 'false'
            assert row[9] in ("true", "false")

    # Verify rejections.csv header and sorting (no PII)
    with open(reports_dir / "rejections.csv", "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert reader[0] == ["workflow_id", "batch_id", "row_number", "reason"]
        keys = [(r[0], r[1], int(r[2])) for r in reader[1:]]
        assert keys == sorted(keys)

    # Verify data_quality.json keys
    with open(reports_dir / "data_quality.json", "r", encoding="utf-8") as f:
        dq = json.load(f)
        expected_keys = {
            "lead_rows_total", "lead_rows_rejected", "rejections_by_reason",
            "duplicate_lead_rows_merged", "field_warnings", "events_total",
            "malformed_events", "duplicate_events_dropped", "orphan_events",
            "unknown_disposition_events"
        }
        assert set(dq.keys()) == expected_keys
