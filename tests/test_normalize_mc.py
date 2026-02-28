"""Tests for normalize_mc() and _mc_candidates() in broker_intelligence.py

Run with:  pytest tests/test_normalize_mc.py -v
"""
import pytest

from app.services.broker_intelligence import _mc_candidates, normalize_mc


# ---------------------------------------------------------------------------
# normalize_mc — parameterized truth table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    # ── Standard digit-only MCs ────────────────────────────────────────────
    ("009153",        "009153"),   # already canonical, zero-padded
    ("9153",          "9153"),     # short unpadded — valid, candidates handle lookup
    ("1234567",       "1234567"),  # 7-digit
    ("12345678",      "12345678"), # 8-digit (max)
    ("1234",          "1234"),     # 4-digit (min)

    # ── Prefix/separator stripping ─────────────────────────────────────────
    ("MC009153",      "009153"),   # "MC" prefix stripped
    ("mc009153",      "009153"),   # lowercase prefix stripped
    ("MC 009153",     "009153"),   # "MC " with space
    ("mc-009153",     "009153"),   # "mc-" with dash
    ("  009153  ",    "009153"),   # surrounding whitespace

    # ── Freight forwarder MCs ──────────────────────────────────────────────
    ("FF003723",      "FF003723"), # canonical FF
    ("ff003723",      "FF003723"), # lowercase ff → uppercased
    ("FF-003723",     "FF003723"), # dash separator stripped
    ("ff 003723",     "FF003723"), # space separator stripped
    ("FF-003723",     "FF003723"), # dash variant
    ("  FF042975  ",  "FF042975"), # surrounding whitespace

    # ── Rejected: too short ────────────────────────────────────────────────
    ("123",           None),       # 3 digits — below minimum
    ("12",            None),
    ("1",             None),
    ("",              None),
    (None,            None),

    # ── Rejected: too long (DOT numbers are 9 digits) ─────────────────────
    ("123456789",     None),       # 9-digit DOT — rejected
    ("1234567890",    None),       # 10-digit — rejected

    # ── Rejected: ambiguous / garbage ─────────────────────────────────────
    ("MC",            None),       # prefix only, no digits
    ("FF",            None),       # FF prefix only, no digits
    ("ABCDEF",        None),       # all letters
    ("MC-ABC",        None),       # letters after stripping
])
def test_normalize_mc(raw, expected):
    assert normalize_mc(raw) == expected


# ---------------------------------------------------------------------------
# _mc_candidates — padding-aware lookup candidates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mc,expected_candidates", [
    # 6-digit: zfill(6) and zfill(7) both produce longer values → 3 distinct
    ("9153",    ["9153",    "009153",  "0009153"]),
    # 6-digit already: zfill(6) is same → 2 distinct
    ("009153",  ["009153",  "0009153"]),
    # 7-digit: zfill(6) is shorter, ignored; zfill(7) is same → 1 distinct
    ("1234567", ["1234567"]),
    # 8-digit: both zfills are shorter → 1 distinct
    ("12345678",["12345678"]),
    # 4-digit: 3 distinct candidates
    ("1234",    ["1234",    "001234",  "0001234"]),
    # FF: always single-element, no padding
    ("FF003723",["FF003723"]),
    ("FF042975",["FF042975"]),
])
def test_mc_candidates(mc, expected_candidates):
    assert _mc_candidates(mc) == expected_candidates


# ---------------------------------------------------------------------------
# Critical property: normalize then candidates never violates DB constraint
# (^\d{4,8}$ OR ^FF\d+$)
# ---------------------------------------------------------------------------

import re
_DB_CONSTRAINT = re.compile(r"^\d{4,8}$|^FF\d+$")

@pytest.mark.parametrize("raw", [
    "MC009153", "mc-009153", "9153", "009153", "1234567", "12345678",
    "FF003723", "ff003723", "FF-003723", "ff 003723", "FF042975",
    "1234",
])
def test_all_candidates_satisfy_db_constraint(raw):
    mc = normalize_mc(raw)
    assert mc is not None, f"normalize_mc({raw!r}) returned None unexpectedly"
    for candidate in _mc_candidates(mc):
        assert _DB_CONSTRAINT.match(candidate), (
            f"candidate {candidate!r} from input {raw!r} would violate DB constraint"
        )
