import json
from pathlib import Path

import pytest

from curie_infusion.cli import main

FIXTURE = Path(__file__).parent.parent / "fixtures" / "icu_bundle.json"


def test_demo_bundle_end_to_end(capsys):
    assert main(["evaluate", str(FIXTURE), "--as-of", "2026-01-01T10:00:00+00:00"]) == 0
    state = json.loads(capsys.readouterr().out)

    statuses = {(f["rule_id"], f["status"]) for f in state["flags"]}
    assert ("physio-hyperkalemia-potassium-chloride", "do_not_infuse") in statuses
    assert ("ysite-ceftriaxone-calcium", "do_not_infuse") in statuses

    kcl = next(o for o in state["ledger"] if o["order"] == "MedicationRequest/mr-kcl")
    assert kcl["total_amount"] == {"mEq": pytest.approx(30)}
    assert state["reconciliation"] == [
        {"kind": "order_without_administration", "reference": "MedicationRequest/mr-insulin"}
    ]
