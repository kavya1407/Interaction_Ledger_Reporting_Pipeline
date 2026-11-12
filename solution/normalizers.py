"""Data normalization functions conforming to the pipeline specification."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from solution.config import IST

# Map 3-letter English month names to month numbers
_MONTH_NAME_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
}


def normalize_header(header: str) -> str:
    """Normalise header by lower-casing, trimming, and replacing runs of _ or whitespace with single space."""
    if not header:
        return ""
    # Lowercase, replace runs of whitespace or underscore with single space, and strip
    return re.sub(r"[\s_]+", " ", header.lower()).strip()


def normalize_phone(raw: str | None) -> str | None:
    """Normalize an Indian mobile phone number.

    Rules:
    - Strip every non-digit.
    - If 12 digits starting with '91', drop '91'.
    - If 11 digits starting with '0', drop '0'.
    - Valid only if exactly 10 digits and starts with 6, 7, 8 or 9.
    - Canonical format: '+91XXXXXXXXXX'.
    """
    if not raw or not isinstance(raw, str):
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"
    return None


def normalize_date(raw: str | None) -> str | None:
    """Normalize date strings in day-first formats.

    Accepts:
    - YYYY-MM-DD
    - DD/MM/YYYY
    - DD-MM-YYYY
    - DD-Mon-YYYY (month name case-insensitive)
    Dates are day-first. Impossible dates (e.g. 31/02) return None.
    Returns canonical ISO string 'YYYY-MM-DD' or None.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None

    # 1. YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
            return d.isoformat()
        except ValueError:
            return None

    # 2. DD/MM/YYYY
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            d = date(year, month, day)
            return d.isoformat()
        except ValueError:
            return None

    # 3. DD-MM-YYYY (numeric month)
    m = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})$", s)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            d = date(year, month, day)
            return d.isoformat()
        except ValueError:
            return None

    # 4. DD-Mon-YYYY (3-letter month, case-insensitive)
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$", s)
    if m:
        day, mon_str, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        month = _MONTH_NAME_MAP.get(mon_str)
        if not month:
            return None
        try:
            d = date(year, month, day)
            return d.isoformat()
        except ValueError:
            return None

    return None


def normalize_decimal(raw: str | None) -> float | None:
    """Normalize decimal values.

    Rules:
    - Optionally prefixed by ₹, Rs, Rs., or INR (case-insensitive, optional space).
    - Commas are thousands separators.
    - Must be >= 0.
    - Round to 2 places.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None

    # Strip prefixes: ₹, Rs., Rs, INR
    s = re.sub(r"^(?:₹|rs\.?|inr)\s*", "", s, flags=re.IGNORECASE).strip()
    # Remove commas
    s = s.replace(",", "")

    try:
        val = float(s)
        if val < 0 or val != val:  # check < 0 and NaN
            return None
        return round(val, 2)
    except ValueError:
        return None


def normalize_enum(raw: str | None, allowed_values: list[str]) -> str | None:
    """Normalize enum values case-insensitively and return lower-case canonical value."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    allowed_map = {v.lower(): v.lower() for v in allowed_values}
    return allowed_map.get(s)


def parse_timestamp_ist(raw: str) -> datetime:
    """Parse timestamps in UTC, offset, or naive format into an aware IST datetime.

    Rules:
    - UTC (...Z)
    - Offset (...+05:30)
    - Naive (YYYY-MM-DD HH:MM:SS) -> Naive means IST
    """
    s = raw.strip()
    if s.endswith("Z"):
        # UTC format
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(IST)
    elif "+" in s or (len(s) > 10 and "-" in s[10:]):
        # Has explicit timezone offset
        dt = datetime.fromisoformat(s)
        return dt.astimezone(IST)
    else:
        # Naive means IST per spec
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        return dt
