"""Command Line Interface (CLI) for the pipeline."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from solution.config import DEFAULT_DB_PATH
from solution.db import get_db_connection, init_db
from solution.event_ingestion import EventIngestor
from solution.lead_ingestion import LeadIngestor
from solution.reporting import ReportGenerator
from solution.schema_loader import SchemaCatalog


def ingest_drop(drop_path: Path, db_path: Path | None = None) -> None:
    """Ingest a drop folder or file into the SQLite database."""
    drop_path = Path(drop_path).resolve()
    if not drop_path.exists():
        print(f"Error: Path does not exist: {drop_path}", file=sys.stderr)
        sys.exit(1)

    catalog = SchemaCatalog()
    conn = get_db_connection(db_path)
    init_db(conn, catalog)

    lead_ingestor = LeadIngestor(conn, catalog)
    event_ingestor = EventIngestor(conn, catalog)

    drop_name = drop_path.name

    # 1. Ingest Leads
    leads_dir = drop_path / "leads"
    if leads_dir.exists() and leads_dir.is_dir():
        # Subdirectories are workflow_ids
        for wf_dir in sorted(leads_dir.iterdir()):
            if wf_dir.is_dir():
                wf_id = wf_dir.name
                csv_files = sorted(wf_dir.glob("*.csv"), key=lambda p: p.name)
                for csv_f in csv_files:
                    batch_id = csv_f.stem
                    print(f"Ingesting lead batch: {wf_id} / {csv_f.name}...")
                    lead_ingestor.ingest_lead_file(
                        file_path=csv_f,
                        workflow_id=wf_id,
                        drop_name=drop_name,
                        batch_id=batch_id,
                    )

    # 2. Ingest Events
    events_dir = drop_path / "events"
    if events_dir.exists() and events_dir.is_dir():
        jsonl_files = sorted(events_dir.glob("*.jsonl"), key=lambda p: p.name)
        for jsonl_f in jsonl_files:
            print(f"Ingesting event file: {jsonl_f.name}...")
            event_ingestor.ingest_event_file(
                file_path=jsonl_f,
                drop_name=drop_name,
            )

    conn.close()
    print(f"Successfully processed drop: {drop_name}")


def generate_reports(out_dir: Path, db_path: Path | None = None) -> None:
    """Generate all five report files into the output directory."""
    out_dir = Path(out_dir).resolve()
    catalog = SchemaCatalog()
    conn = get_db_connection(db_path)
    init_db(conn, catalog)

    generator = ReportGenerator(conn, catalog)
    print(f"Generating reports into: {out_dir}...")
    paths = generator.generate_all_reports(out_dir)
    conn.close()

    for name, p in paths.items():
        print(f"  Generated: {p.name}")
    print("All reports generated successfully.")


def main():
    parser = argparse.ArgumentParser(
        description="Agentic Universe - Interaction Ledger & Reporting Pipeline"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to SQLite database file (default: data/pipeline.db)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest a drop folder")
    ingest_parser.add_argument(
        "drop_path",
        type=Path,
        help="Path to drop directory (e.g. data/drop_1)",
    )

    # report command
    report_parser = subparsers.add_parser("report", help="Generate reports")
    report_parser.add_argument(
        "--out",
        type=Path,
        default=Path("reports"),
        help="Output directory for reports (default: reports/)",
    )

    args = parser.parse_args()

    if args.command == "ingest":
        ingest_drop(args.drop_path, db_path=args.db)
    elif args.command == "report":
        generate_reports(args.out, db_path=args.db)


if __name__ == "__main__":
    main()
