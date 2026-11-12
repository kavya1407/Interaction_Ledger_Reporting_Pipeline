"""Configuration constants and paths for the pipeline."""
from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

# Indian Standard Time (IST): UTC+05:30
IST = timezone(timedelta(hours=5, minutes=30))

# Repository Root Directory
REPO_ROOT = Path(__file__).resolve().parent.parent

# Default Schemas Directory
SCHEMAS_DIR = REPO_ROOT / "assignment-a-data" / "schemas"

# Default Database Path
DEFAULT_DB_PATH = REPO_ROOT / "data" / "pipeline.db"

# PII Secret Key (used for deterministic HMAC-SHA256 phone search hash and AEAD encryption)
# In production, this is supplied via an environment variable or KMS.
PII_SECRET_KEY = os.environ.get("PII_SECRET_KEY", "agentic-universe-sde-2026-secure-salt-key-9f8e7d").encode("utf-8")
