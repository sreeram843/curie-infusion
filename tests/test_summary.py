from curie_infusion import summary
from curie_infusion.summary import fallback_summary, summarize, validate

HIGH_K = {
    "rule_id": "physio-hyperkalemia-potassium-chloride", "status": "do_not_infuse",
    "drug": "Potassium Chloride", "message": "Serum potassium above threshold.",
    "evidence": [{"reference": "Observation/k1", "detail": "7.4 mEq/L at 2160-03-02T03:19:00+00:00"}],
    "hypothetical": False, "citation": "x", "kind": "physiologic", "targets": ["MedicationAdministration/a"],
}
GOOD = "- Hold Potassium Chloride: potassium 7.4 mEq/L is high [physio-hyperkalemia-potassium-chloride]"


def test_valid_summary_passes():
    assert validate(GOOD, [HIGH_K]) is None


def test_copied_or_invented_number_is_rejected():
    assert "6.1" in validate(GOOD.replace("7.4", "6.1"), [HIGH_K])


def test_wrong_action_is_rejected():
    assert "wrong action" in validate(GOOD.replace("- Hold", "- Do not start"), [HIGH_K])


def test_extra_or_missing_bullets_are_rejected():
    assert "expected 1 bullets" in validate(GOOD + "\n- Review: (missing data)", [HIGH_K])
    assert "does not name" in validate("- Hold something else [other-rule]", [HIGH_K])


def test_no_flags_never_calls_the_model(monkeypatch):
    monkeypatch.setattr(summary.urllib.request, "urlopen", lambda *a, **k: 1 / 0)
    assert summarize([])["source"] == "rules"


def test_unreachable_model_falls_back_to_rule_text():
    out = summarize([HIGH_K], base="http://127.0.0.1:9")
    assert out["source"] == "rules" and out["error"]
    assert out["text"] == fallback_summary([HIGH_K])
    assert out["text"].startswith("- Hold Potassium Chloride")
