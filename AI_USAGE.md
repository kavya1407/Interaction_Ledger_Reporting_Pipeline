# AI Usage Disclosure

## Tools used
- Google Antigravity / Gemini 3.8 Flash (High)

## What I used them for
- Scaffolding the initial SQLite schema, pragma optimizations (WAL mode, index layout), and module architecture.
- Drafted test cases for phone normalization variants (e.g., stripping non-digits, handling +91 vs 0 prefix, landline rejection, masking).
- Accelerating generation of day-first date regexes and ISO-8601 IST timestamp parsers.
- Structuring the report generation logic according to the exact CSV and JSON output contracts.

## What I wrote or decided myself
- **Core Architecture & Schema Design**: Designed the append-only interaction ledger pattern with dynamic linking (`phone_hash` + `call_id` fallback for supervisory overrides).
- **Cryptographic PII Protection**: Chose HMAC-SHA256 for deterministic, uninvertible phone indexing combined with authenticated Encrypt-then-MAC keystream encryption for PII fields at rest using standard library primitives so the codebase has zero external cryptographic dependencies.
- **Strict Schema-Order Walking**: Implemented the single rejection reason rule (missing required fields first, invalid phone second) and ensured warnings on optional fields are counted exclusively for accepted rows.
- **Idempotency & Re-entrancy**: Designed file content-hash tracking in `ingested_files` to ensure re-running `ingest` on any drop is completely idempotent.
- **Orphan Resolution Across Drops**: Implemented dynamic linking where orphan counts in `data_quality.json` reflect the final state after drop 2.

## Where the AI was wrong or unhelpful
- **Header Normalization Trailing Underscores**: The AI initially drafted `re.sub(r"[\s_]+", " ", header.strip().lower())`, which trimmed spaces before substituting underscores. For headers with trailing underscores (like `Insured___Name_`), this left a trailing whitespace `"insured name "`. This was caught immediately by unit test `test_replace_runs_of_underscores_and_whitespace`, and fixed by applying `.strip()` after the regex replacement.
- **Duplicate Lead Merging Null Overwrites**: An initial draft of the SQL update in `lead_ingestion.py` attempted a standard full-row `UPDATE`, which would have overwritten existing optional fields with `NULL`. I refactored it to dynamically update only the non-null keys present in the duplicate row, ensuring existing valid attributes are preserved as per the specification.

## How I verified AI-generated code
- Automated test suite with 31 unit and integration tests via `pytest` covering phone normalization, date parsing, decimal sanitization, effective disposition precedence, bucket categorization, orphan resolution across drops, and full end-to-end pipeline idempotency.
- Manual verification of generated CSV and JSON reports against the spec contracts, including diffing row counts and checking 4-decimal formatting on connectivity rates.
- Benchmark validation on the large synthetic dataset (~140k leads, ~600k events).
