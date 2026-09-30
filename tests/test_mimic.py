from datetime import UTC, datetime

import pytest
from mimic_fixture import write_mimic

from curie_infusion.fhir import BundleIndex
from curie_infusion.mimic.fhir_adapter import build_bundle
from curie_infusion.mimic.store import MimicStore, build_store
from curie_infusion.rules import evaluate_flags, load_rules


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    root = tmp_path_factory.mktemp("mimic")
    build_store(write_mimic(root / "src"), root / "store")
    return MimicStore(root / "store")


def at(hhmm: str, day: int = 1) -> datetime:
    return datetime.fromisoformat(f"2150-01-0{day} {hhmm}:00")


def test_store_lists_only_stays_with_infusions(store):
    assert [s["stay_id"] for s in store.stays()] == [101]
    assert store.stay(101)["n_infusion_rows"] == 4
    assert store.stay(999) is None


def test_hourly_grid_spreads_amount_by_overlap(store):
    cells = store.grid(101, "hour", at("08:00"), at("23:00"))
    kcl = {c["bucket"].hour: c["amount"] for c in cells if c["label"] == "Potassium Chloride"}
    assert kcl == {10: pytest.approx(5), 11: pytest.approx(10), 12: pytest.approx(5)}
    [ca] = [c for c in cells if c["label"] == "Calcium Gluconate"]
    assert (ca["bucket"].hour, ca["amount"]) == (11, pytest.approx(2))


def test_grid_stops_at_the_replay_clock(store):
    cells = store.grid(101, "hour", at("08:00"), at("11:30"))
    kcl = {c["bucket"].hour: c["amount"] for c in cells if c["label"] == "Potassium Chloride"}
    assert kcl == {10: pytest.approx(5), 11: pytest.approx(5)}


def test_daily_and_weekly_totals_match_charted_amounts(store):
    for grain in ("day", "week"):
        cells = store.grid(101, grain, at("00:00"), at("00:00", day=3))
        assert sum(c["amount"] for c in cells if c["label"] == "Potassium Chloride") == pytest.approx(20)


def test_unknown_grain_is_rejected(store):
    with pytest.raises(ValueError):
        store.grid(101, "month", at("08:00"), at("09:00"))


def bundle_at(store, clock):
    stay = store.stay(101)
    labs = store.labs(1, at("00:00"), clock)
    return BundleIndex(build_bundle(stay, store.infusions(101, until=clock), labs, clock))


def test_bundle_shows_only_what_had_happened(store):
    idx = bundle_at(store, at("11:00"))
    [ma] = [m for m in idx.of("MedicationAdministration") if idx.drug_label(m) == "Potassium Chloride"]
    assert ma["status"] == "in-progress"
    assert "end" not in ma["effectivePeriod"]  # a live feed would not know the end yet
    assert ma["effectivePeriod"]["start"].endswith("+00:00")
    [order] = idx.of("MedicationRequest")
    assert order["status"] == "active"
    assert idx.latest_observation(("http://loinc.org", "29463-7"), at("11:00").replace(tzinfo=UTC))


def test_mimic_potassium_raises_the_hyperkalemia_flag(store):
    clock = at("11:00")
    flags = evaluate_flags(bundle_at(store, clock), load_rules(), clock.replace(tzinfo=UTC))
    [flag] = [f for f in flags if f.rule_id == "physio-hyperkalemia-potassium-chloride"]
    assert flag.status == "do_not_infuse"
    assert "6.2" in flag.evidence[0]["detail"]


def test_lab_not_yet_stored_is_not_used(store):
    # The 4.1 drawn at 11:50 is not stored until 13:00, so at 12:00 the 6.2 still applies.
    clock = at("12:00")
    flags = evaluate_flags(bundle_at(store, clock), load_rules(), clock.replace(tzinfo=UTC))
    [flag] = [f for f in flags if f.rule_id == "physio-hyperkalemia-potassium-chloride"]
    assert "6.2" in flag.evidence[0]["detail"]


def test_cell_events_explain_a_grid_cell(store):
    [ev] = store.cell_events(101, 225166, "hour", at("11:00"), at("23:00"))
    assert ev["in_bucket"] == pytest.approx(10)  # half of the 20 mEq 10:30-12:30 drip
    assert (ev["amount"], ev["rate"], ev["rateuom"]) == (pytest.approx(20), pytest.approx(50), "mL/hour")
    assert ev["statusdescription"] == "FinishedRunning"
    assert ev["bucket_end"] == at("12:00")


def test_cell_events_respect_the_replay_clock(store):
    [ev] = store.cell_events(101, 225166, "hour", at("11:00"), at("11:30"))
    assert ev["in_bucket"] == pytest.approx(5)
    assert store.cell_events(101, 225855, "hour", at("11:00"), at("23:00")) == []


def test_cell_events_list_the_rest_of_the_bag(store):
    [ev] = store.cell_events(101, 225166, "hour", at("11:00"), at("23:00"))
    assert [(b["label"], b["amount"], b["rate"]) for b in ev["bag"]] == [("NaCl 0.9%", 100, 50)]


def test_vitals_are_cleaned_and_normalized(store):
    cells = {(c["vital"], c["bucket"].hour): c for c in store.vitals_grid(101, "hour", at("08:00"), at("23:00"))}
    hr = cells[("hr", 10)]
    assert (hr["median"], hr["min"], hr["max"], hr["n"], hr["last"]) == (85, 80, 90, 2, 90)  # 9999 dropped
    assert cells[("temp", 10)]["median"] == pytest.approx(98.6)  # 37 °C
    assert cells[("sbp", 10)]["median"] == 120


def test_vitals_grid_stops_at_the_clock(store):
    cells = store.vitals_grid(101, "hour", at("08:00"), at("10:20"))
    assert {c["vital"]: c["n"] for c in cells} == {"hr": 1, "sbp": 1, "temp": 1}


def test_latest_vitals_use_charting_time(store):
    # The 130 bpm drawn at 11:50 is not charted until 12:30.
    assert {v["vital"]: v["value"] for v in store.vitals_latest(101, at("12:00"))}["hr"] == 90
    assert {v["vital"]: v["value"] for v in store.vitals_latest(101, at("12:30"))}["hr"] == 130


def test_vital_readings_for_a_cell(store):
    rows = store.vital_readings(101, "hr", "hour", at("10:00"), at("23:00"))
    assert [(r["value"], r["label"]) for r in rows] == [(80, "Heart Rate"), (90, "Heart Rate")]
