"""Lightweight web app: infusion grid (hour/day/week) + current-hour safety panel for MIMIC-IV stays.

Replay: every endpoint takes `as_of`, the simulated pump clock. Nothing after it is shown or used.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from .fhir import BundleIndex
from .mimic.fhir_adapter import build_bundle
from .mimic.store import GRAINS, MimicStore
from .rules import blocked_medications, evaluate_flags, load_rules, running_at
from .summary import summarize

STORE_DIR = Path(os.environ.get("CURIE_MIMIC_STORE", "data/mimic"))
STATIC = Path(__file__).parent / "static"
LAB_LOOKBACK = timedelta(hours=48)

app = FastAPI(title="Curie Infusion")


@cache
def store() -> MimicStore:
    return MimicStore(STORE_DIR)


@cache
def rules() -> dict:
    return load_rules()


def _stay(stay_id: int) -> dict:
    stay = store().stay(stay_id)
    if not stay:
        raise HTTPException(404, f"stay {stay_id} not found")
    return stay


def _as_of(stay: dict, as_of: datetime | None) -> datetime:
    """Default replay clock: 12 h into the stay (or discharge, if sooner). Stored times are naive."""
    if as_of:
        return as_of.replace(tzinfo=None)
    return min(stay["outtime"], stay["intime"] + timedelta(hours=12))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/stays")
def stays(q: str | None = None, limit: int = Query(50, le=200)) -> list[dict]:
    return store().stays(q, limit)


@app.get("/api/stays/{stay_id}")
def stay(stay_id: int) -> dict:
    return _stay(stay_id)


@app.get("/api/stays/{stay_id}/grid")
def grid(stay_id: int, grain: str = "hour", as_of: datetime | None = None) -> dict:
    """Hour: the 24 h ending at the as_of hour. Day/week: the whole stay up to as_of."""
    if grain not in GRAINS:
        raise HTTPException(422, f"grain must be one of {sorted(GRAINS)}")
    stay = _stay(stay_id)
    clock = _as_of(stay, as_of)
    hour_end = clock.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    start = hour_end - timedelta(hours=24) if grain == "hour" else stay["intime"]
    cells = store().grid(stay_id, grain, start, clock)

    rows: dict[tuple, dict] = {}
    for c in cells:
        row = rows.setdefault((c["category"], c["label"], c["unit"], c["itemid"]), {
            "itemid": c["itemid"], "label": c["label"], "category": c["category"],
            "unit": c["unit"], "continuous": False, "cells": {},
        })
        row["continuous"] |= bool(c["continuous"])
        row["cells"][c["bucket"].isoformat()] = {
            "amount": c["amount"], "max_rate": c["max_rate"], "rate_unit": c["rate_unit"],
        }
    buckets = sorted({c["bucket"] for c in cells} | ({hour_end - timedelta(hours=h) for h in range(1, 25)} if grain == "hour" else set()))
    return {
        "stay": stay, "grain": grain, "as_of": clock,
        "buckets": [b.isoformat() for b in buckets if b <= clock],
        "rows": list(rows.values()),
    }


def _squash(text: str | None) -> str | None:
    """MIMIC pads some descriptions with runs of spaces."""
    return " ".join(text.split()) if text else text


def _event_view(r: dict, clock: datetime) -> dict:
    """One charted event as known at the pump clock. A running event hides its end, final total,
    end status and bag totals: a live feed would not know them yet."""
    running = r["endtime"] > clock
    amount = r["amount"]
    if running:
        full = max((r["endtime"] - r["starttime"]).total_seconds(), 60)
        amount = amount * (clock - r["starttime"]).total_seconds() / full
    return {
        "start": r["starttime"], "end": None if running else r["endtime"], "running": running,
        "charted_at": r["storetime"] if r["storetime"] and r["storetime"] <= clock else None,
        "amount": amount, "unit": r["amountuom"], "in_bucket": r["in_bucket"],
        "rate": r["rate"], "rate_unit": r["rateuom"], "kind": r["ordercategorydescription"],
        "component": _squash(r["ordercomponenttypedescription"]),
        "status": "Running" if running else r["statusdescription"],
        "order": r["linkorderid"], "weight_kg": r["patientweight"],
        "bag": [{**b, "amount": None} for b in r["bag"]] if running else r["bag"],
    }


@app.get("/api/stays/{stay_id}/cell")
def cell(stay_id: int, itemid: int, bucket: datetime, grain: str = "hour", as_of: datetime | None = None) -> dict:
    """Drill-down for one grid cell: the charted events behind it and each one's share."""
    if grain not in GRAINS:
        raise HTTPException(422, f"grain must be one of {sorted(GRAINS)}")
    stay = _stay(stay_id)
    clock = _as_of(stay, as_of)
    start = bucket.replace(tzinfo=None)
    rows = store().cell_events(stay_id, itemid, grain, start, clock)
    item = store().item(stay_id, itemid)
    events = [_event_view(r, clock) for r in rows]
    return {
        "stay_id": stay_id, "itemid": itemid, "grain": grain, "as_of": clock,
        "label": item["label"] if item else str(itemid), "category": item["category"] if item else None,
        "unit": rows[0]["amountuom"] if rows else None,
        "bucket": start, "bucket_end": rows[0]["bucket_end"] if rows else None,
        "total": sum(e["in_bucket"] for e in events),
        "events": events,
    }


def _flag_view(flag, idx: BundleIndex, hypothetical: bool) -> dict:
    target = flag.targets[0]
    ref = idx.by_ref.get(target)
    drug = idx.drug_label(ref) if ref else target.split("/", 1)[1]
    return {**asdict(flag), "drug": drug, "hypothetical": hypothetical}


def _safety(stay_id: int, as_of: datetime | None) -> dict:
    stay = _stay(stay_id)
    clock = _as_of(stay, as_of)
    infusions = store().infusions(stay_id, until=clock, since=clock - timedelta(hours=1))
    labs = store().labs(stay["subject_id"], clock - LAB_LOOKBACK, clock)
    idx = BundleIndex(build_bundle(stay, infusions, labs, clock))
    aware_dt = clock.replace(tzinfo=UTC)  # MIMIC times are naive; the engine needs an offset

    running = [
        {"drug": idx.drug_label(ma), "rate": ma.get("dosage", {}).get("rateQuantity"),
         "since": ma["effectivePeriod"]["start"]}
        for ma in idx.of("MedicationAdministration") if running_at(ma, aware_dt)
    ]
    latest: dict[str, dict] = {}
    for lab in labs:
        if lab["storetime"] and lab["storetime"] <= clock:  # only results available at as_of
            latest[lab["label"]] = lab
    return {
        "stay": stay, "as_of": clock,
        "running": running,
        "flags": [_flag_view(f, idx, False) for f in evaluate_flags(idx, rules(), aware_dt)],
        "blocked": [_flag_view(f, idx, True) for f in blocked_medications(idx, rules(), aware_dt)],
        "labs": list(latest.values()),
        "rules_version": rules()["schema_version"],
        "rules_provenance": rules()["provenance"],
    }


@app.get("/api/stays/{stay_id}/safety")
def safety(stay_id: int, as_of: datetime | None = None) -> dict:
    return _safety(stay_id, as_of)


@app.get("/api/stays/{stay_id}/summary")
def summary(stay_id: int, as_of: datetime | None = None) -> dict:
    state = _safety(stay_id, as_of)
    flags = state["flags"] + state["blocked"]
    return {"as_of": state["as_of"], "flag_count": len(flags), **summarize(flags)}


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)
