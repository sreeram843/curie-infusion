"""Small readers for the FHIR R4 JSON shapes this engine consumes."""

from __future__ import annotations

from datetime import datetime

# FHIR R4 has no standard lumen/line element on MedicationAdministration.
LINE_EXT = "https://curie.local/fhir/StructureDefinition/infusion-line"

Code = tuple[str, str]


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {value}")
    return dt


def codings(concept: dict | None) -> set[Code]:
    if not concept:
        return set()
    return {(c.get("system", ""), c["code"]) for c in concept.get("coding", []) if c.get("code")}


def has_status(concept: dict | None, *statuses: str) -> bool:
    return any(code in statuses for _, code in codings(concept))


def key(resource: dict) -> str:
    return f"{resource['resourceType']}/{resource['id']}"


def line_of(resource: dict) -> str | None:
    for ext in resource.get("extension", []):
        if ext.get("url") == LINE_EXT:
            return ext.get("valueString")
    return None


class BundleIndex:
    """Resources of one patient's Bundle, addressable by type and by reference."""

    def __init__(self, bundle: dict):
        self.by_ref: dict[str, dict] = {}
        self.by_type: dict[str, list[dict]] = {}
        for entry in bundle.get("entry", []):
            res = entry["resource"]
            self.by_ref[key(res)] = res
            self.by_type.setdefault(res["resourceType"], []).append(res)

    def of(self, resource_type: str) -> list[dict]:
        return self.by_type.get(resource_type, [])

    def medication(self, resource: dict) -> dict | None:
        ref = resource.get("medicationReference", {}).get("reference")
        return self.by_ref.get(ref) if ref else None

    def drug_codes(self, resource: dict) -> set[Code]:
        """Product and ingredient codes for a MedicationRequest/MedicationAdministration."""
        codes = codings(resource.get("medicationCodeableConcept"))
        med = self.medication(resource)
        if med:
            codes |= codings(med.get("code"))
            for ing in med.get("ingredient", []):
                codes |= codings(ing.get("itemCodeableConcept"))
        return codes

    def drug_label(self, resource: dict) -> str:
        med = self.medication(resource)
        concept = (med or {}).get("code") or resource.get("medicationCodeableConcept") or {}
        if concept.get("text"):
            return concept["text"]
        return next((c["code"] for c in concept.get("coding", []) if c.get("code")), "unknown")
