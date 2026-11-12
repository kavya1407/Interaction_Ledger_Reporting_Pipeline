"""Unit tests for data normalization and validation rules."""
from __future__ import annotations

import pytest
from solution.normalizers import (
    normalize_date,
    normalize_decimal,
    normalize_enum,
    normalize_header,
    normalize_phone,
    parse_timestamp_ist,
)
from solution.crypto import mask_phone, hash_phone, encrypt_pii, decrypt_pii
from solution.config import IST


class TestHeaderNormalization:
    def test_lower_case_and_trim(self):
        assert normalize_header("  Customer Name  ") == "customer name"

    def test_replace_runs_of_underscores_and_whitespace(self):
        assert normalize_header("mobile__no") == "mobile no"
        assert normalize_header("Insured___Name_") == "insured name"
        assert normalize_header("  policy   _   number  ") == "policy number"

    def test_empty_header(self):
        assert normalize_header("") == ""


class TestPhoneNormalization:
    def test_valid_10_digit_numbers(self):
        assert normalize_phone("9876543210") == "+919876543210"
        assert normalize_phone("8123456789") == "+918123456789"
        assert normalize_phone("7000000000") == "+917000000000"
        assert normalize_phone("6999999999") == "+916999999999"

    def test_12_digits_starting_with_91(self):
        assert normalize_phone("919876543210") == "+919876543210"
        assert normalize_phone("+91-98765-43210") == "+919876543210"
        assert normalize_phone("91 88888 77777") == "+918888877777"

    def test_11_digits_starting_with_0(self):
        assert normalize_phone("09876543210") == "+919876543210"
        assert normalize_phone("0-71234-56789") == "+917123456789"

    def test_invalid_lengths(self):
        assert normalize_phone("987654321") is None   # 9 digits
        assert normalize_phone("98765432100") is None # 11 digits not starting with 0
        assert normalize_phone("") is None
        assert normalize_phone(None) is None

    def test_invalid_prefixes(self):
        assert normalize_phone("5123456789") is None  # starts with 5
        assert normalize_phone("1234567890") is None  # starts with 1
        assert normalize_phone("022-2541-1234") is None  # landline

    def test_phone_masking(self):
        assert mask_phone("+919876543210") == "+91******3210"
        assert mask_phone("+917932896340") == "+91******6340"


class TestDateNormalization:
    def test_iso_format(self):
        assert normalize_date("2026-10-15") == "2026-10-15"

    def test_day_first_slash(self):
        assert normalize_date("15/10/2026") == "2026-10-15"
        assert normalize_date("01/05/2026") == "2026-05-01"

    def test_day_first_hyphen(self):
        assert normalize_date("15-10-2026") == "2026-10-15"
        assert normalize_date("09-02-2026") == "2026-02-09"

    def test_day_month_name_case_insensitive(self):
        assert normalize_date("01-Dec-2026") == "2026-12-01"
        assert normalize_date("14-oct-2026") == "2026-10-14"
        assert normalize_date("25-JAN-2026") == "2026-01-25"

    def test_impossible_dates(self):
        assert normalize_date("31/02/2026") is None
        assert normalize_date("2026-13-01") is None
        assert normalize_date("soon") is None
        assert normalize_date("TBD") is None


class TestDecimalNormalization:
    def test_plain_numbers(self):
        assert normalize_decimal("1500") == 1500.00
        assert normalize_decimal("1500.5") == 1500.50
        assert normalize_decimal("1500.556") == 1500.56

    def test_currency_prefixes(self):
        assert normalize_decimal("₹27,554") == 27554.00
        assert normalize_decimal("Rs. 24787") == 24787.00
        assert normalize_decimal("Rs 12000") == 12000.00
        assert normalize_decimal("INR 18678.3") == 18678.30

    def test_commas_as_thousands_separators(self):
        assert normalize_decimal("1,00,000.50") == 100000.50
        assert normalize_decimal("₹ 1,23,456.78") == 123456.78

    def test_invalid_decimals(self):
        assert normalize_decimal("-500") is None
        assert normalize_decimal("N/A") is None
        assert normalize_decimal("twelve thousand") is None


class TestEnumNormalization:
    def test_case_insensitive_matching(self):
        vals = ["en", "hi", "ta", "kn", "te", "mr"]
        assert normalize_enum("EN", vals) == "en"
        assert normalize_enum(" Hi ", vals) == "hi"
        assert normalize_enum("ta", vals) == "ta"

    def test_invalid_values(self):
        vals = ["en", "hi", "ta", "kn", "te", "mr"]
        assert normalize_enum("Hindi", vals) is None
        assert normalize_enum("english", vals) is None
        assert normalize_enum("french", vals) is None


class TestTimestampParsing:
    def test_utc_timestamp(self):
        dt = parse_timestamp_ist("2026-10-05T13:20:21Z")
        assert dt.tzinfo == IST
        assert dt.hour == 18
        assert dt.minute == 50
        assert dt.second == 21

    def test_offset_timestamp(self):
        dt = parse_timestamp_ist("2026-10-05T18:50:21+05:30")
        assert dt.tzinfo == IST
        assert dt.hour == 18
        assert dt.minute == 50

    def test_naive_timestamp_treated_as_ist(self):
        dt = parse_timestamp_ist("2026-10-05 18:50:21")
        assert dt.tzinfo == IST
        assert dt.hour == 18
        assert dt.minute == 50
        assert dt.second == 21


class TestCryptoPII:
    def test_hash_phone_deterministic(self):
        h1 = hash_phone("+919876543210")
        h2 = hash_phone("+919876543210")
        h3 = hash_phone("+919876543211")
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 64

    def test_encrypt_decrypt_roundtrip(self):
        original = "Aarav Sharma"
        ciphertext = encrypt_pii(original)
        assert ciphertext != original
        decrypted = decrypt_pii(ciphertext)
        assert decrypted == original
