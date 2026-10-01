from datetime import datetime

import pytest
from mimic_fixture import write_mimic

from curie_infusion.mimic import tabs
from curie_infusion.mimic.store import MimicStore, build_store


@pytest.fixture(scope="module")
def st(tmp_path_factory):
    root = tmp_path_factory.mktemp("mimic")
    build_store(write_mimic(root / "src"), root / "store")
    return MimicStore(root / "store")


@pytest.fixture(scope="module")
def stay(st):
    return st.stay(101)


def at(hhmm, day=1):
    return datetime.fromisoformat(f"2150-01-0{day} {hhmm}:00")


def cells(series, label):
    row = next(r for g in series["groups"] for r in g["rows"] if r["label"] == label)
    return {datetime.fromisoformat(b).hour: c for b, c in row["cells"].items()}


def test_window_alignment(stay):
    start, buckets = tabs.window(stay, "hour", at("12:30"))
    assert (len(buckets), buckets[-1]) == (24, at("12:00"))
    assert tabs.floor(at("12:30", day=3), "week").weekday() == 0


def test_fluid_balance(st, stay):
    s = tabs.fluids(st, stay, "hour", at("12:00"))
    assert {h: round(c["v"]) for h, c in cells(s, "Drips").items()} == {10: 25, 11: 50}
    # 11:00 output is 150 Foley minus 50 irrigant instilled; the 11:30 output is not charted until 13:00
    assert {h: c["v"] for h, c in cells(s, "Foley").items()} == {10: 200, 11: 150}
    assert cells(s, "GU Irrigant Volume In")[11]["v"] == -50
    net = cells(s, "Net balance (in − out)")
    assert (round(net[10]["v"]), net[10]["flag"]) == (-175, True)


def test_labs_show_results_only_once_resulted(st, stay):
    s = tabs.labs(st, stay, "hour", at("12:00"))
    k = cells(s, "Potassium")
    assert list(k) == [10] and k[10]["v"] == 6.2 and k[10]["flag"] is True
    assert cells(tabs.labs(st, stay, "hour", at("13:00")), "Potassium")[11]["v"] == 4.1
    detail = tabs.series_cell(st, stay, "labs", "50971", "hour", at("10:00"), at("13:00"))
    assert [r["value"] for r in detail["rows"]] == ["6.2"]


def test_assessments_numeric_and_text(st, stay):
    s = tabs.assessments(st, stay, "hour", at("12:00"))
    assert cells(s, "RASS (sedation)")[10]["v"] == -1
    assert cells(s, "Ventilator mode")[10]["text"] == "CMV/ASSIST"


def test_nutrition_spreads_calories(st, stay):
    s = tabs.nutrition(st, stay, "hour", at("23:00"))
    assert {h: round(c["v"]) for h, c in cells(s, "Calories").items()} == {10: 55, 11: 110, 12: 55}


def test_orders_status_at_the_clock(st, stay):
    o = tabs.orders(st, stay, at("12:00"))["rows"]
    assert [r["medication"] for r in o] == ["CefTRIAXone"]  # famotidine not entered until day 2
    [cef] = o
    assert (cef["status_now"], cef["final_status"], cef["dose"], cef["given"], cef["stop"]) == ("active", None, "1 g", 1, None)
    later = {r["medication"]: r for r in tabs.orders(st, stay, at("12:00", day=3))["rows"]}
    assert later["CefTRIAXone"]["final_status"] == "Discontinued via patient discharge"
    assert later["CefTRIAXone"]["not_given"] == 1 and later["Famotidine"]["status_now"] == "active"


def test_emar_rows(st, stay):
    [row] = tabs.emar(st, stay, at("12:00"))["rows"]
    assert (row["event"], row["dose_given"], row["route"], row["administration_type"]) == ("Administered", "1 g", "IV", "IV Antibiotic")


def test_micro_pending_then_resulted(st, stay):
    [pending] = tabs.micro(st, stay, at("12:00"))["rows"]
    assert (pending["status"], pending["organisms"]) == ("pending", None)
    [done] = tabs.micro(st, stay, at("11:00", day=2))["rows"]
    assert (done["organisms"], done["resistant"], done["susceptible"]) == ("ESCHERICHIA COLI", "AMPICILLIN", "CEFTRIAXONE")


def test_procedures_hide_future_end(st, stay):
    rows = {r["procedure"]: r for r in tabs.procedures(st, stay, at("12:00"))["rows"]}
    assert (rows["Multi Lumen"]["status"], rows["Multi Lumen"]["end"], rows["Multi Lumen"]["location"]) == ("ongoing", None, "Right IJ")
    assert rows["Chest X-Ray"]["status"] == "FinishedRunning"


def test_journey_hides_discharge_coding_until_discharge(st, stay):
    j = tabs.journey(st, stay, at("12:00"))
    assert j["coding_hidden"] and j["coding"] == [] and "dischtime" not in j["admission"]
    assert [(r["what"], r["until"]) for r in j["rows"] if r["kind"] == "transfer"] == [
        ("Emergency Department", at("08:00")), ("MICU", None)]
    assert any(c["title"] == "Sepsis, unspecified organism" for c in tabs.journey(st, stay, at("12:00"), hindsight=True)["coding"])
    done = tabs.journey(st, stay, at("12:00", day=6))
    assert not done["coding_hidden"] and done["admission"]["discharge_location"] == "HOME"
