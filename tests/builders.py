"""Tiny builders for synthetic FHIR R4 resources used in tests."""

DRUG = "https://curie.local/fhir/CodeSystem/synthetic-drug"
COND = "https://curie.local/fhir/CodeSystem/synthetic-condition"
LOINC = "http://loinc.org"
LINE_EXT = "https://curie.local/fhir/StructureDefinition/infusion-line"


def t(hhmm: str) -> str:
    return f"2026-01-01T{hhmm}:00+00:00"


def concept(system: str, code: str) -> dict:
    return {"coding": [{"system": system, "code": code}]}


def medication(id: str, code: str, strength: tuple | None = None) -> dict:
    med = {"resourceType": "Medication", "id": id, "code": concept(DRUG, code)}
    if strength:
        num_v, num_u, den_v, den_u = strength
        med["ingredient"] = [
            {
                "itemCodeableConcept": concept(DRUG, code),
                "strength": {
                    "numerator": {"value": num_v, "unit": num_u},
                    "denominator": {"value": den_v, "unit": den_u},
                },
            }
        ]
    return med


def request(id: str, med_id: str, status: str = "active") -> dict:
    return {
        "resourceType": "MedicationRequest",
        "id": id,
        "status": status,
        "intent": "order",
        "medicationReference": {"reference": f"Medication/{med_id}"},
    }


def admin(
    id: str,
    med_id: str,
    start: str,
    end: str | None = None,
    rate: tuple | None = None,
    status: str | None = None,
    request_id: str | None = None,
    line: str | None = None,
    dose: tuple | None = None,
) -> dict:
    ma = {
        "resourceType": "MedicationAdministration",
        "id": id,
        "status": status or ("completed" if end else "in-progress"),
        "medicationReference": {"reference": f"Medication/{med_id}"},
        "effectivePeriod": {"start": start, **({"end": end} if end else {})},
    }
    if rate:
        ma["dosage"] = {"rateQuantity": {"value": rate[0], "unit": rate[1]}}
    if dose:
        ma["dosage"] = {"dose": {"value": dose[0], "unit": dose[1]}}
    if request_id:
        ma["request"] = {"reference": f"MedicationRequest/{request_id}"}
    if line:
        ma["extension"] = [{"url": LINE_EXT, "valueString": line}]
    return ma


def observation(
    id: str,
    code: str,
    value: float,
    unit: str,
    effective: str,
    issued: str | None = None,
    status: str = "final",
) -> dict:
    obs = {
        "resourceType": "Observation",
        "id": id,
        "status": status,
        "code": concept(LOINC, code),
        "effectiveDateTime": effective,
        "valueQuantity": {"value": value, "unit": unit},
    }
    if issued:
        obs["issued"] = issued
    return obs


def condition(id: str, code: str, status: str = "active") -> dict:
    return {
        "resourceType": "Condition",
        "id": id,
        "clinicalStatus": concept(
            "http://terminology.hl7.org/CodeSystem/condition-clinical", status
        ),
        "code": concept(COND, code),
    }


def allergy(id: str, drug_code: str, criticality: str = "high") -> dict:
    return {
        "resourceType": "AllergyIntolerance",
        "id": id,
        "clinicalStatus": concept(
            "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "active"
        ),
        "criticality": criticality,
        "code": concept(DRUG, drug_code),
    }


def bundle(*resources: dict) -> dict:
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": r} for r in resources],
    }
