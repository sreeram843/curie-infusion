import pytest
from fastapi.testclient import TestClient
from mimic_fixture import write_mimic

from curie_infusion import app as app_module
from curie_infusion import summary
from curie_infusion.mimic.store import MimicStore, build_store


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("mimic")
    build_store(write_mimic(root / "src"), root / "store")
    app_module.store.cache_clear()
    app_module.STORE_DIR = root / "store"
    assert isinstance(app_module.store(), MimicStore)
    yield TestClient(app_module.app)
    app_module.store.cache_clear()


def test_index_and_stays(client):
    assert "Curie Infusion" in client.get("/").text
    assert [s["stay_id"] for s in client.get("/api/stays").json()] == [101]
    assert client.get("/api/stays/101").json()["subject_id"] == 1
    assert client.get("/api/stays/999").status_code == 404


def test_grid_rows_and_buckets(client):
    g = client.get("/api/stays/101/grid", params={"grain": "hour", "as_of": "2150-01-01T12:30:00"}).json()
    assert len(g["buckets"]) == 24
    kcl = next(r for r in g["rows"] if r["label"] == "Potassium Chloride")
    assert kcl["continuous"] and sum(c["amount"] for c in kcl["cells"].values()) == pytest.approx(20)
    assert client.get("/api/stays/101/grid", params={"grain": "month"}).status_code == 422
    day = client.get("/api/stays/101/grid", params={"grain": "day", "as_of": "2150-01-02T08:00:00"}).json()
    assert {r["label"] for r in day["rows"]} == {"Potassium Chloride", "Calcium Gluconate", "Ceftriaxone", "NaCl 0.9%"}


def test_safety_panel(client):
    s = client.get("/api/stays/101/safety", params={"as_of": "2150-01-01T11:00:00"}).json()
    assert [r["drug"] for r in s["running"]] == ["NaCl 0.9%", "Potassium Chloride"]
    [flag] = s["flags"]
    assert (flag["status"], flag["drug"], flag["hypothetical"]) == ("do_not_infuse", "Potassium Chloride", False)
    assert s["blocked"] == []  # nothing in the rule table conflicts with a running KCl drip
    assert {lab["label"] for lab in s["labs"]} == {"Potassium", "Sodium"}


def test_summary_falls_back_without_llm(client, monkeypatch):
    monkeypatch.setattr(app_module, "summarize", lambda flags: summary.summarize(flags, base="http://127.0.0.1:9"))
    out = client.get("/api/stays/101/summary", params={"as_of": "2150-01-01T11:00:00"}).json()
    assert out["source"] == "rules" and out["flag_count"] >= 1
    assert "Hold Potassium Chloride" in out["text"]


def test_cell_detail(client):
    params = {"itemid": 225166, "bucket": "2150-01-01T11:00:00", "grain": "hour", "as_of": "2150-01-01T23:00:00"}
    d = client.get("/api/stays/101/cell", params=params).json()
    assert d["label"] == "Potassium Chloride" and d["unit"] == "mEq"
    assert d["total"] == pytest.approx(10)
    assert d["bucket_end"].startswith("2150-01-01T12:00")
    [ev] = d["events"]
    assert ev["kind"] == "Continuous Med"
    assert ev["bag"] == [{"label": "NaCl 0.9%", "amount": 100, "unit": "mL", "rate": 50, "rate_unit": "mL/hour"}]
    assert client.get("/api/stays/101/cell", params={**params, "grain": "month"}).status_code == 422


def test_grid_rows_carry_itemid_for_drilldown(client):
    g = client.get("/api/stays/101/grid", params={"grain": "day", "as_of": "2150-01-02T08:00:00"}).json()
    assert {r["itemid"] for r in g["rows"]} == {225166, 221456, 225855, 225158}


def test_cell_detail_does_not_reveal_the_future(client):
    # At 11:30 the 10:30-12:30 drip is still running: no end time, no final total, no bag totals.
    params = {"itemid": 225166, "bucket": "2150-01-01T11:00:00", "grain": "hour", "as_of": "2150-01-01T11:30:00"}
    [ev] = client.get("/api/stays/101/cell", params=params).json()["events"]
    assert ev["running"] is True and ev["end"] is None
    assert ev["amount"] == pytest.approx(10)  # delivered so far, not the 20 charted for the whole bag
    assert ev["in_bucket"] == pytest.approx(5)
    assert ev["bag"] == [{"label": "NaCl 0.9%", "amount": None, "unit": "mL", "rate": 50, "rate_unit": "mL/hour"}]


def test_billing_page_and_endpoint(client, tmp_path, monkeypatch):
    (tmp_path / "Payment Limit File.csv").write_text(
        "HCPCS Code,Short Description,HCPCS Code Dosage,Payment Limit\n"
        "J3480,Inj potassium chloride,2 MEQ,0.130\nJ0612,Inj calcium gluconate,10 MG,0.024\n"
    )
    monkeypatch.setattr(app_module, "CMS_DIR", tmp_path)
    assert "Billing" in client.get("/billing").text
    b = client.get("/api/stays/101/billing").json()
    by_code = {(line["code"], line["kind"]): line for line in b["lines"]}
    kcl = by_code[("J3480", "drug")]
    assert (kcl["units"], kcl["charge"]) == (10, pytest.approx(1.3))  # 20 mEq / 2 mEq
    assert by_code[("J0612", "drug")]["units"] == 200  # 2 g / 10 mg
    assert ("J7030", "drug") not in by_code  # the NaCl carrier of the KCl drip is not billed alone
    assert by_code[("J0696", "drug")]["status"] == "unpriced"  # ceftriaxone: no price in this file
    assert by_code[("96365", "administration")]["status"] == "units_only"
    assert b["totals"]["drugs"] == pytest.approx(1.3 + 4.8)
    assert b["sources"]["opps"] is None and b["drg"][0]["drg_code"] in {"871", "720"}
