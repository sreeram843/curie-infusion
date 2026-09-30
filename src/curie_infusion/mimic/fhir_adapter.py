"""MIMIC-IV rows -> the FHIR R4 Bundle a pump/EHR feed would have produced by `as_of`.

The engine only ever sees FHIR, so a live pump gateway (HL7 v2 PCD-01 -> FHIR) and this replay
adapter are interchangeable inputs.
"""

from __future__ import annotations

from datetime import UTC, datetime

MIMIC_ITEM = "https://mimic.mit.edu/fhir/mimic/CodeSystem/mimic-d-items"
MIMIC_LAB = "https://mimic.mit.edu/fhir/mimic/CodeSystem/mimic-d-labitems"
SYNTHETIC_DRUG = "https://curie.local/fhir/CodeSystem/synthetic-drug"
LOINC = "http://loinc.org"

# inputevents itemid -> rule-table drug code. Labels verified against icu/d_items (MIMIC-IV 3.1).
DRUG_MAP = {
    225166: "potassium-chloride",  # Potassium Chloride
    225855: "ceftriaxone",  # Ceftriaxone
    221456: "calcium-gluconate",  # Calcium Gluconate
    227525: "calcium-gluconate",  # Calcium Gluconate (CRRT)
    228317: "calcium-gluconate",  # Calcium Gluconate (Bolus)_OLD_1
    229640: "calcium-gluconate",  # Calcium Gluconate (Bolus)
    229618: "calcium-chloride",  # Calcium Chloride
}
# labevents itemid -> LOINC, only where a rule needs it. 50971/52610 are "Potassium, Blood,
# Chemistry" (serum/plasma). Whole-blood potassium (50822) is deliberately not mapped to 2823-3.
LAB_LOINC = {50971: "2823-3", 52610: "2823-3"}
BODY_WEIGHT = "29463-7"


def _iso(ts: datetime | None) -> str | None:
    """MIMIC times are naive (and date-shifted); the engine requires an explicit offset."""
    if ts is None:
        return None
    return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).isoformat()


def _medication(itemid: int, label: str) -> dict:
    coding = [{"system": MIMIC_ITEM, "code": str(itemid), "display": label}]
    if itemid in DRUG_MAP:
        coding.append({"system": SYNTHETIC_DRUG, "code": DRUG_MAP[itemid]})
    return {"resourceType": "Medication", "id": f"mimic-{itemid}", "code": {"text": label, "coding": coding}}


def _administration(i: int, row: dict, as_of: datetime) -> dict:
    ended = row["endtime"] is not None and _iso(row["endtime"]) <= _iso(as_of)
    status = ("stopped" if row["statusdescription"] == "Stopped" else "completed") if ended else "in-progress"
    ma = {
        "resourceType": "MedicationAdministration",
        "id": f"{i}-{row['orderid']}-{row['itemid']}",
        "status": status,
        "subject": {"reference": f"Patient/{row['subject_id']}"},
        "medicationReference": {"reference": f"Medication/mimic-{row['itemid']}"},
        "request": {"reference": f"MedicationRequest/{row['linkorderid']}"},
        # Not yet ended at as_of: a live feed would not know the end time.
        "effectivePeriod": {"start": _iso(row["starttime"]), **({"end": _iso(row["endtime"])} if ended else {})},
    }
    if row["rate"] is not None and row["rateuom"]:
        ma["dosage"] = {"rateQuantity": {"value": row["rate"], "unit": row["rateuom"]}}
    elif row["amount"] is not None:
        ma["dosage"] = {"dose": {"value": row["amount"], "unit": row["amountuom"]}}
    return ma


def _lab(row: dict) -> dict:
    coding = [{"system": MIMIC_LAB, "code": str(row["itemid"]), "display": row["label"]}]
    if row["itemid"] in LAB_LOINC:
        coding.append({"system": LOINC, "code": LAB_LOINC[row["itemid"]]})
    obs = {
        "resourceType": "Observation",
        "id": f"lab-{row['itemid']}-{row['charttime']:%Y%m%d%H%M%S}",
        "status": "final",
        "code": {"text": row["label"], "coding": coding},
        "effectiveDateTime": _iso(row["charttime"]),
        "valueQuantity": {"value": row["valuenum"], "unit": row["valueuom"]},
    }
    if row.get("storetime"):
        obs["issued"] = _iso(row["storetime"])
    return obs


def build_bundle(stay: dict, infusions: list[dict], labs: list[dict], as_of: datetime) -> dict:
    """One stay's FHIR Bundle containing only what had happened (or been charted) by as_of."""
    cutoff = _iso(as_of)
    rows = [r for r in infusions if _iso(r["starttime"]) <= cutoff]
    resources: list[dict] = [{"resourceType": "Patient", "id": str(stay["subject_id"])}]
    resources += [_medication(item, label) for item, label in sorted({(r["itemid"], r["label"]) for r in rows})]

    administrations = [_administration(i, r, as_of) for i, r in enumerate(rows)]
    resources += administrations

    orders: dict[int, dict] = {}
    for row, ma in zip(rows, administrations, strict=True):
        order = orders.setdefault(row["linkorderid"], {
            "resourceType": "MedicationRequest",
            "id": str(row["linkorderid"]),
            "status": "completed",
            "intent": "order",
            "subject": {"reference": f"Patient/{row['subject_id']}"},
            "medicationReference": {"reference": f"Medication/mimic-{row['itemid']}"},
        })
        if ma["status"] == "in-progress":
            order["status"] = "active"
    resources += orders.values()

    weighed = [r for r in rows if r["patientweight"]]
    if weighed:
        last = weighed[-1]
        resources.append({
            "resourceType": "Observation",
            "id": "body-weight",
            "status": "final",
            "code": {"coding": [{"system": LOINC, "code": BODY_WEIGHT}], "text": "Body weight"},
            "effectiveDateTime": _iso(last["starttime"]),
            "valueQuantity": {"value": last["patientweight"], "unit": "kg"},
        })
    resources += [_lab(r) for r in labs if _iso(r["charttime"]) <= cutoff]
    return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in resources]}
