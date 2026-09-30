import json

import pytest

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


# Regression: numbers are compared by numeric VALUE, not raw formatting. An LLM that rewords
# "7.4" as "7.40" or "7.400000" (the same number, a valid way to restate a decimal) must pass;
# only numbers genuinely absent from the evidence (e.g. a rounded "7", or an invented "6.1")
# are rejections. Before the fix the raw-string token compare rejected valid restatements.
@pytest.mark.parametrize(
    "bullet",
    [
        GOOD,
        GOOD.replace("7.4", "7.40"),
        GOOD.replace("7.4", "7.400000"),
        GOOD.replace("7.4", "07.4"),
    ],
)
def test_formatting_variant_of_evidence_number_passes(bullet):
    assert validate(bullet, [HIGH_K]) is None


@pytest.mark.parametrize(
    "bullet",
    [
        GOOD.replace("7.4", "6.1"),  # invented value
        GOOD.replace("7.4", "7"),  # rounded DOWN to a different number
        GOOD.replace("7.4", "7.400001"),  # off by one in the last place
    ],
)
def test_genuinely_different_number_still_rejected(bullet):
    assert validate(bullet, [HIGH_K]) is not None


def test_number_in_rule_id_formats_equal_tokens():
    # The rule_id itself carries digits; re-stating it must not be misread as a cited number.
    flag = dict(HIGH_K, rule_id="physio-hyperkalemia-7.4-potassium")
    assert validate("- Hold Potassium Chloride: potassium 7.4 mEq/L [physio-hyperkalemia-7.4-potassium]",
                    [flag]) is None


def test_no_flags_never_calls_the_model(monkeypatch):
    monkeypatch.setattr(summary.urllib.request, "urlopen", lambda *a, **k: 1 / 0)
    assert summarize([])["source"] == "rules"


def test_unreachable_model_falls_back_to_rule_text():
    out = summarize([HIGH_K], base="http://127.0.0.1:9")
    assert out["source"] == "rules" and out["error"]
    assert out["text"] == fallback_summary([HIGH_K])
    assert out["text"].startswith("- Hold Potassium Chloride")


# --- mocked-urlopen coverage of the LLM round-trip -------------------------
class _FakeResp:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode()
        self.read = lambda *a, **k: self._data
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _run(monkeypatch, urlopen, flags, base="http://127.0.0.1:1234", model="local-model"):
    calls = []
    def recording(req, *a, **k):
        if isinstance(req, str):
            calls.append({"url": req, "body": None})
        else:
            calls.append({"url": req.full_url, "body": json.loads(req.data)})
        return urlopen(req, *a, **k)
    monkeypatch.setattr(summary.urllib.request, "urlopen", recording)
    return summarize(flags, base=base, model=model), calls


def test_llm_valid_response_accepted(monkeypatch):
    def urlopen(req, *a, **k):
        return _FakeResp({"choices": [{"message": {"content": GOOD}}]})
    out, calls = _run(monkeypatch, urlopen, [HIGH_K])
    assert out["source"] == "llm" and out["error"] is None
    assert out["model"] == "local-model"
    # user message is the compacted, sorted flag JSON (one bullet per flag to reword)
    sent = calls[0]["body"]["messages"][1]["content"]
    assert "Potassium Chloride" in sent and "hypothetical" not in sent


def test_llm_rejected_output_falls_back_to_rule_text(monkeypatch):
    # Model hilariously invents a number -> validate() rejects -> dry fallback, with the reason set.
    def urlopen(req, *a, **k):
        return _FakeResp({"choices": [{"message": {"content": GOOD.replace("7.4", "3.3")}}]})
    out, _ = _run(monkeypatch, urlopen, [HIGH_K])
    assert out["source"] == "rules" and out["model"] == "local-model"
    assert "LLM output rejected" in out["error"]
    assert out["text"] == fallback_summary([HIGH_K])


def test_default_model_resolved_when_model_empty(monkeypatch):
    def urlopen(req, *a, **k):
        url = req if isinstance(req, str) else req.full_url
        if url.endswith("/v1/models"):
            return _FakeResp({"data": [{"id": "embed-1"}, {"id": "local-chat-v1"}]})
        return _FakeResp({"choices": [{"message": {"content": GOOD}}]})
    out, _ = _run(monkeypatch, urlopen, [HIGH_K], model="")
    assert out["source"] == "llm" and out["model"] == "local-chat-v1"  # embed model skipped


def test_empty_model_list_raises_and_falls_back(monkeypatch):
    def urlopen(req, *a, **k):
        return _FakeResp({"data": [{"id": "embed-1"}]})  # no chat model
    out, _ = _run(monkeypatch, urlopen, [HIGH_K], model="")
    assert out["source"] == "rules" and "no chat model" in out["error"]


def test_malformed_llm_payload_falls_back(monkeypatch):
    def urlopen(req, *a, **k):
        return _FakeResp({"oops": 1})  # no choices -> KeyError path
    out, _ = _run(monkeypatch, urlopen, [HIGH_K])
    assert out["source"] == "rules" and out["error"]