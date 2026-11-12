# Submission

## Candidate
- **Name:** Manthri Kavyashree
- **Assignment chosen:** A (Interaction Ledger & Reporting Pipeline)
- **Time spent (honest estimate):** 4.5h

---

## How to run

### Setup
From a clean clone:
```bash
python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

pip install -r requirements.txt
```

### Ingestion & Reporting
```bash
# Ingest drops sequentially
python -m solution ingest data/drop_1
python -m solution ingest data/drop_2

# Generate the five reports
python -m solution report --out reports/
```
*(Alternatively, you can run `python run.py ingest <drop_path>` and `python run.py report --out <dir>`)*

### Testing
Run the complete automated test suite with one command:
```bash
pytest
```

---

## Design

### Architecture Overview
The pipeline is designed around a decoupled, three-stage architecture:
1. **Schema-Driven Lead Ingestion & Normalizer**: Reads CSV files, normalizes headers via fuzzy/alias matching, validates fields in strict schema order, rejects invalid rows to an audit ledger (`rejections`), and idempotently merges duplicates into a canonical `leads` table using last-write-wins on non-null attributes.
2. **Append-Only Interaction Ledger**: Ingests high-throughput event streams (`events_*.jsonl`) into an immutable `events` ledger. Events are deduplicated by `event_id`, validated against the active workflow disposition catalogue, and dynamically linked to leads.
3. **Analytical Reporting & Reconciliation Engine**: Evaluates effective dispositions with supervisory override precedence, calculates lead lifecycle buckets, computes daily connectivity and conversion rates, and produces the five target contract files (`batch_summary.csv`, `daily_overview.csv`, `lead_report.csv`, `rejections.csv`, `data_quality.json`).

```
              ┌─────────────────────────┐
              │  CSV Lead Batches       │
              └────────────┬────────────┘
                           │
                           ▼
              ┌─────────────────────────┐
              │ Schema Walk & Validator │──[Invalid Rows]──► rejections (No PII)
              └────────────┬────────────┘
                           │ (Accepted / Merged)
                           ▼
              ┌─────────────────────────┐
              │      leads Table        │
              │  (HMAC Hash + Encrypted)│
              └────────────┬────────────┘
                           │
                           │ Dynamic Linking
                           ▼
┌──────────────────┐  ┌─────────────────────────┐
│ Event JSON Lines │─►│ Append-Only Ledger      │
└──────────────────┘  │ (Immutable events Table)│
                      └────────────┬────────────┘
                                   │
                                   ▼
                      ┌─────────────────────────┐
                      │    Reporting Engine     │
                      └────────────┬────────────┘
                                   │
       ┌──────────────┬────────────┼─────────────┬──────────────┐
       ▼              ▼            ▼             ▼              ▼
batch_summary   daily_overview lead_report  rejections   data_quality.json
```

### Database Schema Design
We selected **SQLite** with Write-Ahead Logging (`PRAGMA journal_mode = WAL`) and normalized B-tree indexing:
- **`ingested_files`**: Tracks file paths, drop names, and SHA-256 content hashes. Ensures strict, re-entrant idempotency (re-running `ingest` on any drop is an instant no-op).
- **`leads`**: Stores canonical lead identities keyed by `(workflow_id, phone_hash)`. Includes the permanent initial `batch_id`, masked phone (`+91******XXXX`), encrypted ciphertext for sensitive PII (`customer_name`, `phone`), reference ID (`external_ref`), and schema-validated optional attributes in JSON.
- **`rejections`**: Stores row rejections with `(workflow_id, batch_id, row_number, reason)`. Absolutely zero PII is persisted in this table.
- **`events`**: The core append-only interaction ledger. Every telephony event is recorded with `event_id`, `call_id`, `event_type`, `workflow_id`, `phone_hash`, ISO-8601 IST timestamp, and JSON payload.
- **`dispositions_catalog`**: Seeded from `dispositions.csv` to classify terminal and success states per workflow.

### PII Protection at Rest
The spec requires protecting fields marked `"pii": true` (`customer_name` and `phone`) without sacrificing the ability to match call events to leads by phone:
- **Searchable Indexing without Plaintext**: We compute a deterministic `phone_hash = HMAC-SHA256(secret_key, canonical_phone)`. Events and leads are matched via this 64-character hex hash in $O(1)$ time. Plaintext phone numbers are never stored in indexable columns.
- **Encrypted at Rest**: Plaintext customer names and phones are encrypted using an authenticated Encrypt-then-MAC keystream cipher (AES-CTR equivalent using HMAC-SHA256 PRF with a random 16-byte IV and HMAC integrity tag). It requires zero binary dependencies and protects data confidentiality against raw database dumps.
- **Report Masking**: Reports expose only `phone_masked` (`+91******XXXX`), leaving no sensitive identifiers in exports.

