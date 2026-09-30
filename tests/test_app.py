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
    assert {r["label"] for r in day["rows"]} == {"Potassium Chloride", "Calcium Gluconate", "Ceftriaxone"}


def test_safety_panel(client):
    s = client.get("/api/stays/101/safety", params={"as_of": "2150-01-01T11:00:00"}).json()
    assert [r["drug"] for r in s["running"]] == ["Potassium Chloride"]
    [flag] = s["flags"]
    assert (flag["status"], flag["drug"], flag["hypothetical"]) == ("do_not_infuse", "Potassium Chloride", False)
    assert s["blocked"] == []  # nothing in the rule table conflicts with a running KCl drip
    assert {lab["label"] for lab in s["labs"]} == {"Potassium", "Sodium"}


def test_summary_falls_back_without_llm(client, monkeypatch):
    monkeypatch.setattr(app_module, "summarize", lambda flags: summary.summarize(flags, base="http://127.0.0.1:9"))
    out = client.get("/api/stays/101/summary", params={"as_of": "2150-01-01T11:00:00"}).json()
    assert out["source"] == "rules" and out["flag_count"] >= 1
    assert "Hold Potassium Chloride" in out["text"]
