"""Estimated charges from charted infusions: drug lines (HCPCS x CMS ASP) and administration lines.

Administration uses simplified, per-calendar-day facility rules for the IV infusion/push/hydration
code family (96360-96376). They approximate outpatient-style coding; inpatient stays are actually
paid per DRG. Code numbers only: CPT descriptors are AMA-licensed and are not reproduced.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from importlib import resources

from .prices import PriceFile, billing_units

CROSSWALK = "hcpcs_crosswalk.v0.1.json"

# MIMIC-IV inputevents.ordercategoryname
NOT_BILLED = {"13-Enteral Nutrition", "14-Oral/Gastric Intake", "15-Supplements", "16-Pre Admission/Non-ICU"}
THERAPEUTIC = {"01-Drips", "04-Fluids (Colloids)", "08-Antibiotics (IV)", "10-Prophylaxis (IV)"}
PUSH = {"05-Med Bolus"}
HYDRATION = {"02-Fluids (Crystalloids)", "03-IV Fluid Bolus"}
DRUG_CATEGORIES = {"Medications", "Antibiotics"}

ADMIN_CODES = {
    "96365": "IV infusion, initial (up to 1 h)",
    "96366": "IV infusion, each additional hour",
    "96367": "IV infusion, additional sequential drug",
    "96368": "IV infusion, concurrent",
    "96374": "IV push, initial",
    "96375": "IV push, each additional new drug",
    "96376": "IV push, same drug repeated",
    "96360": "IV hydration, initial (31 min to 1 h)",
    "96361": "IV hydration, each additional hour",
}


def load_crosswalk() -> dict:
    text = resources.files("curie_infusion.billing").joinpath(CROSSWALK).read_text()
    return json.loads(text)


def _extra_hours(minutes: float) -> int:
    """Additional-hour units: each full hour, plus one more if the remainder exceeds 30 min."""
    if minutes <= 0:
        return 0
    return int(minutes // 60) + (1 if minutes % 60 > 30 else 0)


def _merge(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[list[datetime]] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _minutes(intervals: list[tuple[datetime, datetime]]) -> float:
    return sum((e - s).total_seconds() for s, e in _merge(intervals)) / 60


def _minus(intervals, cover) -> list[tuple[datetime, datetime]]:
    """Parts of `intervals` not covered by `cover`."""
    out = []
    cover = _merge(cover)
    for s, e in _merge(intervals):
        cur = s
        for cs, ce in cover:
            if ce <= cur or cs >= e:
                continue
            if cs > cur:
                out.append((cur, cs))
            cur = max(cur, ce)
        if cur < e:
            out.append((cur, e))
    return out


def drug_lines(rows: list[dict], crosswalk: dict, asp: PriceFile | None) -> list[dict]:
    """One line per day x HCPCS code (or unmapped drug). Units are rounded up per order per day.

    `rows`: day, itemid, label, category, linkorderid, ordercategoryname, amountuom, amount
    (amount already split across days by overlap).
    """
    items = crosswalk["items"]
    per_order: dict[tuple, dict] = {}
    for r in rows:
        if r["ordercategoryname"] in NOT_BILLED or not r["amount"]:
            continue
        mapped = items.get(str(r["itemid"]))
        if not mapped and r["category"] not in DRUG_CATEGORIES:
            continue  # carrier fluids, flushes, etc. with no code
        code = mapped.get("hcpcs") if mapped else None
        key = (r["day"], code or f"item:{r['itemid']}", r["amountuom"], r["linkorderid"])
        o = per_order.setdefault(key, {"amount": 0.0, "labels": set(), "itemids": set()})
        o["amount"] += r["amount"]
        o["labels"].add(r["label"])
        o["itemids"].add(r["itemid"])

    lines: dict[tuple, dict] = {}
    for (day, code, unit, _order), o in per_order.items():
        line = lines.setdefault((day, code, unit), {
            "day": day, "kind": "drug", "code": None if code.startswith("item:") else code,
            "labels": set(), "itemids": set(), "amount": 0.0, "unit": unit, "units": 0,
            "unit_price": None, "charge": None, "status": "priced", "reason": None, "description": None,
        })
        line["labels"] |= o["labels"]
        line["itemids"] |= o["itemids"]
        line["amount"] += o["amount"]
        if line["code"] is None:
            note = next((items[str(i)].get("note") for i in o["itemids"] if str(i) in items), None)
            line.update(status="unpriced", reason=note or "no HCPCS crosswalk for this item")
            continue
        price = asp.codes.get(code) if asp else None
        if price is None:
            line.update(status="unpriced", reason="no CMS payment limit for this code in the ASP file")
            continue
        exact = billing_units(o["amount"], unit, price["dosage"])
        line["description"] = f"{price['description']} (per {price['dosage']})"
        if exact is None:
            reason = f"charted as '{unit}'; strength not recorded" if unit == "dose" else (
                f"'{unit}' cannot be converted to '{price['dosage']}'")
            line.update(status="unpriced", reason=reason)
            continue
        line["units"] += math.ceil(round(exact, 4))  # MIMIC amounts carry float32 noise
        line["unit_price"] = price["limit"]

    out = []
    for line in lines.values():
        if line["status"] == "priced":
            line["charge"] = round(line["units"] * line["unit_price"], 2)
        line["labels"] = sorted(line["labels"])
        line["itemids"] = sorted(line["itemids"])
        out.append(line)
    return sorted(out, key=lambda x: (x["day"], x["status"] != "priced", x["code"] or "", x["labels"]))


def classify_orders(orders: list[dict]) -> list[dict]:
    """Add `admin` = infusion | push | hydration | None to each order.

    `orders`: orderid, start, end, ordercategoryname, ordercategorydescription,
    items: [(itemid, label, category)].
    """
    for o in orders:
        cat, desc = o["ordercategoryname"], o["ordercategorydescription"]
        drugs = sorted({(i, lab) for i, lab, c in o["items"] if c in DRUG_CATEGORIES})
        o["drug"] = drugs[0] if drugs else None
        minutes = (o["end"] - o["start"]).total_seconds() / 60
        if cat in PUSH or desc == "Drug Push":
            o["admin"] = "push" if drugs or cat in PUSH else None
        elif cat in THERAPEUTIC or (cat in HYDRATION and drugs):
            # A fluid with a drug additive is a therapeutic infusion, not hydration.
            o["admin"] = "push" if minutes <= 15 else "infusion"
        elif cat in HYDRATION:
            o["admin"] = "hydration"
        else:
            o["admin"] = None
        if o["drug"] is None and o["admin"] in {"infusion", "push"}:
            o["drug"] = next(((i, lab) for i, lab, _ in o["items"]), None)
    return orders


def _days(start: datetime, end: datetime):
    d = datetime(start.year, start.month, start.day)
    while d < end:
        yield d
        d += timedelta(days=1)


def admin_lines(orders: list[dict], opps: PriceFile | None) -> list[dict]:
    """Per-day administration codes from classified orders."""
    by_day: dict[datetime, dict] = defaultdict(lambda: {"infusion": [], "push": [], "hydration": []})
    for o in classify_orders(orders):
        if not o["admin"]:
            continue
        if o["admin"] == "push":
            day = datetime(o["start"].year, o["start"].month, o["start"].day)
            by_day[day]["push"].append((o["start"], o["drug"]))
            continue
        for day in _days(o["start"], o["end"]):
            s, e = max(o["start"], day), min(o["end"], day + timedelta(days=1))
            if e > s:
                by_day[day][o["admin"]].append((s, e, o["drug"]))

    lines = []
    for day in sorted(by_day):
        d = by_day[day]
        counts: dict[str, int] = defaultdict(int)
        details: dict[str, str] = {}

        infusion = [(s, e) for s, e, _ in d["infusion"]]
        inf_minutes = _minutes(infusion)
        has_infusion = inf_minutes > 15
        if has_infusion:
            counts["96365"] = 1
            counts["96366"] = _extra_hours(inf_minutes - 60)
            details["96365"] = details["96366"] = f"{inf_minutes:.0f} min of drug infusion"
            per_drug: dict = defaultdict(list)
            for s, e, drug in d["infusion"]:
                per_drug[drug].append((s, e))
            first = min(per_drug, key=lambda k: min(per_drug[k]))
            for drug, ivs in per_drug.items():
                if drug == first:
                    continue
                others = [iv for k, v in per_drug.items() if k != drug for iv in v]
                if _minutes(_minus(ivs, others)) > 15:
                    counts["96367"] += 1
                else:
                    counts["96368"] = 1
            if counts["96367"]:
                details["96367"] = f"{counts['96367']} additional drug(s) infused on their own"
            if counts["96368"]:
                details["96368"] = "another drug ran at the same time"

        last_push: dict = {}
        for t, drug in sorted(d["push"], key=lambda p: p[0]):
            if drug in last_push:
                if (t - last_push[drug]).total_seconds() > 30 * 60:
                    counts["96376"] += 1
                    last_push[drug] = t
                continue
            if not has_infusion and not counts["96374"]:
                counts["96374"] = 1
            else:
                counts["96375"] += 1
            last_push[drug] = t
        if d["push"]:
            n = len(d["push"])
            for c in ("96374", "96375", "96376"):
                if counts[c]:
                    details[c] = f"{n} push(es) charted"

        hyd_minutes = _minutes(_minus([(s, e) for s, e, _ in d["hydration"]], infusion))
        if hyd_minutes > 30:
            if not has_infusion and not d["push"]:
                counts["96360"] = 1
                counts["96361"] = _extra_hours(hyd_minutes - 60)
            else:
                counts["96361"] = _extra_hours(hyd_minutes)
            details["96360"] = details["96361"] = f"{hyd_minutes:.0f} min of plain IV fluid"

        for code in ADMIN_CODES:
            if not counts.get(code):
                continue
            rate = (opps.codes.get(code) if opps else None) or {}
            line = {
                "day": day, "kind": "administration", "code": code, "labels": [ADMIN_CODES[code]],
                "itemids": [], "amount": None, "unit": None, "units": counts[code],
                "unit_price": rate.get("rate"), "charge": None, "status": "priced", "reason": None,
                "description": details.get(code),
            }
            if not opps:
                line.update(status="units_only", reason="add the OPPS Addendum B file to price administration")
            elif code not in opps.codes:
                line.update(status="unpriced", reason="code not found in Addendum B")
            elif rate.get("rate") is None:
                line.update(status="packaged", reason=f"no separate OPPS payment (status {rate.get('si') or '?'})")
            else:
                line["charge"] = round(counts[code] * rate["rate"], 2)
            lines.append(line)
    return lines
