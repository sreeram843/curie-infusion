"""Deterministic 'what not to infuse' rules. No LLM is involved in raising a flag."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from importlib import resources
from itertools import combinations
from pathlib import Path

from .fhir import BundleIndex, Code, codings, has_status, key, line_of, parse_time

DEFAULT_RULES = "infusion_rules.v0.1.json"
FINAL_OBS = {"final", "amended", "corrected"}
OPERATORS = {
    ">": lambda v, t: v > t,
    ">=": lambda v, t: v >= t,
    "<": lambda v, t: v < t,
    "<=": lambda v, t: v <= t,
}

# Flag statuses
DO_NOT_INFUSE = "do_not_infuse"
REVIEW = "review"
INSUFFICIENT = "insufficient_context"


@dataclass
class SafetyFlag:
    rule_id: str
    kind: str
    status: str
    targets: list[str]
    message: str
    citation: str
    evidence: list[dict] = field(default_factory=list)


def load_rules(path: str | Path | None = None) -> dict:
    if path:
        return json.loads(Path(path).read_text())
    return json.loads(resources.files("curie_infusion").joinpath("rulesets", DEFAULT_RULES).read_text())


def _codes(items: list[dict]) -> set[Code]:
    return {(c["system"], c["code"]) for c in items}


def _targets(idx: BundleIndex, as_of: datetime) -> list[dict]:
    """In-progress administrations at as_of, plus active orders not already running."""
    running, running_orders = [], set()
    for ma in idx.of("MedicationAdministration"):
        period = ma.get("effectivePeriod", {})
        start, end = parse_time(period.get("start")), parse_time(period.get("end"))
        if ma.get("status") == "in-progress" and start and start <= as_of and (end is None or end > as_of):
            running.append(ma)
            running_orders.add(ma.get("request", {}).get("reference"))
    pending = [
        mr
        for mr in idx.of("MedicationRequest")
        if mr.get("status") == "active" and key(mr) not in running_orders
    ]
    return running + pending


def _latest_observation(idx: BundleIndex, code: Code, as_of: datetime) -> dict | None:
    """Latest final result for `code` that was available (issued) by as_of."""
    best, best_time = None, None
    for obs in idx.of("Observation"):
        if obs.get("status") not in FINAL_OBS or code not in codings(obs.get("code")):
            continue
        effective = parse_time(obs.get("effectiveDateTime"))
        available = parse_time(obs.get("issued")) or effective
        if effective and available <= as_of and (best_time is None or effective > best_time):
            best, best_time = obs, effective
    return best


def _observation_flag(rule: dict, target: dict, idx: BundleIndex, as_of: datetime) -> SafetyFlag | None:
    code = (rule["observation_code"]["system"], rule["observation_code"]["code"])
    base = dict(rule_id=rule["id"], kind="physiologic", targets=[key(target)], citation=rule["citation"])
    obs = _latest_observation(idx, code, as_of)
    if obs is None:
        return SafetyFlag(status=INSUFFICIENT, message=f"No available {code[1]} result to evaluate.", **base)

    effective = parse_time(obs["effectiveDateTime"])
    qty = obs.get("valueQuantity", {})
    unit = qty.get("unit") or qty.get("code", "")
    evidence = [{"reference": key(obs), "detail": f"{qty.get('value')} {unit} at {obs['effectiveDateTime']}"}]
    if as_of - effective > timedelta(hours=rule["max_age_hours"]):
        return SafetyFlag(status=INSUFFICIENT, message=f"Latest {code[1]} is older than {rule['max_age_hours']} h.", evidence=evidence, **base)
    if unit.lower() not in {u.lower() for u in rule["units"]} or qty.get("value") is None:
        return SafetyFlag(status=INSUFFICIENT, message=f"Unit '{unit}' not comparable to rule threshold.", evidence=evidence, **base)
    if OPERATORS[rule["operator"]](float(qty["value"]), rule["threshold"]):
        return SafetyFlag(status=DO_NOT_INFUSE, message=rule["message"], evidence=evidence, **base)
    return None


def _condition_flag(rule: dict, target: dict, idx: BundleIndex) -> SafetyFlag | None:
    wanted = _codes(rule["condition_codes"])
    for cond in idx.of("Condition"):
        if has_status(cond.get("clinicalStatus"), "active", "recurrence", "relapse") and not has_status(
            cond.get("verificationStatus"), "refuted", "entered-in-error"
        ) and codings(cond.get("code")) & wanted:
            return SafetyFlag(
                rule_id=rule["id"], kind="physiologic", status=DO_NOT_INFUSE, targets=[key(target)],
                message=rule["message"], citation=rule["citation"],
                evidence=[{"reference": key(cond), "detail": "active condition"}],
            )
    return None


def _allergy_flags(target: dict, idx: BundleIndex) -> list[SafetyFlag]:
    drugs = idx.drug_codes(target)
    flags = []
    for al in idx.of("AllergyIntolerance"):
        if has_status(al.get("clinicalStatus"), "active") and codings(al.get("code")) & drugs:
            flags.append(
                SafetyFlag(
                    rule_id="allergy-match", kind="allergy", status=DO_NOT_INFUSE, targets=[key(target)],
                    message=f"Active allergy recorded for {idx.drug_label(target)}.",
                    citation=f"{key(al)} (patient record)",
                    evidence=[{"reference": key(al), "detail": f"criticality={al.get('criticality', 'unknown')}"}],
                )
            )
    return flags


def _coinfusion_flags(targets: list[dict], rules: list[dict], idx: BundleIndex) -> list[SafetyFlag]:
    flags = []
    for a, b in combinations(targets, 2):
        codes_a, codes_b = idx.drug_codes(a), idx.drug_codes(b)
        for rule in rules:
            ra, rb = _codes(rule["drugs_a"]), _codes(rule["drugs_b"])
            if not ((codes_a & ra and codes_b & rb) or (codes_a & rb and codes_b & ra)):
                continue
            status = DO_NOT_INFUSE
            if rule["scope"] == "same_line":
                line_a, line_b = line_of(a), line_of(b)
                if line_a and line_b and line_a != line_b:
                    continue
                if not (line_a and line_b):
                    status = REVIEW
            flags.append(
                SafetyFlag(
                    rule_id=rule["id"], kind="coinfusion", status=status, targets=[key(a), key(b)],
                    message=rule["message"], citation=rule["citation"],
                    evidence=[{"reference": key(r), "detail": f"line={line_of(r) or 'unknown'}"} for r in (a, b)],
                )
            )
    return flags


def evaluate_flags(idx: BundleIndex, rules: dict, as_of: datetime) -> list[SafetyFlag]:
    targets = _targets(idx, as_of)
    flags: list[SafetyFlag] = []
    for target in targets:
        drugs = idx.drug_codes(target)
        for rule in rules["physiologic"]:
            if not drugs & _codes(rule["drug_codes"]):
                continue
            if rule["kind"] == "observation_threshold":
                flag = _observation_flag(rule, target, idx, as_of)
            elif rule["kind"] == "condition_present":
                flag = _condition_flag(rule, target, idx)
            else:
                raise ValueError(f"unknown rule kind: {rule['kind']}")
            if flag:
                flags.append(flag)
        flags.extend(_allergy_flags(target, idx))
    flags.extend(_coinfusion_flags(targets, rules["coinfusion"], idx))
    return flags
