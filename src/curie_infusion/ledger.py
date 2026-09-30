"""Real-time dose ledger: rate x elapsed time, grouped by parent order."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .fhir import BundleIndex, key, parse_time

EXCLUDED_STATUSES = {"entered-in-error", "not-done", "unknown"}

# unit -> (canonical unit, factor to canonical). Includes MIMIC-IV spellings ("grams", "mEq.").
AMOUNT_UNITS = {
    "g": ("mg", 1000.0),
    "gram": ("mg", 1000.0),
    "grams": ("mg", 1000.0),
    "mg": ("mg", 1.0),
    "mcg": ("mg", 0.001),
    "ug": ("mg", 0.001),
    "µg": ("mg", 0.001),
    "ng": ("mg", 1e-6),
    "nanograms": ("mg", 1e-6),
    "meq": ("mEq", 1.0),
    "meq.": ("mEq", 1.0),
    "mmol": ("mmol", 1.0),
    "unit": ("unit", 1.0),
    "units": ("unit", 1.0),
    "u": ("unit", 1.0),
    "[iu]": ("unit", 1.0),
}
VOLUME_UNITS = {"ml": 1.0, "l": 1000.0, "ul": 0.001, "µl": 0.001}
HOURS_PER = {
    "h": 1.0, "hr": 1.0, "hour": 1.0, "hours": 1.0,
    "min": 1 / 60, "minute": 1 / 60, "s": 1 / 3600, "d": 24.0, "day": 24.0,
}
BODY_WEIGHT = ("http://loinc.org", "29463-7")


@dataclass
class Segment:
    administration: str
    start: datetime
    end: datetime
    rate: float | None
    rate_unit: str | None
    volume_ml: float | None
    amount: float | None
    amount_unit: str | None


@dataclass
class OrderLedger:
    order: str | None
    drug: str
    segments: list[Segment] = field(default_factory=list)
    total_volume_ml: float | None = None
    total_amount: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _rate(dosage: dict) -> tuple[float, str] | None:
    if q := dosage.get("rateQuantity"):
        return float(q["value"]), q.get("code") or q.get("unit", "")
    if r := dosage.get("rateRatio"):
        num, den = r["numerator"], r["denominator"]
        den_v = float(den.get("value", 1))
        return float(num["value"]) / den_v, f"{num.get('unit', '')}/{den.get('unit', '')}"
    return None


def _parse_rate_unit(unit: str) -> tuple[str, str, float, bool] | None:
    """'mL/h' -> ('volume', 'mL', per-hour factor, per_kg); 'mcg/kg/min' -> ('amount', 'mg', f, True)."""
    parts = unit.strip().lower().split("/")
    per_kg = len(parts) == 3 and parts[1] == "kg"
    if per_kg:
        parts = [parts[0], parts[2]]
    if len(parts) != 2 or parts[1] not in HOURS_PER:
        return None
    num, per = parts
    if num in VOLUME_UNITS:
        return "volume", "mL", VOLUME_UNITS[num] / HOURS_PER[per], per_kg
    if num in AMOUNT_UNITS:
        canon, factor = AMOUNT_UNITS[num]
        return "amount", canon, factor / HOURS_PER[per], per_kg
    return None


def _weight_kg(idx: BundleIndex, as_of: datetime) -> float | None:
    obs = idx.latest_observation(BODY_WEIGHT, as_of)
    qty = (obs or {}).get("valueQuantity", {})
    if qty.get("value") is None or (qty.get("unit") or qty.get("code", "")).lower() != "kg":
        return None
    return float(qty["value"])


def _bolus(dosage: dict) -> tuple[float | None, float | None, str | None] | None:
    """A `dosage.dose` with no rate -> (volume_ml, amount, amount_unit)."""
    dose = dosage.get("dose")
    if not dose or dosage.get("rateQuantity") or dosage.get("rateRatio"):
        return None
    unit = (dose.get("code") or dose.get("unit", "")).lower()
    if unit in VOLUME_UNITS:
        return float(dose["value"]) * VOLUME_UNITS[unit], None, None
    if unit in AMOUNT_UNITS:
        canon, factor = AMOUNT_UNITS[unit]
        return None, float(dose["value"]) * factor, canon
    return None


def _concentration(med: dict | None) -> tuple[float, str] | None:
    """Amount per mL from the first ingredient strength expressed as amount/volume."""
    for ing in (med or {}).get("ingredient", []):
        s = ing.get("strength")
        if not s:
            continue
        num, den = s["numerator"], s["denominator"]
        amount = AMOUNT_UNITS.get(num.get("unit", "").lower())
        volume = VOLUME_UNITS.get(den.get("unit", "").lower())
        if amount and volume:
            return float(num["value"]) * amount[1] / (float(den["value"]) * volume), amount[0]
    return None


def compute_ledger(
    idx: BundleIndex,
    as_of: datetime,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[OrderLedger]:
    """Cumulative volume and amount per parent order within [window_start, min(window_end, as_of)]."""
    horizon = min(window_end, as_of) if window_end else as_of
    orders: dict[str, OrderLedger] = {}
    weight = _weight_kg(idx, as_of)

    for ma in idx.of("MedicationAdministration"):
        if ma.get("status") in EXCLUDED_STATUSES:
            continue
        order_ref = ma.get("request", {}).get("reference")
        group = orders.setdefault(order_ref or key(ma), OrderLedger(order_ref, idx.drug_label(ma)))

        period = ma.get("effectivePeriod")
        start = parse_time((period or {}).get("start"))
        if start is None:
            group.warnings.append(f"{key(ma)}: no effectivePeriod.start; not an infusion segment")
            continue
        dosage = ma.get("dosage", {})

        if bolus := _bolus(dosage):
            if (window_start is None or start >= window_start) and start <= horizon:
                volume, amount, amount_unit = bolus
                group.segments.append(Segment(key(ma), start, start, None, None, volume, amount, amount_unit))
            continue

        end = parse_time(period.get("end"))
        if end is None:
            if ma.get("status") != "in-progress":
                group.warnings.append(f"{key(ma)}: open period but status {ma.get('status')}")
                continue
            end = as_of
        start, end = max(start, window_start) if window_start else start, min(end, horizon)
        if end <= start:
            continue

        rate = _rate(dosage)
        parsed = _parse_rate_unit(rate[1]) if rate else None
        if not parsed:
            group.warnings.append(f"{key(ma)}: unsupported or missing rate ({rate[1] if rate else 'none'})")
            continue
        kind, unit, per_hour, per_kg = parsed
        if per_kg:
            if weight is None:
                group.warnings.append(f"{key(ma)}: weight-based rate {rate[1]} but no body weight")
                continue
            per_hour *= weight
        hours = (end - start).total_seconds() / 3600
        volume = amount = amount_unit = None
        if kind == "volume":
            volume = rate[0] * per_hour * hours
            if conc := _concentration(idx.medication(ma)):
                amount, amount_unit = volume * conc[0], conc[1]
            else:
                group.warnings.append(f"{key(ma)}: volume rate but no ingredient concentration")
        else:
            amount, amount_unit = rate[0] * per_hour * hours, unit
        group.segments.append(Segment(key(ma), start, end, rate[0], rate[1], volume, amount, amount_unit))

    for group in orders.values():
        group.segments.sort(key=lambda s: s.start)
        volumes = [s.volume_ml for s in group.segments if s.volume_ml is not None]
        group.total_volume_ml = sum(volumes) if volumes else None
        for s in group.segments:
            if s.amount is not None:
                group.total_amount[s.amount_unit] = group.total_amount.get(s.amount_unit, 0.0) + s.amount
    return [g for g in orders.values() if g.segments or g.warnings]


def reconcile(idx: BundleIndex) -> list[dict]:
    """Orders with no administration, and administrations with no known order."""
    administered = set()
    findings = []
    for ma in idx.of("MedicationAdministration"):
        if ma.get("status") in EXCLUDED_STATUSES:
            continue
        ref = ma.get("request", {}).get("reference")
        if ref in idx.by_ref:
            administered.add(ref)
        else:
            findings.append({"kind": "administration_without_order", "reference": key(ma)})
    for mr in idx.of("MedicationRequest"):
        if mr.get("status") == "active" and key(mr) not in administered:
            findings.append({"kind": "order_without_administration", "reference": key(mr)})
    return findings
