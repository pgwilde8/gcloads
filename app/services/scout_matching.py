"""Scout match scoring service.

Computes a 0–4 score comparing an ingested load against a driver's Scout
profile.  Each criterion is worth 1 point:

  1. Origin region match
  2. Destination region match
  3. Rate-per-mile >= driver minimum
  4. Equipment type match

Design decisions:
- If a driver has NOT set a preference for a criterion, that criterion is
  treated as a match (score point awarded).  An empty preference means
  "I don't care", not "nothing matches".
- RPM is extracted from metadata["rate_per_mile"] if present, otherwise
  computed from price / distance if both are available, otherwise the
  criterion is skipped (treated as match) when the driver has no min_cpm.
- Equipment type comparison is case-insensitive and normalises common
  abbreviations (FTL, Flatbed, Reefer, etc.).
- All string matching uses ILIKE-style substring matching (case-insensitive
  containment) on the first token of the preference string.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.driver import Driver
from app.models.load import Load


# ── Equipment normalisation map ────────────────────────────────────────────────
_EQUIP_ALIASES: dict[str, str] = {
    "van": "dry van",
    "dryvan": "dry van",
    "dry": "dry van",
    "ftl": "dry van",
    "reefer": "refrigerated",
    "ref": "refrigerated",
    "rf": "refrigerated",
    "flatbed": "flatbed",
    "flat": "flatbed",
    "fb": "flatbed",
    "step": "step deck",
    "stepdeck": "step deck",
    "lowboy": "lowboy",
    "power only": "power only",
    "po": "power only",
    "tanker": "tanker",
    "bulk": "bulk",
    "conestoga": "conestoga",
    "rgn": "rgn",
    "double drop": "double drop",
}


def _normalise_equip(raw: str | None) -> str:
    if not raw:
        return ""
    key = re.sub(r"[^a-z0-9 ]", "", raw.lower().strip())
    key_no_space = key.replace(" ", "")
    return _EQUIP_ALIASES.get(key_no_space, _EQUIP_ALIASES.get(key, key))


# ── RPM extraction ─────────────────────────────────────────────────────────────

def _parse_price_to_decimal(raw: str | None) -> Decimal | None:
    """Strip currency symbols and return a Decimal, or None on failure."""
    if not raw:
        return None
    cleaned = re.sub(r"[^\d.]", "", raw)
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _extract_rpm(load: Load, metadata: dict[str, Any]) -> Decimal | None:
    """Best-effort rate-per-mile extraction.

    Priority:
      1. metadata["rate_per_mile"] (explicit, e.g. "$2.12 / mi" or 2.12)
      2. metadata["distance_miles"] + load.price (compute)
      3. None (not enough data)
    """
    # 1. Explicit RPM in metadata
    raw_rpm = metadata.get("rate_per_mile") or metadata.get("rpm")
    if raw_rpm is not None:
        rpm = _parse_price_to_decimal(str(raw_rpm))
        if rpm and rpm > 0:
            return rpm

    # 2. Compute from price / distance
    price = _parse_price_to_decimal(load.price)
    raw_dist = metadata.get("distance_miles") or metadata.get("distance") or metadata.get("miles")
    if price and raw_dist:
        dist = _parse_price_to_decimal(str(raw_dist))
        if dist and dist > 0:
            return price / dist

    return None


# ── Region matching ────────────────────────────────────────────────────────────

# Tokens to skip: too generic, cause false positives (e.g. "New" matches "New Jersey")
_SKIP_TOKENS: frozenset = frozenset({"new", "the", "a", "an", "or", "and", "of"})
_MIN_TOKEN_LEN = 2


def _region_matches(load_field: str | None, preference: str | None) -> bool:
    """Case-insensitive match: any meaningful token from preference appears in load_field.

    Normalization:
      - Replace commas/slashes with spaces, then split into tokens
      - Skip tokens: length < 2, or in _SKIP_TOKENS (avoids "New" from "New Jersey / PA")
      - Match = true if any meaningful token is a substring of load_field

    Examples:
      preference="New Jersey / PA" -> tokens ["jersey", "pa"] -> match if load has either
      preference="Northeast" -> tokens ["northeast"] -> match if load contains "northeast"
      preference="Atlanta, Southeast" -> tokens ["atlanta", "southeast"] -> match if either
    """
    if not preference:
        return True  # no preference = match anything
    if not load_field:
        return False

    load_lower = load_field.lower()
    # Normalize: commas and slashes -> spaces, then split
    normalized = re.sub(r"[,/]+", " ", preference).strip()
    tokens = normalized.split()

    for token in tokens:
        t = token.lower().strip()
        if len(t) < _MIN_TOKEN_LEN or t in _SKIP_TOKENS:
            continue
        if t in load_lower:
            return True

    # No meaningful token matched
    return False


# ── Main scoring function ──────────────────────────────────────────────────────

def compute_match(
    driver: Driver,
    load: Load,
    merged_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Compute a 0–4 match score for a load against a driver's Scout profile.

    Returns a dict with keys:
      score          int (0–4)
      total          int (always 4)
      matched        list[str]  – criteria names that passed
      missed         list[str]  – criteria names that failed
      computed_rpm   str | None – the RPM value used (formatted), or None
      thresholds_used dict      – the driver thresholds applied
    """
    matched: list[str] = []
    missed: list[str] = []

    # ── Criterion 1: Origin ────────────────────────────────────────────────────
    if _region_matches(load.origin, driver.preferred_origin_region):
        matched.append("origin")
    else:
        missed.append("origin")

    # ── Criterion 2: Destination ───────────────────────────────────────────────
    if _region_matches(load.destination, driver.preferred_destination_region):
        matched.append("destination")
    else:
        missed.append("destination")

    # ── Criterion 3: Rate per mile ─────────────────────────────────────────────
    rpm = _extract_rpm(load, merged_metadata)
    computed_rpm_str: str | None = f"${rpm:.2f}/mi" if rpm is not None else None

    min_cpm = Decimal(str(driver.min_cpm)) if driver.min_cpm else None
    if min_cpm is None:
        # Driver has no minimum — treat as match
        matched.append("rate")
    elif rpm is None:
        # Can't determine rate — treat as miss (conservative)
        missed.append("rate")
    elif rpm >= min_cpm:
        matched.append("rate")
    else:
        missed.append("rate")

    # ── Criterion 4: Equipment type ────────────────────────────────────────────
    driver_equip = _normalise_equip(driver.preferred_equipment_type)
    load_equip = _normalise_equip(load.equipment_type)

    if not driver_equip:
        # No preference — match anything
        matched.append("equipment")
    elif not load_equip:
        # Load has no equipment type — treat as miss
        missed.append("equipment")
    elif driver_equip in load_equip or load_equip in driver_equip:
        matched.append("equipment")
    else:
        missed.append("equipment")

    return {
        "score": len(matched),
        "total": 4,
        "matched": matched,
        "missed": missed,
        "computed_rpm": computed_rpm_str,
        "thresholds_used": {
            "min_cpm": float(min_cpm) if min_cpm else None,
            "preferred_origin_region": driver.preferred_origin_region,
            "preferred_destination_region": driver.preferred_destination_region,
            "preferred_equipment_type": driver.preferred_equipment_type,
        },
    }
