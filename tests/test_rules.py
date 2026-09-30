from datetime import datetime

from builders import admin, allergy, bundle, condition, medication, observation, request, t

from curie_infusion.fhir import BundleIndex
from curie_infusion.rules import blocked_medications, evaluate_flags, load_rules

AS_OF = datetime.fromisoformat(t("10:00"))
RULES = load_rules()
KCL = medication("kcl", "potassium-chloride", strength=(20, "mEq", 100, "mL"))
CEF = medication("cef", "ceftriaxone")
CAGLU = medication("caglu", "calcium-gluconate")
K = "2823-3"


def flags_for(*resources):
    return evaluate_flags(BundleIndex(bundle(*resources)), RULES, as_of=AS_OF)


def by_rule(flags, rule_id):
    return [f for f in flags if f.rule_id == rule_id]


RUNNING_KCL = admin("a1", "kcl", t("08:00"), rate=(50, "mL/h"), request_id="r1")
HIGH_K = "physio-hyperkalemia-potassium-chloride"


def test_high_potassium_blocks_running_kcl_with_evidence():
    [flag] = by_rule(
        flags_for(KCL, request("r1", "kcl"), RUNNING_KCL, observation("k1", K, 5.8, "mmol/L", t("09:00"), t("09:20"))),
        HIGH_K,
    )
    assert flag.status == "do_not_infuse"
    assert flag.targets == ["MedicationAdministration/a1"]
    assert flag.evidence[0]["reference"] == "Observation/k1"
    assert flag.citation


def test_normal_potassium_does_not_flag():
    flags = flags_for(KCL, request("r1", "kcl"), RUNNING_KCL, observation("k1", K, 4.2, "mmol/L", t("09:00")))
    assert by_rule(flags, HIGH_K) == []


def test_latest_value_wins():
    flags = flags_for(
        KCL,
        request("r1", "kcl"),
        RUNNING_KCL,
        observation("k1", K, 5.9, "mmol/L", t("06:00")),
        observation("k2", K, 4.8, "mmol/L", t("09:00")),
    )
    assert by_rule(flags, HIGH_K) == []


def test_result_not_yet_available_is_ignored():
    flags = flags_for(
        KCL, request("r1", "kcl"), RUNNING_KCL, observation("k1", K, 6.1, "mmol/L", t("09:50"), issued=t("10:30"))
    )
    [flag] = by_rule(flags, HIGH_K)
    assert flag.status == "insufficient_context"


def test_stale_or_missing_lab_is_insufficient_context_not_silently_clear():
    [flag] = by_rule(
        flags_for(KCL, request("r1", "kcl"), RUNNING_KCL, observation("k1", K, 4.0, "mmol/L", "2025-12-31T08:00:00+00:00")),
        HIGH_K,
    )
    assert flag.status == "insufficient_context"


def test_unit_mismatch_is_not_compared():
    [flag] = by_rule(
        flags_for(KCL, request("r1", "kcl"), RUNNING_KCL, observation("k1", K, 22, "mg/dL", t("09:00"))), HIGH_K
    )
    assert flag.status == "insufficient_context"


def test_active_order_not_yet_running_is_evaluated_once():
    flags = flags_for(KCL, request("r2", "kcl"), observation("k1", K, 5.8, "mmol/L", t("09:00")))
    [flag] = by_rule(flags, HIGH_K)
    assert flag.targets == ["MedicationRequest/r2"]

    flags = flags_for(KCL, request("r1", "kcl"), RUNNING_KCL, observation("k1", K, 5.8, "mmol/L", t("09:00")))
    assert len(by_rule(flags, HIGH_K)) == 1


def test_stopped_infusion_is_not_a_target():
    stopped = admin("a1", "kcl", t("06:00"), t("08:00"), rate=(50, "mL/h"), request_id="r1")
    flags = flags_for(KCL, request("r1", "kcl", status="completed"), stopped, observation("k1", K, 6.0, "mmol/L", t("09:00")))
    assert flags == []