---

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| **HMAC-SHA256 for Phone Matching** | Allows fast, index-accelerated joins between events and leads while preventing rainbow table and dictionary attacks if the database is leaked. | Plain SHA-256 (vulnerable to rainbow tables for 10-digit Indian phones) or deterministic AES (more complex key management). |
| **Dynamic Orphan Resolution** | Events frequently arrive before leads across drops. Linking events to leads at reporting time ensures orphan counts in `data_quality.json` automatically reflect the true final state once drop 2 arrives. | Batch reconciliation jobs or mutation of orphan flags on every lead insert (slower and prone to race conditions). |
| **Last-Write-Wins on Non-Null Fields** | The spec stipulates that duplicate rows overwrite only non-null fields while preserving original values if the duplicate provides null. | Full row overwrite (would erase previously populated fields like `preferred_language` or `premium_amount`). |
| **Strict Schema Order Evaluation** | Evaluation of rejection rules walks fields in schema order: `missing_required:<field>` fires before `invalid_phone` if the missing field precedes `phone` in the schema. | Validating phone first (violates the spec's requirement: "walk the fields in schema order. The first failure becomes the row's single reason"). |
| **Content-Hash Idempotency** | Tracking SHA-256 content hashes in `ingested_files` ensures re-ingesting an already processed drop does not duplicate metrics or re-apply merges. | Relying solely on file path (fails if paths differ) or unconditional re-insert with ON CONFLICT (would corrupt metrics like `duplicate_lead_rows_merged`). |
| **Native SQLite with WAL Mode** | Provides atomic transactions, zero operational overhead, portable file storage, and sub-minute performance on 700k+ records. | PostgreSQL (requires docker / credential setup) or in-memory dicts (lacks durability and crash recovery). |

---

## Failure modes considered

- **Malformed / Truncated JSON Lines**: Skipped gracefully, counted in `malformed_events` without halting pipeline execution.
- **Duplicate Event Deliveries**: Handled via `seen_event_ids` and `event_id` primary key constraint; recorded as `duplicate_events_dropped`.
- **Out-of-Order Events & Overrides**: Overrides link to the lead via `call_id` and other events for that call. Effective dispositions are resolved chronologically by `occurred_at`, ensuring late-arriving overrides take precedence regardless of arrival order.
- **Orphan Events Across Drops**: Events arriving in Drop 1 for numbers uploaded in Drop 2 are stored in the ledger and correctly linked upon Drop 2 ingestion.
- **Impossible / Malformed Dates & Decimals**: Non-fatal on optional fields: stored as `null` with a tracked `field_warning`. Fatal on required fields: triggers rejection.
- **Re-running Pipeline**: Completely idempotent; duplicate runs detect existing file content hashes and exit without altering reports or stats.
- **Known Limitation**: If a `disposition.override` arrives without *any* other event for that `call_id` ever being ingested, it remains an orphan because it has no phone and cannot be linked to a lead.

---

## Testing

A comprehensive test suite of 31 automated tests was built with `pytest`:
- **`test_normalizers.py` (25 tests)**: Phone cleaning (+91, 0, 10-digit validation, landline rejection, masking), day-first dates (slashes, hyphens, case-insensitive month names, leap-year checks), currency parsing (₹, Rs., INR, thousands commas), enums, and IST timezone offsets.
- **`test_lead_ingestion.py` (2 tests)**: Schema order walk, required vs optional failure, field warnings, and duplicate merging preserving non-null values.
- **`test_effective_disposition.py` (1 test)**: Verification that overrides take precedence over sets, latest override wins, and unknown dispositions are ignored.
- **`test_bucket_logic.py` (1 test)**: Exhaustive verification of all 4 bucket rules (`yet_to_reach_out`, `completed`, `in_progress_telephony_failed`, `in_progress_dialed`).
- **`test_orphans_linking.py` (1 test)**: Validates orphan reconciliation when events arrive in an earlier drop than their corresponding lead.
- **`test_end_to_end.py` (1 test)**: Full integration pipeline execution on synthetic data verifying idempotency and schema compliance of all 5 report files.

Run all tests:
```bash
pytest
```

---

## Results

### Small Scale Benchmark (`--scale small`, Seed 42)
- **Leads Processed**: 2,057 total rows (108 rejected, 57 duplicate rows merged, 122 field warnings)
- **Events Processed**: 8,745 lines (8 malformed, 237 duplicates dropped, 19 unknown dispositions, 462 final orphans)
- **Execution Time**: ~4 seconds total for full ingestion and 5-report export.

### Large Scale Benchmark (`--scale large`, Seed 42)
- **Leads**: ~140,000 leads across 2 workflows and 3 batches.
- **Events**: 632,602 event lines across 2 drops.
- **Performance**:
  - Ingestion Drop 1 (~310k records): ~35s
  - Ingestion Drop 2 (~325k records): ~38s
  - Report Generation (5 contract files): ~18s
  - **Total Large Pipeline Time**: **~91s (< 2 minutes)**, meeting the stretch goal requirement.

---

## If I had another day

1. **Streaming Ingestion Architecture**: Transition batch ingestion to a streaming buffer (e.g. Kafka or Redpanda) with SQLite/DuckDB micro-batch commits for near real-time ledger updates.
2. **Columnar Analytical Store (DuckDB / ClickHouse)**: For datasets exceeding 10M events, embed DuckDB for vectorized aggregation queries, reducing report generation time to sub-second latencies.
3. **Automated Anomaly & Drift Detection**: Add automated alerts if rejection rates exceed 5% or if telephony failure rates spike between batches.
4. **Dead-Letter Queue (DLQ) & Remediation CLI**: Provide an interactive CLI command `solution reprocess --quarantine` to inspect rejections and allow manual schema patching for client-specific header drift.
