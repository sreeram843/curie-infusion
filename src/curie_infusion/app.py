"""Lightweight web app: infusion grid (hour/day/week) + current-hour safety panel for MIMIC-IV stays.

Replay: every endpoint takes `as_of`, the simulated pump clock. Nothing after it is shown or used.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from functools import cache, lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .billing.charges import ADMIN_CODES, admin_lines, drug_lines, load_crosswalk
from .billing.prices import load_asp, load_opps
from .fhir import BundleIndex
from .mimic.fhir_adapter import build_bundle
from .mimic.store import GRAINS, VITALS, MimicStore
from .rules import blocked_medications, evaluate_flags, load_rules, running_at
from .summary import summarize

STORE_DIR = Path(os.environ.get("CURIE_MIMIC_STORE", "data/mimic"))
STATIC = Path(__file__).parent / "static"
CMS_DIR = Path(os.environ.get("CURIE_CMS_DIR", "data/cms"))
LAB_LOOKBACK = timedelta(hours=48)

app = FastAPI(title="Curie Infusion")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


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


@app.get("/billing")
def billing_page() -> FileResponse:
    return FileResponse(STATIC / "billing.html")


def _price_files_key() -> tuple:
    """Changes when a price file is added or replaced, so new downloads are picked up live."""
    if not CMS_DIR.exists():
        return ()
    return tuple(sorted((str(p), p.stat().st_mtime) for p in CMS_DIR.rglob("*") if p.is_file()))


@lru_cache(maxsize=4)
def _prices(_key: tuple):
    return load_asp(CMS_DIR), load_opps(CMS_DIR, set(ADMIN_CODES))


@app.get("/api/stays/{stay_id}/billing")
def billing(stay_id: int) -> dict:
    """Estimated charges for the whole stay at CMS reference prices."""
    stay = _stay(stay_id)
    asp, opps = _prices(_price_files_key())
    crosswalk = load_crosswalk()
    lines = drug_lines(store().billing_rows(stay_id), crosswalk, asp) + admin_lines(store().orders(stay_id), opps)
    lines.sort(key=lambda x: (x["day"], x["kind"] != "drug", x["status"] != "priced", x["code"] or ""))

    days: dict = defaultdict(lambda: {"drugs": 0.0, "administration": 0.0})
    for line in lines:
        bucket = days[line["day"]]
        if line["charge"] is not None:
            bucket["drugs" if line["kind"] == "drug" else "administration"] += line["charge"]
    drugs = round(sum(d["drugs"] for d in days.values()), 2)
    admin = round(sum(d["administration"] for d in days.values()), 2)
    return {
        "stay": stay,
        "drg": store().drg(stay["hadm_id"]),
        "sources": {
            "asp": {"file": asp.source, "effective": asp.effective} if asp else None,
            "opps": {"file": opps.source} if opps else None,
            "crosswalk": crosswalk["schema_version"],
            "crosswalk_provenance": crosswalk["provenance"],
        },
        "totals": {
            "drugs": drugs, "administration": admin, "total": round(drugs + admin, 2),
            "unpriced_lines": sum(line["status"] == "unpriced" for line in lines),
            "units_only_lines": sum(line["status"] == "units_only" for line in lines),
            "packaged_lines": sum(line["status"] == "packaged" for line in lines),
        },
        "days": [
            {"day": day, "drugs": round(v["drugs"], 2), "administration": round(v["administration"], 2),
             "total": round(v["drugs"] + v["administration"], 2)}
            for day, v in sorted(days.items())
        ],
        "lines": lines,
    }


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
    vital_cells = store().vitals_grid(stay_id, grain, start, clock)
    vitals = {key: {"vital": key, "label": label, "unit": unit, "cells": {}} for key, (label, unit, _) in VITALS.items()}
    for c in vital_cells:
        vitals[c["vital"]]["cells"][c["bucket"].isoformat()] = {
            k: c[k] for k in ("median", "min", "max", "last", "n")
        }
    buckets = sorted({c["bucket"] for c in cells} | {c["bucket"] for c in vital_cells}
                     | ({hour_end - timedelta(hours=h) for h in range(1, 25)} if grain == "hour" else set()))
    return {
        "stay": stay, "grain": grain, "as_of": clock,
        "buckets": [b.isoformat() for b in buckets if b <= clock],
        "rows": list(rows.values()),
        "vitals": [v for v in vitals.values() if v["cells"]],
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


@app.get("/api/stays/{stay_id}/vitals/cell")
def vital_cell(stay_id: int, vital: str, bucket: datetime, grain: str = "hour", as_of: datetime | None = None) -> dict:
    """Drill-down for one vitals cell: every reading in the bucket up to the pump clock."""
    if grain not in GRAINS or vital not in VITALS:
        raise HTTPException(422, f"grain must be one of {sorted(GRAINS)}, vital one of {sorted(VITALS)}")
    clock = _as_of(_stay(stay_id), as_of)
    readings = store().vital_readings(stay_id, vital, grain, bucket.replace(tzinfo=None), clock)
    label, unit, _ = VITALS[vital]
    return {"vital": vital, "label": label, "unit": unit, "bucket": bucket.replace(tzinfo=None), "as_of": clock,
            "readings": [{**r, "storetime": r["storetime"] if r["storetime"] and r["storetime"] <= clock else None}
                         for r in readings]}


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
        "vitals": sorted(
            ({**v, "name": VITALS[v["vital"]][0], "unit": VITALS[v["vital"]][1]}
             for v in store().vitals_latest(stay_id, clock)),
            key=lambda v: list(VITALS).index(v["vital"]),
        ),
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
