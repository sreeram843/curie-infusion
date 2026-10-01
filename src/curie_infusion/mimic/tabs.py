"""Queries behind the data tabs. Every function takes the pump clock and returns only what was
known by then: results by their stored/verified time, running things without their end.

Two response shapes:
- series (grid tabs): {"buckets": [...], "groups": [{"name", "rows": [{"key", "label", "unit",
  "cells": {bucket_iso: {"v", "text", "tip", "flag"}}, "total"}]}]}
- table (list tabs): {"columns": [{"key", "label"}], "rows": [...], "note"}
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .extras import ASSESSMENTS
from .store import GRAINS, MimicStore

# mimic-code urine output convention: irrigant instilled is recorded in outputevents and subtracted.
IRRIGANT_IN = 227488


def floor(ts: datetime, grain: str) -> datetime:
    """Same alignment as DuckDB time_bucket (weeks start Monday)."""
    ts = ts.replace(minute=0, second=0, microsecond=0)
    if grain == "hour":
        return ts
    ts = ts.replace(hour=0)
    return ts - timedelta(days=ts.weekday()) if grain == "week" else ts


def window(stay: dict, grain: str, clock: datetime) -> tuple[datetime, list[datetime]]:
    """Hour: the 24 h ending at the clock hour. Day/week: the stay up to the clock."""
    step = {"hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1)}[grain]
    last = floor(clock, grain)
    first = last - timedelta(hours=23) if grain == "hour" else floor(stay["intime"], grain)
    out, b = [], first
    while b <= last:
        out.append(b)
        b += step
    return first, out


def _series(buckets: list[datetime], groups: dict[str, dict], extra: dict | None = None) -> dict:
    return {
        "buckets": [b.isoformat() for b in buckets],
        "groups": [{"name": name, "rows": list(rows.values())} for name, rows in groups.items() if rows],
        **(extra or {}),
    }


def _row(groups, group, key, label, unit):
    return groups.setdefault(group, {}).setdefault(key, {"key": key, "label": label, "unit": unit, "cells": {}, "total": None})


# ---------- series tabs ----------

def fluids(st: MimicStore, stay: dict, grain: str, clock: datetime) -> dict:
    start, buckets = window(stay, grain, clock)
    step = GRAINS[grain]
    params = {"stay": stay["stay_id"], "start": start, "end": clock}
    intake = st._rows(f"""
        WITH ev AS (
            SELECT ordercategoryname AS route, amount, starttime,
                   greatest(endtime, starttime + INTERVAL 1 minute) AS eff_end
            FROM inputevents WHERE stay_id = $stay AND amountuom = 'mL' AND amount > 0
              AND starttime < $end AND endtime >= $start
        ), b AS (
            SELECT t.b AS bstart, t.b + INTERVAL {step} AS bend
            FROM generate_series(time_bucket(INTERVAL {step}, $start::TIMESTAMP), $end::TIMESTAMP, INTERVAL {step}) t(b)
        )
        SELECT route, bstart AS bucket,
               sum(amount * epoch(least(eff_end, bend, $end::TIMESTAMP) - greatest(starttime, bstart))
                   / epoch(eff_end - starttime)) AS ml
        FROM ev JOIN b ON starttime < bend AND eff_end > bstart
        GROUP BY ALL HAVING ml > 0""", params)
    output = st._rows(f"""
        SELECT label, time_bucket(INTERVAL {step}, charttime) AS bucket,
               sum(CASE WHEN itemid = {IRRIGANT_IN} THEN -value ELSE value END) AS ml, count(*) AS n
        FROM outputs WHERE stay_id = $stay AND valueuom = 'mL'
          AND charttime >= time_bucket(INTERVAL {step}, $start::TIMESTAMP) AND charttime <= $end
          AND coalesce(storetime, charttime) <= $end
        GROUP BY ALL""", params)

    groups: dict = {}
    net: dict[str, float] = {}
    for r in intake:
        b = r["bucket"].isoformat()
        row = _row(groups, "Intake (mL)", r["route"], r["route"].split("-", 1)[-1], "mL")
        row["cells"][b] = {"v": r["ml"], "tip": f"{r['ml']:.0f} mL in"}
        net[b] = net.get(b, 0) + r["ml"]
    for r in output:
        b = r["bucket"].isoformat()
        row = _row(groups, "Output (mL)", r["label"], r["label"], "mL")
        row["cells"][b] = {"v": r["ml"], "tip": f"{r['ml']:.0f} mL out ({r['n']} charted)"}
        net[b] = net.get(b, 0) - r["ml"]
    for rows in groups.values():
        for row in rows.values():
            row["total"] = sum(c["v"] for c in row["cells"].values())
    if net:
        bal = _row(groups, "Balance", "net", "Net balance (in − out)", "mL")
        running = 0.0
        for b in [x.isoformat() for x in buckets]:
            if b in net:
                running += net[b]
                bal["cells"][b] = {"v": net[b], "tip": f"net {net[b]:+.0f} mL · cumulative {running:+.0f} mL",
                                   "flag": net[b] < 0}
        bal["total"] = sum(net.values())
    return _series(buckets, groups)


def labs(st: MimicStore, stay: dict, grain: str, clock: datetime) -> dict:
    start, buckets = window(stay, grain, clock)
    rows = st._rows(f"""
        SELECT itemid, label, fluid, category, time_bucket(INTERVAL {GRAINS[grain]}, charttime) AS bucket,
               arg_max(value, charttime) AS value, arg_max(valuenum, charttime) AS valuenum,
               any_value(valueuom) AS unit, bool_or(flag = 'abnormal') AS abnormal, count(*) AS n,
               any_value(ref_range_lower) AS lo, any_value(ref_range_upper) AS hi
        FROM labs_all
        WHERE hadm_id = $hadm AND charttime >= time_bucket(INTERVAL {GRAINS[grain]}, $start::TIMESTAMP)
          AND charttime <= $end AND coalesce(storetime, charttime) <= $end
        GROUP BY ALL ORDER BY category, label""",
        {"hadm": stay["hadm_id"], "start": start, "end": clock})
    groups: dict = {}
    for r in rows:
        name = r["label"] if r["fluid"] in (None, "Blood") else f"{r['label']} ({r['fluid']})"
        row = _row(groups, r["category"] or "Other", r["itemid"], name, r["unit"] or "")
        ref = f" · ref {r['lo']:g}–{r['hi']:g}" if r["lo"] is not None and r["hi"] is not None else ""
        row["cells"][r["bucket"].isoformat()] = {
            "v": r["valuenum"], "text": None if r["valuenum"] is not None else (r["value"] or "")[:12],
            "flag": bool(r["abnormal"]), "tip": f"{r['value']} {r['unit'] or ''}{ref} · {r['n']} result(s)",
        }
    return _series(buckets, groups, {"note": "Last result per period, shown once it was resulted. Red = flagged abnormal by the lab."})


def assessments(st: MimicStore, stay: dict, grain: str, clock: datetime) -> dict:
    start, buckets = window(stay, grain, clock)
    rows = st._rows(f"""
        SELECT key, time_bucket(INTERVAL {GRAINS[grain]}, charttime) AS bucket, median(valuenum) AS med,
               arg_max(value, charttime) AS last, count(*) AS n, min(valuenum) AS lo, max(valuenum) AS hi
        FROM assessments
        WHERE stay_id = $stay AND charttime >= time_bucket(INTERVAL {GRAINS[grain]}, $start::TIMESTAMP)
          AND charttime <= $end AND coalesce(storetime, charttime) <= $end
        GROUP BY ALL""", {"stay": stay["stay_id"], "start": start, "end": clock})
    groups: dict = {}
    order = list(ASSESSMENTS)
    for r in sorted(rows, key=lambda r: order.index(r["key"])):
        label, unit, _ = ASSESSMENTS[r["key"]]
        group = "Sedation & pain" if r["key"] in {"rass", "rass_goal", "pain"} else (
            "Neuro (GCS)" if r["key"].startswith("gcs") else ("Ventilator" if r["key"] in {"vent_mode", "fio2", "peep", "vt_obs", "vt_set"} else "Other"))
        row = _row(groups, group, r["key"], label, unit)
        text = r["last"] if r["med"] is None or r["key"] in {"vent_mode", "pain"} else None
        rng = f" · range {r['lo']:g}–{r['hi']:g}" if r["lo"] is not None else ""
        row["cells"][r["bucket"].isoformat()] = {
            "v": None if text else r["med"], "text": (text or "")[:14] or None,
            "tip": f"last: {r['last']}{rng} · {r['n']} charted",
        }
    return _series(buckets, groups, {"note": "Numeric scores show the median; text items show the last value charted."})


def nutrition(st: MimicStore, stay: dict, grain: str, clock: datetime) -> dict:
    start, buckets = window(stay, grain, clock)
    step = GRAINS[grain]
    rows = st._rows(f"""
        WITH ev AS (
            SELECT label, amountuom AS unit, amount, starttime, greatest(endtime, starttime + INTERVAL 1 minute) AS eff_end
            FROM ingredients WHERE stay_id = $stay AND starttime < $end AND endtime >= $start
        ), b AS (
            SELECT t.b AS bstart, t.b + INTERVAL {step} AS bend
            FROM generate_series(time_bucket(INTERVAL {step}, $start::TIMESTAMP), $end::TIMESTAMP, INTERVAL {step}) t(b)
        )
        SELECT label, unit, bstart AS bucket,
               sum(amount * epoch(least(eff_end, bend, $end::TIMESTAMP) - greatest(starttime, bstart))
                   / epoch(eff_end - starttime)) AS amount
        FROM ev JOIN b ON starttime < bend AND eff_end > bstart
        GROUP BY ALL HAVING amount > 0 ORDER BY label""",
        {"stay": stay["stay_id"], "start": start, "end": clock})
    groups: dict = {}
    for r in rows:
        row = _row(groups, "Delivered by infusions and feeds", (r["label"], r["unit"]), r["label"], r["unit"])
        row["key"] = f"{r['label']}|{r['unit']}"
        row["cells"][r["bucket"].isoformat()] = {"v": r["amount"], "tip": f"{r['amount']:.1f} {r['unit']}"}
    for rows_ in groups.values():
        for row in rows_.values():
            row["total"] = sum(c["v"] for c in row["cells"].values())
    return _series(buckets, groups, {"note": "Ingredients of everything infused or fed, including propofol's lipid calories."})


SERIES = {"fluids": fluids, "labs": labs, "assessments": assessments, "nutrition": nutrition}


def series_cell(st: MimicStore, stay: dict, tab: str, key: str, grain: str, bucket: datetime, clock: datetime) -> dict:
    """The raw rows behind one grid cell of a series tab."""
    end = min(bucket + {"hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1)}[grain], clock)
    p = {"start": bucket, "end": end, "clock": clock}
    if tab == "labs":
        rows = st._rows("""
            SELECT charttime AS time, storetime AS resulted, value, valueuom AS unit, flag, ref_range_lower AS ref_low,
                   ref_range_upper AS ref_high, priority
            FROM labs_all WHERE hadm_id = $hadm AND itemid = $key AND charttime >= $start AND charttime < $end
              AND coalesce(storetime, charttime) <= $clock ORDER BY charttime""", {**p, "hadm": stay["hadm_id"], "key": int(key)})
    elif tab == "assessments":
        rows = st._rows("""
            SELECT charttime AS time, storetime AS charted, value, valueuom AS unit FROM assessments
            WHERE stay_id = $stay AND key = $key AND charttime >= $start AND charttime < $end
              AND coalesce(storetime, charttime) <= $clock ORDER BY charttime""", {**p, "stay": stay["stay_id"], "key": key})
    elif tab == "fluids":
        rows = st._rows("""
            SELECT charttime AS time, label AS item, CASE WHEN itemid = 227488 THEN -value ELSE value END AS ml,
                   storetime AS charted FROM outputs
            WHERE stay_id = $stay AND label = $key AND charttime >= $start AND charttime < $end
              AND coalesce(storetime, charttime) <= $clock
            UNION ALL
            SELECT starttime, label, amount, storetime FROM inputevents
            WHERE stay_id = $stay AND ordercategoryname = $key AND amountuom = 'mL'
              AND starttime < $end AND greatest(endtime, starttime + INTERVAL 1 minute) > $start
            ORDER BY 1""", {**p, "stay": stay["stay_id"], "key": key})
    elif tab == "nutrition":
        label, unit = key.split("|", 1)
        rows = st._rows("""
            SELECT starttime AS start, CASE WHEN endtime > $clock THEN NULL ELSE endtime END AS "end",
                   amount, amountuom AS unit, statusdescription AS status FROM ingredients
            WHERE stay_id = $stay AND label = $label AND amountuom = $unit
              AND starttime < $end AND greatest(endtime, starttime + INTERVAL 1 minute) > $start
            ORDER BY starttime""", {**p, "stay": stay["stay_id"], "label": label, "unit": unit})
    else:
        raise KeyError(tab)
    return {"columns": [{"key": k, "label": k.replace("_", " ")} for k in (rows[0] if rows else {})], "rows": rows}


# ---------- table tabs ----------

def _cols(*pairs):
    return [{"key": k, "label": label} for k, label in pairs]


def orders(st: MimicStore, stay: dict, clock: datetime, hindsight: bool = False) -> dict:
    rows = st._rows("""
        WITH given AS (
            SELECT pharmacy_id,
                   count(*) FILTER (WHERE event_txt ILIKE 'Administered%' OR event_txt ILIKE '%Started%'
                                    OR event_txt ILIKE 'Restarted%' OR event_txt ILIKE 'Bolus%') AS given,
                   count(*) FILTER (WHERE event_txt ILIKE 'Not Given%' OR event_txt ILIKE '%Held%') AS not_given
            FROM emar WHERE hadm_id = $hadm AND charttime <= $clock AND coalesce(storetime, charttime) <= $clock
            GROUP BY pharmacy_id
        )
        SELECT o.starttime AS start, CASE WHEN o.stoptime <= $clock THEN o.stoptime END AS stop,
               coalesce(o.drug, o.medication) AS medication, o.dose, o.route, o.frequency,
               CASE WHEN o.starttime > $clock THEN 'scheduled'
                    WHEN o.stoptime IS NULL OR o.stoptime > $clock THEN 'active' ELSE 'stopped' END AS status_now,
               CASE WHEN o.stoptime <= $clock THEN o.status END AS final_status,
               g.given, g.not_given, o.infusion_type, o.sliding_scale, o.proc_type, o.entertime AS entered,
               o.verifiedtime AS verified, o.pharmacy_id
        FROM orders o LEFT JOIN given g USING (pharmacy_id)
        WHERE o.hadm_id = $hadm AND coalesce(o.entertime, o.starttime) <= $clock
        ORDER BY (status_now = 'active') DESC, o.starttime DESC""", {"hadm": stay["hadm_id"], "clock": clock})
    return {"columns": _cols(("status_now", "Status"), ("medication", "Medication"), ("dose", "Dose"),
                             ("route", "Route"), ("frequency", "Frequency"), ("start", "Start"), ("stop", "Stop"),
                             ("given", "Given"), ("not_given", "Not given")),
            "rows": rows, "note": "Pharmacy orders for the whole admission entered by the clock; given / not given come from eMAR scans."}


def emar(st: MimicStore, stay: dict, clock: datetime, hindsight: bool = False) -> dict:
    rows = st._rows("""
        SELECT charttime AS time, medication, event_txt AS event, dose_given, dose_due, route, infusion_rate,
               scheduletime AS scheduled, administration_type, products, site, no_barcode_reason,
               complete_dose_not_given, storetime AS charted, emar_id, pharmacy_id
        FROM emar WHERE hadm_id = $hadm AND charttime <= $clock AND coalesce(storetime, charttime) <= $clock
        ORDER BY charttime DESC""", {"hadm": stay["hadm_id"], "clock": clock})
    return {"columns": _cols(("time", "Time"), ("medication", "Medication"), ("event", "Event"), ("dose_given", "Dose given"),
                             ("route", "Route"), ("infusion_rate", "Rate"), ("scheduled", "Scheduled")),
            "rows": rows, "note": "Barcode-scanned medication administrations across the whole admission (ward and ICU)."}


def micro(st: MimicStore, stay: dict, clock: datetime, hindsight: bool = False) -> dict:
    rows = st._rows("""
        WITH r AS (
            SELECT * FROM micro WHERE hadm_id = $hadm AND charttime <= $clock
        )
        SELECT min(charttime) AS collected, spec_type_desc AS specimen, test_name AS test,
               CASE WHEN max(storetime) FILTER (WHERE storetime <= $clock) IS NULL THEN 'pending' ELSE 'resulted' END AS status,
               string_agg(DISTINCT org_name, ', ') FILTER (WHERE storetime <= $clock) AS organisms,
               string_agg(DISTINCT ab_name, ', ') FILTER (WHERE interpretation = 'R' AND storetime <= $clock) AS resistant,
               string_agg(DISTINCT ab_name, ', ') FILTER (WHERE interpretation = 'S' AND storetime <= $clock) AS susceptible,
               string_agg(DISTINCT ab_name, ', ') FILTER (WHERE interpretation = 'I' AND storetime <= $clock) AS intermediate,
               max(storetime) FILTER (WHERE storetime <= $clock) AS resulted,
               string_agg(DISTINCT comments, ' | ') FILTER (WHERE storetime <= $clock AND comments NOT IN ('___', '')) AS comments,
               micro_specimen_id
        FROM r GROUP BY micro_specimen_id, spec_type_desc, test_name
        ORDER BY collected DESC""", {"hadm": stay["hadm_id"], "clock": clock})
    return {"columns": _cols(("collected", "Collected"), ("specimen", "Specimen"), ("test", "Test"), ("status", "Status"),
                             ("organisms", "Organisms"), ("resistant", "Resistant to"), ("susceptible", "Susceptible to")),
            "rows": rows, "note": "Specimens collected by the clock; results appear only once they were entered."}


def procedures(st: MimicStore, stay: dict, clock: datetime, hindsight: bool = False) -> dict:
    rows = st._rows("""
        SELECT starttime AS start, CASE WHEN endtime <= $clock THEN endtime END AS "end",
               CASE WHEN endtime > $clock THEN 'ongoing' ELSE statusdescription END AS status,
               label AS procedure, category, location, locationcategory AS location_type,
               CASE WHEN endtime <= $clock THEN value END AS value, valueuom AS unit, ordercategoryname AS order_category
        FROM procedures WHERE stay_id = $stay AND starttime <= $clock
        ORDER BY (endtime > $clock) DESC, starttime DESC""", {"stay": stay["stay_id"], "clock": clock})
    return {"columns": _cols(("status", "Status"), ("procedure", "Procedure / line"), ("category", "Category"),
                             ("location", "Location"), ("start", "Start"), ("end", "End")),
            "rows": rows, "note": "ICU procedures and lines (central, arterial, PICC, dialysis, ventilation, …)."}


def journey(st: MimicStore, stay: dict, clock: datetime, hindsight: bool = False) -> dict:
    hadm = stay["hadm_id"]
    [adm] = st._rows("SELECT * FROM admissions WHERE hadm_id = ?", [hadm]) or [{}]
    discharged = bool(adm) and adm["dischtime"] <= clock
    show_coding = discharged or hindsight
    events = st._rows("""
        SELECT intime AS time, 'transfer' AS kind, coalesce(careunit, eventtype) AS what,
               CASE WHEN outtime <= $clock THEN outtime END AS until, eventtype AS detail
        FROM transfers WHERE hadm_id = $hadm AND intime <= $clock
        UNION ALL
        SELECT transfertime, 'service', curr_service, NULL, coalesce('from ' || prev_service, 'admitting service')
        FROM services WHERE hadm_id = $hadm AND transfertime <= $clock
        ORDER BY 1""", {"hadm": hadm, "clock": clock})
    coding = []
    if show_coding:
        coding += [{"kind": "diagnosis", "seq": r["seq_num"], "code": f"ICD-{r['icd_version']} {r['icd_code']}", "title": r["long_title"], "date": None}
                   for r in st._rows("SELECT * FROM diagnoses WHERE hadm_id = ? ORDER BY seq_num", [hadm])]
        coding += [{"kind": "procedure", "seq": r["seq_num"], "code": f"ICD-{r['icd_version']} {r['icd_code']}", "title": r["long_title"], "date": r["chartdate"]}
                   for r in st._rows("SELECT * FROM icd_procedures WHERE hadm_id = ? ORDER BY seq_num", [hadm])]
        coding += [{"kind": f"{'MS' if r['drg_type'] == 'HCFA' else 'APR'}-DRG", "seq": None, "code": r["drg_code"], "title": r["description"], "date": None}
                   for r in st.drg(hadm)]
    known = {k: v for k, v in adm.items() if k not in {"dischtime", "deathtime", "discharge_location", "hospital_expire_flag"}}
    if discharged:
        known |= {k: adm[k] for k in ("dischtime", "deathtime", "discharge_location", "hospital_expire_flag")}
    return {
        "admission": known,
        "columns": _cols(("time", "Time"), ("kind", "Kind"), ("what", "Unit / service"), ("until", "Until"), ("detail", "Detail")),
        "rows": events,
        "coding": coding,
        "coding_hidden": not show_coding,
        "dischtime": adm.get("dischtime") if adm else None,
        "note": "Diagnoses, ICD procedures and DRGs are coded at discharge." + (
            "" if show_coding else " Hidden until the clock passes discharge; turn on hindsight to see them."),
    }


TABLES = {"orders": orders, "emar": emar, "micro": micro, "procedures": procedures, "journey": journey}
