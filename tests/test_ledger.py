from datetime import datetime

import pytest
from builders import admin, bundle, medication, observation, request, t

from curie_infusion.fhir import BundleIndex
from curie_infusion.ledger import compute_ledger, reconcile

AS_OF = datetime.fromisoformat(t("10:00"))
KCL = medication("kcl", "potassium-chloride", strength=(20, "mEq", 100, "mL"))
NOREPI = medication("norepi", "norepinephrine")
WEIGHT = "29463-7"


def ledger_for(*resources, **kw):
    return compute_ledger(BundleIndex(bundle(*resources)), as_of=AS_OF, **kw)


def test_mass_rate_accumulates_over_elapsed_time():
    [order] = ledger_for(
        NOREPI, request("r1", "norepi"), admin("a1", "norepi", t("07:00"), rate=(8, "mcg/min"), request_id="r1")
    )
    assert order.total_amount == {"mg": pytest.approx(1.44)}
    assert order.total_volume_ml is None


def test_volume_rate_uses_ingredient_concentration():
    [order] = ledger_for(
        KCL, request("r1", "kcl"), admin("a1", "kcl", t("08:00"), t("10:00"), rate=(50, "mL/h"), request_id="r1")
    )
    assert order.total_volume_ml == pytest.approx(100)
    assert order.total_amount == {"mEq": pytest.approx(20)}


def test_titrations_group_under_parent_order():
    [order] = ledger_for(
        KCL,
        request("r1", "kcl"),
        admin("a1", "kcl", t("06:00"), t("08:00"), rate=(25, "mL/h"), request_id="r1"),
        admin("a2", "kcl", t("08:00"), rate=(50, "mL/h"), request_id="r1"),
    )
    assert order.order == "MedicationRequest/r1"
    assert [s.administration for s in order.segments] == [
        "MedicationAdministration/a1",
        "MedicationAdministration/a2",
    ]
    assert order.total_amount == {"mEq": pytest.approx(30)}
    assert order.total_volume_ml == pytest.approx(150)


def test_window_clamps_segments():
    [order] = ledger_for(
        KCL,
        request("r1", "kcl"),
        admin("a1", "kcl", t("06:00"), t("08:00"), rate=(25, "mL/h"), request_id="r1"),
        window_start=datetime.fromisoformat(t("07:00")),
    )
    assert order.total_volume_ml == pytest.approx(25)


def test_volume_rate_without_concentration_is_reported_not_guessed():
    [order] = ledger_for(
        NOREPI, request("r1", "norepi"), admin("a1", "norepi", t("09:00"), rate=(10, "mL/h"), request_id="r1")
    )
    assert order.total_volume_ml == pytest.approx(10)
    assert order.total_amount == {}
    assert any("concentration" in w for w in order.warnings)


def test_unsupported_rate_unit_is_a_warning():
    [order] = ledger_for(
        NOREPI, request("r1", "norepi"), admin("a1", "norepi", t("09:00"), rate=(0.1, "mcg/kg/min"), request_id="r1")
    )
    assert order.segments == []
    assert any("mcg/kg/min" in w for w in order.warnings)


def test_entered_in_error_is_excluded():
    assert ledger_for(
        KCL, admin("a1", "kcl", t("08:00"), t("09:00"), rate=(50, "mL/h"), status="entered-in-error")
    ) == []


def test_reconcile_flags_orders_and_administrations_without_a_match():
    idx = BundleIndex(
        bundle(
            KCL,
            request("r1", "kcl"),
            request("r2", "kcl"),
            admin("a1", "kcl", t("08:00"), rate=(50, "mL/h"), request_id="r1"),
            admin("a2", "kcl", t("08:00"), rate=(50, "mL/h")),
        )
    )
    findings = {(f["kind"], f["reference"]) for f in reconcile(idx)}
    assert findings == {
        ("order_without_administration", "MedicationRequest/r2"),
        ("administration_without_order", "MedicationAdministration/a2"),
    }


@pytest.mark.parametrize(
    ("unit", "rate", "expected"),
    [
        ("units/hour", 5, {"unit": pytest.approx(15)}),
        ("mg/hour", 2, {"mg": pytest.approx(6)}),
        ("mEq./hour", 10, {"mEq": pytest.approx(30)}),
        ("grams/hour", 1, {"mg": pytest.approx(3000)}),
        ("mg/min", 1, {"mg": pytest.approx(180)}),
    ],
)
def test_mimic_rate_units_are_understood(unit, rate, expected):
    [order] = ledger_for(NOREPI, admin("a1", "norepi", t("07:00"), rate=(rate, unit)))
    assert order.total_amount == expected


def test_mimic_volume_rate_per_hour():
    [order] = ledger_for(KCL, admin("a1", "kcl", t("08:00"), t("10:00"), rate=(50, "mL/hour")))
    assert order.total_volume_ml == pytest.approx(100)


def test_weight_based_rate_uses_latest_body_weight():
    [order] = ledger_for(
        NOREPI,
        observation("w1", WEIGHT, 80, "kg", t("06:00")),
        admin("a1", "norepi", t("09:00"), rate=(0.1, "mcg/kg/min")),
    )
    # 0.1 mcg/kg/min x 80 kg x 60 min = 480 mcg
    assert order.total_amount == {"mg": pytest.approx(0.48)}


def test_bolus_dose_counts_once_inside_the_window():
    [order] = ledger_for(KCL, admin("a1", "kcl", t("08:00"), t("08:01"), dose=(20, "mEq")))
    assert order.total_amount == {"mEq": pytest.approx(20)}
    assert ledger_for(
        KCL, admin("a1", "kcl", t("08:00"), t("08:01"), dose=(20, "mEq")),
        window_start=datetime.fromisoformat(t("09:00")),
    ) == []


def test_truly_unknown_unit_is_a_warning():
    [order] = ledger_for(NOREPI, admin("a1", "norepi", t("09:00"), rate=(2, "puffs/h")))
    assert order.segments == []
    assert any("puffs/h" in w for w in order.warnings)


def test_missing_start_is_a_warning_not_a_crash():
    ma = admin("a1", "kcl", t("08:00"), rate=(50, "mL/h"))
    del ma["effectivePeriod"]["start"]
    [order] = ledger_for(KCL, ma)
    assert order.segments == []
    assert any("start" in w for w in order.warnings)
