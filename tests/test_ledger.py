from datetime import datetime

import pytest
from builders import admin, bundle, medication, request, t

from curie_infusion.fhir import BundleIndex
from curie_infusion.ledger import compute_ledger, reconcile

AS_OF = datetime.fromisoformat(t("10:00"))
KCL = medication("kcl", "potassium-chloride", strength=(20, "mEq", 100, "mL"))
NOREPI = medication("norepi", "norepinephrine")


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
