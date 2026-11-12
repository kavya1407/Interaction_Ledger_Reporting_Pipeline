"""Lead ingestion pipeline with schema validation, normalization, rejection, and merging."""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from solution.config import IST
from solution.crypto import encrypt_pii, hash_phone, mask_phone
from solution.normalizers import (
    normalize_date,
    normalize_decimal,
    normalize_enum,
    normalize_header,
    normalize_phone,
)
from solution.schema_loader import FieldDefinition, SchemaCatalog, WorkflowSchema


def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 of file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class LeadIngestor:
    def __init__(self, conn: sqlite3.Connection, catalog: SchemaCatalog):
        self.conn = conn
        self.catalog = catalog

    def ingest_lead_file(
        self,
        file_path: Path,
        workflow_id: str,
        drop_name: str,
        batch_id: str,
    ) -> bool:
        """Ingest a single lead CSV file. Returns True if processed, False if already ingested."""
        content_hash = compute_file_hash(file_path)
        norm_file_path = str(file_path.resolve())

        # Check if already ingested with identical content hash
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT content_hash FROM ingested_files WHERE file_path = ?",
            (norm_file_path,),
        )
        row = cursor.fetchone()
        if row and row["content_hash"] == content_hash:
            # Already ingested, skip for idempotency
            return False

        schema = self.catalog.workflows.get(workflow_id)
        if not schema:
            raise ValueError(f"Unknown workflow_id: {workflow_id}")

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            try:
                raw_headers = next(reader)
            except StopIteration:
                # Empty file
                return False

            # Map column index -> FieldDefinition
            col_to_field: dict[int, FieldDefinition] = {}
            for idx, raw_h in enumerate(raw_headers):
                norm_h = normalize_header(raw_h)
                for field_def in schema.fields:
                    if norm_h == normalize_header(field_def.name):
                        col_to_field[idx] = field_def
                        break
                    alias_matches = [
                        normalize_header(a) for a in field_def.aliases
                    ]
                    if norm_h in alias_matches:
                        col_to_field[idx] = field_def
                        break

            # Metrics for this file
            lead_rows_total = 0
            lead_rows_rejected = 0
            duplicate_rows_merged = 0
            field_warnings = 0
            rejections_by_reason: dict[str, int] = {}
            rejections_to_insert: list[tuple[str, str, int, str, str]] = []

            non_blank_row_num = 0
            now_iso = datetime.now(IST).isoformat()

            # Process data rows
            for raw_row in reader:
                # Check for blank row (every cell empty or whitespace)
                if not raw_row or all(cell.strip() == "" for cell in raw_row):
                    continue

                non_blank_row_num += 1
                lead_rows_total += 1

                # Extract raw cell values
                row_raw_values: dict[str, str | None] = {}
                for idx, cell in enumerate(raw_row):
                    if idx in col_to_field:
                        f_def = col_to_field[idx]
                        trimmed = cell.strip()
                        row_raw_values[f_def.name] = (
                            trimmed if trimmed != "" else None
                        )

                # Validation walk IN SCHEMA ORDER
                rejected = False
                rejection_reason: str | None = None
                row_warnings = 0
                cleaned_values: dict[str, str | float | None] = {}

                for f_def in schema.fields:
                    val = row_raw_values.get(f_def.name)

                    if f_def.required:
                        if val is None:
                            rejected = True
                            rejection_reason = f"missing_required:{f_def.name}"
                            break

                        if f_def.type == "phone":
                            norm_p = normalize_phone(val)
                            if norm_p is None:
                                rejected = True
                                rejection_reason = "invalid_phone"
                                break
                            cleaned_values[f_def.name] = norm_p
                        elif f_def.type == "string":
                            cleaned_values[f_def.name] = val
                        elif f_def.type == "date":
                            norm_d = normalize_date(val)
                            if norm_d is None:
                                rejected = True
                                rejection_reason = f"missing_required:{f_def.name}"
                                break
                            cleaned_values[f_def.name] = norm_d
                        elif f_def.type == "decimal":
                            norm_dec = normalize_decimal(val)
                            if norm_dec is None:
                                rejected = True
                                rejection_reason = f"missing_required:{f_def.name}"
                                break
                            cleaned_values[f_def.name] = norm_dec
                        elif f_def.type == "enum":
                            norm_e = normalize_enum(val, f_def.values)
                            if norm_e is None:
                                rejected = True
                                rejection_reason = f"missing_required:{f_def.name}"
                                break
                            cleaned_values[f_def.name] = norm_e
                    else:
                        # Optional field
                        if val is None:
                            cleaned_values[f_def.name] = None
                        else:
                            if f_def.type == "phone":
                                norm_p = normalize_phone(val)
                                if norm_p is None:
                                    cleaned_values[f_def.name] = None
                                    row_warnings += 1
                                else:
                                    cleaned_values[f_def.name] = norm_p
                            elif f_def.type == "date":
                                norm_d = normalize_date(val)
                                if norm_d is None:
                                    cleaned_values[f_def.name] = None
                                    row_warnings += 1
                                else:
                                    cleaned_values[f_def.name] = norm_d
                            elif f_def.type == "decimal":
                                norm_dec = normalize_decimal(val)
                                if norm_dec is None:
                                    cleaned_values[f_def.name] = None
                                    row_warnings += 1
                                else:
                                    cleaned_values[f_def.name] = norm_dec
                            elif f_def.type == "enum":
                                norm_e = normalize_enum(val, f_def.values)
                                if norm_e is None:
                                    cleaned_values[f_def.name] = None
                                    row_warnings += 1
                                else:
                                    cleaned_values[f_def.name] = norm_e
                            else:
                                cleaned_values[f_def.name] = val

                if rejected and rejection_reason:
                    lead_rows_rejected += 1
                    rejections_by_reason[rejection_reason] = (
                        rejections_by_reason.get(rejection_reason, 0) + 1
                    )
                    rejections_to_insert.append(
                        (
                            workflow_id,
                            batch_id,
                            non_blank_row_num,
                            rejection_reason,
                            norm_file_path,
                        )
                    )
                    continue

                # Accepted row: count field warnings
                field_warnings += row_warnings

                # Canonical phone and identity
                canonical_phone = str(cleaned_values["phone"])
                p_hash = hash_phone(canonical_phone)

                ref_field = schema.reference_field
                ref_val = str(cleaned_values.get(ref_field.name, "")) if ref_field else ""

                # Optional fields dict
                optional_data: dict[str, str | float | None] = {}
                for f_def in schema.fields:
                    if not f_def.required and not f_def.pii:
                        if cleaned_values.get(f_def.name) is not None:
                            optional_data[f_def.name] = cleaned_values[f_def.name]

                # Check if lead exists
                cursor.execute(
                    "SELECT id, batch_id, customer_name_encrypted, external_ref, optional_data FROM leads WHERE workflow_id = ? AND phone_hash = ?",
                    (workflow_id, p_hash),
                )
                existing = cursor.fetchone()

                if existing is None:
                    # New lead
                    cust_name = str(cleaned_values.get("customer_name", ""))
                    name_encrypted = encrypt_pii(cust_name)
                    phone_encrypted = encrypt_pii(canonical_phone)
                    p_masked = mask_phone(canonical_phone)

                    cursor.execute(
                        """
                        INSERT INTO leads (
                            workflow_id, phone_hash, phone_encrypted, phone_masked,
                            customer_name_encrypted, external_ref, batch_id,
                            optional_data, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            workflow_id,
                            p_hash,
                            phone_encrypted,
                            p_masked,
                            name_encrypted,
                            ref_val,
                            batch_id,
                            json.dumps(optional_data),
                            now_iso,
                            now_iso,
                        ),
                    )
                else:
                    # Duplicate lead row merged
                    duplicate_rows_merged += 1
                    lead_id = existing["id"]
                    existing_opt = json.loads(existing["optional_data"]) if existing["optional_data"] else {}

                    # Overwrite only non-null fields
                    name_update_sql = ""
                    name_params = []
                    if cleaned_values.get("customer_name") is not None:
                        new_name_enc = encrypt_pii(str(cleaned_values["customer_name"]))
                        name_update_sql = ", customer_name_encrypted = ?"
                        name_params.append(new_name_enc)

                    ref_update_sql = ""
                    ref_params = []
                    if ref_val != "":
                        ref_update_sql = ", external_ref = ?"
                        ref_params.append(ref_val)

                    # Update optional data with non-null values
                    for k, v in optional_data.items():
                        existing_opt[k] = v

                    update_sql = f"""
                        UPDATE leads
                        SET updated_at = ?, optional_data = ?{name_update_sql}{ref_update_sql}
                        WHERE id = ?
                    """
                    cursor.execute(
                        update_sql,
                        [now_iso, json.dumps(existing_opt)] + name_params + ref_params + [lead_id],
                    )

            # Insert rejections
            if rejections_to_insert:
                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO rejections (workflow_id, batch_id, row_number, reason, file_path)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    rejections_to_insert,
                )

            # Update stats tables
            cursor.execute(
                "INSERT INTO pipeline_stats (stat_key, stat_value) VALUES ('lead_rows_total', ?) ON CONFLICT(stat_key) DO UPDATE SET stat_value = stat_value + ?",
                (lead_rows_total, lead_rows_total),
            )
            cursor.execute(
                "INSERT INTO pipeline_stats (stat_key, stat_value) VALUES ('lead_rows_rejected', ?) ON CONFLICT(stat_key) DO UPDATE SET stat_value = stat_value + ?",
                (lead_rows_rejected, lead_rows_rejected),
            )
            cursor.execute(
                "INSERT INTO pipeline_stats (stat_key, stat_value) VALUES ('duplicate_lead_rows_merged', ?) ON CONFLICT(stat_key) DO UPDATE SET stat_value = stat_value + ?",
                (duplicate_rows_merged, duplicate_rows_merged),
            )
            cursor.execute(
                "INSERT INTO pipeline_stats (stat_key, stat_value) VALUES ('field_warnings', ?) ON CONFLICT(stat_key) DO UPDATE SET stat_value = stat_value + ?",
                (field_warnings, field_warnings),
            )

            for reason, cnt in rejections_by_reason.items():
                cursor.execute(
                    "INSERT INTO rejections_by_reason_stats (reason, count) VALUES (?, ?) ON CONFLICT(reason) DO UPDATE SET count = count + ?",
                    (reason, cnt, cnt),
                )

            # Record file ingestion
            cursor.execute(
                """
                INSERT OR REPLACE INTO ingested_files (
                    file_path, file_type, drop_name, workflow_id, batch_id, content_hash, rows_count, ingested_at
                ) VALUES (?, 'lead', ?, ?, ?, ?, ?, ?)
                """,
                (
                    norm_file_path,
                    drop_name,
                    workflow_id,
                    batch_id,
                    content_hash,
                    non_blank_row_num,
                    now_iso,
                ),
            )

            self.conn.commit()
            return True
