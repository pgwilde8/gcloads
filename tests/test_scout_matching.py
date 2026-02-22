"""Tests for app/services/scout_matching.py

Run with:  pytest tests/test_scout_matching.py -v
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.scout_matching import (
    _extract_rpm,
    _normalise_equip,
    _region_matches,
    compute_match,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _driver(**kwargs):
    defaults = dict(
        preferred_origin_region=None,
        preferred_destination_region=None,
        preferred_equipment_type=None,
        min_cpm=None,
        auto_send_on_perfect_match=False,
        approval_threshold=3,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _load(**kwargs):
    defaults = dict(
        origin="Atlanta, GA",
        destination="Chicago, IL",
        equipment_type="Dry Van",
        price="$3200",
        load_metadata=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ── _normalise_equip ───────────────────────────────────────────────────────────

class TestNormaliseEquip:
    def test_dry_van_aliases(self):
        assert _normalise_equip("Van") == "dry van"
        assert _normalise_equip("DRY VAN") == "dry van"
        assert _normalise_equip("FTL") == "dry van"

    def test_reefer_aliases(self):
        assert _normalise_equip("Reefer") == "refrigerated"
        assert _normalise_equip("REF") == "refrigerated"

    def test_flatbed_aliases(self):
        assert _normalise_equip("Flatbed") == "flatbed"
        assert _normalise_equip("FLAT") == "flatbed"
        assert _normalise_equip("FB") == "flatbed"

    def test_none_returns_empty(self):
        assert _normalise_equip(None) == ""

    def test_unknown_passthrough(self):
        result = _normalise_equip("Conestoga")
        assert result == "conestoga"


# ── _region_matches ────────────────────────────────────────────────────────────

class TestRegionMatches:
    def test_no_preference_always_matches(self):
        assert _region_matches("Atlanta, GA", None) is True
        assert _region_matches("Atlanta, GA", "") is True

    def test_no_load_field_never_matches(self):
        assert _region_matches(None, "Atlanta") is False
        assert _region_matches("", "Atlanta") is False

    def test_case_insensitive_substring(self):
        assert _region_matches("Atlanta, GA", "atlanta") is True
        assert _region_matches("ATLANTA, GA", "Atlanta") is True

    def test_first_token_of_comma_list(self):
        # "Atlanta, Southeast" → first token "Atlanta" → matches "Atlanta, GA"
        assert _region_matches("Atlanta, GA", "Atlanta, Southeast") is True

    def test_no_match(self):
        assert _region_matches("Dallas, TX", "Atlanta") is False


# ── _extract_rpm ───────────────────────────────────────────────────────────────

class TestExtractRpm:
    def test_explicit_rpm_string(self):
        load = _load(price="$3200")
        rpm = _extract_rpm(load, {"rate_per_mile": "$2.12 / mi"})
        assert rpm == Decimal("2.12")

    def test_explicit_rpm_float(self):
        load = _load(price="$3200")
        rpm = _extract_rpm(load, {"rate_per_mile": 2.5})
        assert rpm == Decimal("2.5")

    def test_computed_from_price_and_distance(self):
        load = _load(price="$3200")
        rpm = _extract_rpm(load, {"distance_miles": "1000"})
        assert rpm == Decimal("3.2")

    def test_no_data_returns_none(self):
        load = _load(price=None)
        rpm = _extract_rpm(load, {})
        assert rpm is None

    def test_zero_distance_returns_none(self):
        load = _load(price="$3200")
        rpm = _extract_rpm(load, {"distance_miles": "0"})
        assert rpm is None


# ── compute_match ──────────────────────────────────────────────────────────────

class TestComputeMatch:
    def test_perfect_match_all_set(self):
        driver = _driver(
            preferred_origin_region="Atlanta",
            preferred_destination_region="Chicago",
            preferred_equipment_type="Dry Van",
            min_cpm=2.0,
        )
        load = _load(
            origin="Atlanta, GA",
            destination="Chicago, IL",
            equipment_type="Dry Van",
            price="$3200",
        )
        result = compute_match(driver, load, {"rate_per_mile": "2.50"})
        assert result["score"] == 4
        assert set(result["matched"]) == {"origin", "destination", "rate", "equipment"}
        assert result["missed"] == []

    def test_no_preferences_always_perfect(self):
        """Empty driver profile = all criteria match (driver doesn't care)."""
        driver = _driver()
        load = _load()
        result = compute_match(driver, load, {})
        assert result["score"] == 4

    def test_origin_miss(self):
        driver = _driver(preferred_origin_region="Dallas")
        load = _load(origin="Atlanta, GA")
        result = compute_match(driver, load, {})
        assert "origin" in result["missed"]

    def test_destination_miss(self):
        driver = _driver(preferred_destination_region="Miami")
        load = _load(destination="Chicago, IL")
        result = compute_match(driver, load, {})
        assert "destination" in result["missed"]

    def test_rate_miss_below_minimum(self):
        driver = _driver(min_cpm=3.0)
        load = _load(price="$1500")
        result = compute_match(driver, load, {"rate_per_mile": "2.00"})
        assert "rate" in result["missed"]

    def test_rate_miss_no_rpm_data(self):
        """When RPM can't be determined and driver has a minimum, it's a miss."""
        driver = _driver(min_cpm=2.0)
        load = _load(price=None)
        result = compute_match(driver, load, {})
        assert "rate" in result["missed"]

    def test_rate_match_no_minimum(self):
        """When driver has no min_cpm, rate always matches."""
        driver = _driver(min_cpm=None)
        load = _load(price="$500")
        result = compute_match(driver, load, {})
        assert "rate" in result["matched"]

    def test_equipment_miss(self):
        driver = _driver(preferred_equipment_type="Flatbed")
        load = _load(equipment_type="Reefer")
        result = compute_match(driver, load, {})
        assert "equipment" in result["missed"]

    def test_equipment_alias_match(self):
        driver = _driver(preferred_equipment_type="Van")
        load = _load(equipment_type="Dry Van")
        result = compute_match(driver, load, {})
        assert "equipment" in result["matched"]

    def test_score_3_of_4(self):
        driver = _driver(
            preferred_origin_region="Atlanta",
            preferred_destination_region="Chicago",
            preferred_equipment_type="Flatbed",  # will miss
            min_cpm=2.0,
        )
        load = _load(
            origin="Atlanta, GA",
            destination="Chicago, IL",
            equipment_type="Dry Van",
            price="$3200",
        )
        result = compute_match(driver, load, {"rate_per_mile": "2.50"})
        assert result["score"] == 3
        assert "equipment" in result["missed"]

    def test_output_shape(self):
        result = compute_match(_driver(), _load(), {})
        assert "score" in result
        assert "total" in result
        assert result["total"] == 4
        assert "matched" in result
        assert "missed" in result
        assert "computed_rpm" in result
        assert "thresholds_used" in result


# ── _decide_next_step (integration-style, no DB) ──────────────────────────────

class TestDecideNextStep:
    """Test the routing logic without a real DB by calling the function directly."""

    def _call(self, driver, match, triage, broker_email):
        from app.routes.ingest import _decide_next_step
        return _decide_next_step(driver, match, triage, broker_email)

    def _match(self, score):
        return {"score": score, "total": 4, "matched": [], "missed": []}

    def _triage(self, action="EMAIL_BROKER", standing_status="NEUTRAL"):
        return {
            "action": action,
            "standing": {"status": standing_status},
            "email": "broker@example.com",
        }

    def test_scout_paused(self):
        driver = _driver(scout_active=False)
        assert self._call(driver, self._match(4), self._triage(), "broker@example.com") == "SCOUT_PAUSED"

    def test_setup_required_no_profile(self):
        driver = _driver(scout_active=True)
        assert self._call(driver, self._match(4), self._triage(), "broker@example.com") == "SETUP_REQUIRED"

    def test_broker_blocked(self):
        driver = _driver(scout_active=True, preferred_origin_region="Atlanta")
        assert self._call(driver, self._match(4), self._triage(standing_status="BLACKLISTED"), "broker@example.com") == "BROKER_BLOCKED"

    def test_call_required(self):
        driver = _driver(scout_active=True, preferred_origin_region="Atlanta")
        assert self._call(driver, self._match(4), self._triage(action="CALL_REQUIRED"), "broker@example.com") == "CALL_REQUIRED"

    def test_missing_broker_email(self):
        driver = _driver(scout_active=True, preferred_origin_region="Atlanta")
        assert self._call(driver, self._match(4), self._triage(), None) == "MISSING_BROKER_EMAIL"

    def test_perfect_match_auto_send_enabled(self):
        driver = _driver(scout_active=True, preferred_origin_region="Atlanta", auto_send_on_perfect_match=True)
        assert self._call(driver, self._match(4), self._triage(), "broker@example.com") == "AUTO_SENT"

    def test_perfect_match_auto_send_disabled(self):
        driver = _driver(scout_active=True, preferred_origin_region="Atlanta", auto_send_on_perfect_match=False, approval_threshold=3)
        assert self._call(driver, self._match(4), self._triage(), "broker@example.com") == "NEEDS_APPROVAL"

    def test_score_meets_threshold(self):
        driver = _driver(scout_active=True, preferred_origin_region="Atlanta", approval_threshold=3)
        assert self._call(driver, self._match(3), self._triage(), "broker@example.com") == "NEEDS_APPROVAL"

    def test_score_below_threshold(self):
        driver = _driver(scout_active=True, preferred_origin_region="Atlanta", approval_threshold=3)
        assert self._call(driver, self._match(2), self._triage(), "broker@example.com") == "SAVED_ONLY"
