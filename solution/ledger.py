"""Interaction ledger queries, event linking, effective dispositions, and call metrics."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from solution.schema_loader import SchemaCatalog


@dataclass
class CallFact:
    call_id: str
    workflow_id: str
    phone_hash: str
    initiated_at: str  # ISO IST
    call_date: str     # YYYY-MM-DD
    is_connected: bool
    has_telephony_failed: bool
    effective_disposition: str | None
    is_terminal: bool
    is_success: bool


@dataclass
class LeadAggregates:
    lead_id: int
    workflow_id: str
    phone_hash: str
    phone_masked: str
    external_ref: str
    batch_id: str
    total_calls: int
    connected_calls: int
    last_call_at: str
    current_disposition: str
    ever_success: bool
    bucket: str


class LedgerService:
    def __init__(self, conn: sqlite3.Connection, catalog: SchemaCatalog):
        self.conn = conn
        self.catalog = catalog

    def count_orphan_events(self) -> int:
        """Count total events in the ledger that cannot be linked to any valid lead (final state).

        Optimized to use indexed lookups avoiding heavy unindexed CTE joins:
        1. Events with phone_hash are checked via indexed join with leads table.
        2. Events without phone_hash (e.g. disposition.override) are resolved via their call_id.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) AS count
            FROM events e
            LEFT JOIN leads l ON e.workflow_id = l.workflow_id AND e.phone_hash = l.phone_hash
            WHERE e.phone_hash IS NOT NULL AND l.id IS NULL
        """)
        row = cursor.fetchone()
        direct_orphans = row["count"] if row else 0

        # Check events where phone_hash IS NULL
        cursor.execute("""
            SELECT workflow_id, call_id
            FROM events
            WHERE phone_hash IS NULL
        """)
        null_phone_events = cursor.fetchall()

        if not null_phone_events:
            return direct_orphans

        null_orphans = 0
        call_ids = list({r["call_id"] for r in null_phone_events})
        call_phone_map: dict[str, str | None] = {}
        for i in range(0, len(call_ids), 900):
            batch = call_ids[i:i + 900]
            placeholders = ",".join("?" for _ in batch)
            cursor.execute(f"""
                SELECT call_id, phone_hash
                FROM events
                WHERE call_id IN ({placeholders}) AND phone_hash IS NOT NULL
                GROUP BY call_id
            """, batch)
            for r in cursor.fetchall():
                call_phone_map[r["call_id"]] = r["phone_hash"]

        for r in null_phone_events:
            wf = r["workflow_id"]
            ph = call_phone_map.get(r["call_id"])
            if not ph:
                null_orphans += 1
            else:
                cursor.execute(
                    "SELECT 1 FROM leads WHERE workflow_id = ? AND phone_hash = ? LIMIT 1",
                    (wf, ph),
                )
                if not cursor.fetchone():
                    null_orphans += 1

        return direct_orphans + null_orphans

    def get_all_linked_calls(self) -> list[CallFact]:
        """Reconstruct all valid, linked calls in a single aggregation pass.

        Rules:
        - A call is counted only if its call.initiated event is present (and linked).
        - The call's date is the IST date of call.initiated.
        - A call is connected if it has a call.connected event.
        - Effective disposition: latest disposition.override by occurred_at;
          if none, latest disposition.set by occurred_at; otherwise none.
        - Ignore unknown dispositions (is_valid_disposition = 0).
        """
        cursor = self.conn.cursor()
        query = """
        SELECT
            c.call_id,
            c.workflow_id,
            c.phone_hash,
            c.initiated_at,
            c.conn_count,
            c.fail_count
        FROM (
            SELECT
                call_id,
                workflow_id,
                MAX(phone_hash) AS phone_hash,
                MAX(CASE WHEN event_type = 'call.initiated' THEN occurred_at END) AS initiated_at,
                MAX(CASE WHEN event_type = 'call.connected' THEN 1 ELSE 0 END) AS conn_count,
                MAX(CASE WHEN event_type = 'call.telephony_failed' THEN 1 ELSE 0 END) AS fail_count
            FROM events
            GROUP BY call_id
            HAVING initiated_at IS NOT NULL AND phone_hash IS NOT NULL
        ) c
        JOIN leads l ON c.workflow_id = l.workflow_id AND c.phone_hash = l.phone_hash
        ORDER BY c.initiated_at ASC;
        """
        cursor.execute(query)
        call_rows = cursor.fetchall()

        # For effective disposition, fetch all valid disposition events for these calls
        cursor.execute("""
            SELECT call_id, event_type, occurred_at, payload
            FROM events
            WHERE event_type IN ('disposition.set', 'disposition.override')
              AND is_valid_disposition = 1
            ORDER BY occurred_at ASC
        """)
        disp_rows = cursor.fetchall()

        # Group disposition events by call_id: {'override': [], 'set': []}
        call_disps: dict[str, dict[str, list[tuple[str, str]]]] = {}
        for r in disp_rows:
            cid = r["call_id"]
            if cid not in call_disps:
                call_disps[cid] = {"override": [], "set": []}
            payload = json.loads(r["payload"]) if r["payload"] else {}
            code = payload.get("disposition_code")
            if code:
                if r["event_type"] == "disposition.override":
                    call_disps[cid]["override"].append((r["occurred_at"], str(code).strip()))
                else:
                    call_disps[cid]["set"].append((r["occurred_at"], str(code).strip()))

        calls: list[CallFact] = []
        for cr in call_rows:
            cid = cr["call_id"]
            wf = cr["workflow_id"]
            p_hash = cr["phone_hash"]
            init_at = cr["initiated_at"]
            call_date = init_at[:10]  # YYYY-MM-DD
            is_connected = cr["conn_count"] > 0
            has_telephony_failed = cr["fail_count"] > 0

            # Compute effective disposition
            disps = call_disps.get(cid, {"override": [], "set": []})
            eff_code: str | None = None
            if disps["override"]:
                # Latest override by occurred_at
                eff_code = disps["override"][-1][1]
            elif disps["set"]:
                # Latest set by occurred_at
                eff_code = disps["set"][-1][1]

            is_terminal = False
            is_success = False
            if eff_code:
                wf_catalog = self.catalog.dispositions.get(wf, {})
                disp_obj = wf_catalog.get(eff_code)
                if disp_obj:
                    is_terminal = disp_obj.is_terminal
                    is_success = disp_obj.is_success

            calls.append(
                CallFact(
                    call_id=cid,
                    workflow_id=wf,
                    phone_hash=p_hash,
                    initiated_at=init_at,
                    call_date=call_date,
                    is_connected=is_connected,
                    has_telephony_failed=has_telephony_failed,
                    effective_disposition=eff_code,
                    is_terminal=is_terminal,
                    is_success=is_success,
                )
            )
        return calls

    def get_lead_aggregates(self, all_calls: list[CallFact] | None = None) -> list[LeadAggregates]:
        """Aggregate calls for each lead and compute buckets."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT id, workflow_id, phone_hash, phone_masked, external_ref, batch_id
            FROM leads
            ORDER BY workflow_id, external_ref
        """)
        leads = cursor.fetchall()

        if all_calls is None:
            all_calls = self.get_all_linked_calls()

        # Group calls by (workflow_id, phone_hash)
        lead_calls: dict[tuple[str, str], list[CallFact]] = {}
        for c in all_calls:
            key = (c.workflow_id, c.phone_hash)
            if key not in lead_calls:
                lead_calls[key] = []
            lead_calls[key].append(c)

        result: list[LeadAggregates] = []
        for l in leads:
            lid = l["id"]
            wf = l["workflow_id"]
            ph = l["phone_hash"]
            p_masked = l["phone_masked"]
            ref = l["external_ref"]
            batch_id = l["batch_id"]

            calls = lead_calls.get((wf, ph), [])
            total_calls = len(calls)
            connected_calls = sum(1 for c in calls if c.is_connected)

            last_call_at = ""
            current_disposition = ""
            ever_success = any(c.is_success for c in calls)

            if total_calls > 0:
                # Latest initiated time
                calls_by_time = sorted(calls, key=lambda c: c.initiated_at)
                last_call_at = calls_by_time[-1].initiated_at

                # Most recent call (by initiated time) that has an effective disposition
                calls_with_disp = [c for c in calls_by_time if c.effective_disposition is not None]
                if calls_with_disp:
                    current_disposition = calls_with_disp[-1].effective_disposition or ""

            # Bucket resolution in spec order:
            # 1. yet_to_reach_out: no calls
            # 2. completed: any call's effective disposition is terminal
            # 3. in_progress_telephony_failed: every call has a call.telephony_failed event
            # 4. in_progress_dialed: everything else
            if total_calls == 0:
                bucket = "yet_to_reach_out"
            elif any(c.is_terminal for c in calls):
                bucket = "completed"
            elif all(c.has_telephony_failed for c in calls):
                bucket = "in_progress_telephony_failed"
            else:
                bucket = "in_progress_dialed"

            result.append(
                LeadAggregates(
                    lead_id=lid,
                    workflow_id=wf,
                    phone_hash=ph,
                    phone_masked=p_masked,
                    external_ref=ref,
                    batch_id=batch_id,
                    total_calls=total_calls,
                    connected_calls=connected_calls,
                    last_call_at=last_call_at,
                    current_disposition=current_disposition,
                    ever_success=ever_success,
                    bucket=bucket,
                )
            )
        return result