def test_active_condition_blocks():
    [flag] = by_rule(
        flags_for(KCL, request("r1", "kcl"), RUNNING_KCL, condition("c1", "hyperkalemia"), observation("k1", K, 5.0, "mmol/L", t("09:00"))),
        "condition-hyperkalemia-potassium-chloride",
    )
    assert flag.status == "do_not_infuse"
    assert flag.evidence[0]["reference"] == "Condition/c1"


def test_resolved_condition_does_not_block():
    flags = flags_for(KCL, request("r1", "kcl"), RUNNING_KCL, condition("c1", "hyperkalemia", status="resolved"))
    assert by_rule(flags, "condition-hyperkalemia-potassium-chloride") == []


def test_allergy_to_active_drug_blocks():
    flags = flags_for(CEF, request("r1", "cef"), allergy("al1", "ceftriaxone"))
    [flag] = by_rule(flags, "allergy-match")
    assert flag.status == "do_not_infuse"
    assert flag.evidence[0]["detail"].startswith("criticality=high")


YSITE = "ysite-ceftriaxone-calcium"


def test_same_line_incompatibility_blocks():
    flags = flags_for(
        CEF, CAGLU,
        admin("a1", "cef", t("09:00"), line="CVC-distal"),
        admin("a2", "caglu", t("09:30"), line="CVC-distal"),
    )
    [flag] = by_rule(flags, YSITE)
    assert flag.status == "do_not_infuse"
    assert set(flag.targets) == {"MedicationAdministration/a1", "MedicationAdministration/a2"}


def test_different_lines_do_not_flag():
    flags = flags_for(
        CEF, CAGLU,
        admin("a1", "cef", t("09:00"), line="CVC-distal"),
        admin("a2", "caglu", t("09:30"), line="PIV-left"),
    )
    assert by_rule(flags, YSITE) == []


def test_unknown_line_needs_review():
    flags = flags_for(CEF, CAGLU, admin("a1", "cef", t("09:00"), line="CVC-distal"), request("r2", "caglu"))
    [flag] = by_rule(flags, YSITE)
    assert flag.status == "review"


def test_charted_infusion_covering_as_of_is_a_target_even_when_completed():
    # Replaying history: every MIMIC row is completed, but it was running at as_of.
    charted = admin("a1", "kcl", t("08:00"), t("12:00"), rate=(50, "mL/h"), status="completed")
    [flag] = by_rule(flags_for(KCL, charted, observation("k1", K, 5.9, "mmol/L", t("09:00"))), HIGH_K)
    assert flag.targets == ["MedicationAdministration/a1"]


def test_administration_without_start_is_not_a_target():
    ma = admin("a1", "kcl", t("08:00"), rate=(50, "mL/h"))
    del ma["effectivePeriod"]["start"]
    assert flags_for(KCL, ma, observation("k1", K, 5.9, "mmol/L", t("09:00"))) == []


def test_blocked_list_covers_drugs_that_are_not_ordered():
    idx = BundleIndex(bundle(observation("k1", K, 6.2, "mmol/L", t("09:00"))))
    blocked = blocked_medications(idx, RULES, AS_OF)
    kcl = [f for f in by_rule(blocked, HIGH_K)]
    assert [f.status for f in kcl] == ["do_not_infuse"]
    assert kcl[0].targets == ["Hypothetical/potassium-chloride"]


def test_blocked_list_includes_partner_of_a_running_drug():
    idx = BundleIndex(bundle(CEF, admin("a1", "cef", t("09:00"), line="CVC-distal")))
    flags = by_rule(blocked_medications(idx, RULES, AS_OF), YSITE)
    assert {f.status for f in flags} == {"review"}
    assert {f.targets[0] for f in flags} == {"Hypothetical/calcium-gluconate", "Hypothetical/calcium-chloride"}
    assert all(f.targets[1] == "MedicationAdministration/a1" for f in flags)


def test_blocked_list_skips_drugs_already_running():
    idx = BundleIndex(bundle(KCL, RUNNING_KCL, observation("k1", K, 6.2, "mmol/L", t("09:00"))))
    assert by_rule(blocked_medications(idx, RULES, AS_OF), HIGH_K) == []
