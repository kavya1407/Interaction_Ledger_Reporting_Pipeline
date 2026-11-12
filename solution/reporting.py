"""Report generator fulfilling the pipeline output contract."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from solution.ledger import LedgerService
from solution.schema_loader import SchemaCatalog


class ReportGenerator:
    def __init__(self, conn: sqlite3.Connection, catalog: SchemaCatalog):
        self.conn = conn
        self.catalog = catalog
        self.ledger = LedgerService(conn, catalog)

    def generate_all_reports(self, out_dir: Path | str) -> dict[str, Path]:
        """Generate all five report files into the output directory."""
        target_dir = Path(out_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        batch_summary_path = target_dir / "batch_summary.csv"
        daily_overview_path = target_dir / "daily_overview.csv"
        lead_report_path = target_dir / "lead_report.csv"
        rejections_path = target_dir / "rejections.csv"
        data_quality_path = target_dir / "data_quality.json"

        all_calls = self.ledger.get_all_linked_calls()
        lead_aggs = self.ledger.get_lead_aggregates(all_calls=all_calls)

        # 1. batch_summary.csv
        # Sorted by workflow_id, batch_id
        batch_map: dict[tuple[str, str], dict[str, int]] = {}
        for la in lead_aggs:
            key = (la.workflow_id, la.batch_id)
            if key not in batch_map:
                batch_map[key] = {
                    "total_leads": 0,
                    "yet_to_reach_out": 0,
                    "in_progress_telephony_failed": 0,
                    "in_progress_dialed": 0,
                    "completed": 0,
                }
            batch_map[key]["total_leads"] += 1
            batch_map[key][la.bucket] += 1

        sorted_batches = sorted(batch_map.keys(), key=lambda x: (x[0], x[1]))
        with open(batch_summary_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "workflow_id", "batch_id", "total_leads",
                "yet_to_reach_out", "in_progress_telephony_failed",
                "in_progress_dialed", "completed"
            ])
            for wf, b_id in sorted_batches:
                counts = batch_map[(wf, b_id)]
                writer.writerow([
                    wf,
                    b_id,
                    counts["total_leads"],
                    counts["yet_to_reach_out"],
                    counts["in_progress_telephony_failed"],
                    counts["in_progress_dialed"],
                    counts["completed"],
                ])

        # 2. daily_overview.csv
        # Sorted by workflow_id, date
        # One row per workflow per IST date that has at least one call
        daily_map: dict[tuple[str, str], dict] = {}
        for c in all_calls:
            key = (c.workflow_id, c.call_date)
            if key not in daily_map:
                daily_map[key] = {
                    "total_calls": 0,
                    "connected_calls": 0,
                    "success_leads": set(),
                }
            daily_map[key]["total_calls"] += 1
            if c.is_connected:
                daily_map[key]["connected_calls"] += 1
            if c.is_success:
                daily_map[key]["success_leads"].add(c.phone_hash)

        sorted_dailies = sorted(daily_map.keys(), key=lambda x: (x[0], x[1]))
        with open(daily_overview_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "workflow_id", "date", "total_calls", "connected_calls",
                "connectivity_rate", "success_leads"
            ])
            for wf, dt in sorted_dailies:
                ddata = daily_map[(wf, dt)]
                tot = ddata["total_calls"]
                conn = ddata["connected_calls"]
                rate_str = f"{conn / tot:.4f}" if tot > 0 else "0.0000"
                writer.writerow([
                    wf,
                    dt,
                    tot,
                    conn,
                    rate_str,
                    len(ddata["success_leads"]),
                ])

        # 3. lead_report.csv
        # Sorted by workflow_id, external_ref
        sorted_leads = sorted(lead_aggs, key=lambda l: (l.workflow_id, l.external_ref))
        with open(lead_report_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "workflow_id", "external_ref", "batch_id", "phone_masked",
                "bucket", "total_calls", "connected_calls", "last_call_at",
                "current_disposition", "ever_success"
            ])
            for l in sorted_leads:
                writer.writerow([
                    l.workflow_id,
                    l.external_ref,
                    l.batch_id,
                    l.phone_masked,
                    l.bucket,
                    l.total_calls,
                    l.connected_calls,
                    l.last_call_at,
                    l.current_disposition,
                    "true" if l.ever_success else "false",
                ])

        # 4. rejections.csv
        # Sorted by workflow_id, batch_id, row_number (numeric!)
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT workflow_id, batch_id, row_number, reason
            FROM rejections
            ORDER BY workflow_id ASC, batch_id ASC, row_number ASC
        """)
        rejection_rows = cursor.fetchall()
        with open(rejections_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["workflow_id", "batch_id", "row_number", "reason"])
            for r in rejection_rows:
                writer.writerow([
                    r["workflow_id"],
                    r["batch_id"],
                    r["row_number"],
                    r["reason"],
                ])

        # 5. data_quality.json
        # Fetch stats from pipeline_stats table
        cursor.execute("SELECT stat_key, stat_value FROM pipeline_stats")
        stats_map = {r["stat_key"]: r["stat_value"] for r in cursor.fetchall()}

        # Fetch rejections by reason
        cursor.execute("SELECT reason, count FROM rejections_by_reason_stats ORDER BY reason ASC")
        rejections_by_reason = {r["reason"]: r["count"] for r in cursor.fetchall()}

        orphan_events_count = self.ledger.count_orphan_events()

        data_quality = {
            "lead_rows_total": stats_map.get("lead_rows_total", 0),
            "lead_rows_rejected": stats_map.get("lead_rows_rejected", 0),
            "rejections_by_reason": rejections_by_reason,
            "duplicate_lead_rows_merged": stats_map.get("duplicate_lead_rows_merged", 0),
            "field_warnings": stats_map.get("field_warnings", 0),
            "events_total": stats_map.get("events_total", 0),
            "malformed_events": stats_map.get("malformed_events", 0),
            "duplicate_events_dropped": stats_map.get("duplicate_events_dropped", 0),
            "orphan_events": orphan_events_count,
            "unknown_disposition_events": stats_map.get("unknown_disposition_events", 0),
        }

        with open(data_quality_path, "w", encoding="utf-8") as f:
            json.dump(data_quality, f, indent=2)

        return {
            "batch_summary": batch_summary_path,
            "daily_overview": daily_overview_path,
            "lead_report": lead_report_path,
            "rejections": rejections_path,
            "data_quality": data_quality_path,
        }
