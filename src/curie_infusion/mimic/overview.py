"""Data for the Overview tab: one time range ending at the pump clock, everything on a shared axis.

Only what was known by the clock: vitals and labs by their charted/resulted time, infusions
clipped at the clock (a running infusion has no end yet).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .store import VITALS, MimicStore
from .tabs import IRRIGANT_IN

KEY_LABS = {  # hosp/d_labitems, verified labels
    50971: ("K", "Potassium", "mEq/L"),
    50983: ("Na", "Sodium", "mEq/L"),
    50912: ("Cr", "Creatinine", "mg/dL"),
    50813: ("Lac", "Lactate", "mmol/L"),
    50931: ("Glu", "Glucose", "mg/dL"),
    51222: ("Hgb", "Hemoglobin", "g/dL"),
    51301: ("WBC", "White blood cells", "K/uL"),
    51265: ("Plt", "Platelets", "K/uL"),
}
DRUG_CATEGORIES = ("Medications", "Antibiotics", "Blood Products/Colloids")
MAX_LANES = 12
BOLUS_MINUTES = 30  # rate-less rows shorter than this are drawn as bolus ticks, longer as additive lanes


def time_range(stay: dict, clock: datetime, hours: int) -> datetime:
    """Start of the range: `hours` before the clock, or the ICU admission for hours <= 0."""
    return stay["intime"] if hours <= 0 else max(stay["intime"] - timedelta(hours=6), clock - timedelta(hours=hours))


def overview(st: MimicStore, stay: dict, clock: datetime, hours: int) -> dict:
    start = time_range(stay, clock, hours)
    span_h = (clock - start).total_seconds() / 3600
    p = {"stay": stay["stay_id"], "start": start, "clock": clock}

    if span_h > 72:  # hourly medians keep long ranges light
        vit = st._rows("""
            SELECT vital, time_bucket(INTERVAL 1 hour, charttime) + INTERVAL 30 minute AS t, median(value) AS v
            FROM vitals WHERE stay_id = $stay AND charttime BETWEEN $start AND $clock
              AND coalesce(storetime, charttime) <= $clock GROUP BY ALL ORDER BY t""", p)
    else:
        vit = st._rows("""
            SELECT vital, charttime AS t, value AS v FROM vitals
            WHERE stay_id = $stay AND charttime BETWEEN $start AND $clock
              AND coalesce(storetime, charttime) <= $clock ORDER BY t""", p)
    vitals: dict[str, list] = {k: [] for k in VITALS}
    for r in vit:
        vitals[r["vital"]].append([r["t"].isoformat(), round(r["v"], 1)])

    rows = st._rows(f"""
        SELECT itemid, label, starttime, endtime, rate, rateuom, amount, amountuom, ordercategorydescription AS kind
        FROM inputevents
        WHERE stay_id = $stay AND category IN {DRUG_CATEGORIES} AND amount IS NOT NULL
          AND starttime <= $clock AND greatest(endtime, starttime + INTERVAL 1 minute) > $start
          AND ordercomponenttypedescription <> 'Mixed solution'
        ORDER BY starttime""", p)
    lanes: dict[int, dict] = {}
    boluses = []
    for r in rows:
        minutes = (r["endtime"] - r["starttime"]).total_seconds() / 60
        if r["rate"] is None and minutes <= BOLUS_MINUTES:
            if r["starttime"] >= start:
                boluses.append({"t": r["starttime"].isoformat(), "label": r["label"],
                                "amount": r["amount"], "unit": r["amountuom"], "itemid": r["itemid"]})
            continue
        lane = lanes.setdefault(r["itemid"], {"itemid": r["itemid"], "label": r["label"], "unit": r["rateuom"],
                                              "segments": [], "current": None, "max_rate": 0.0})
        running = r["endtime"] > clock
        lane["segments"].append([r["starttime"].isoformat(), None if running else r["endtime"].isoformat(), r["rate"]])
        if r["rate"] is not None:
            lane["max_rate"] = max(lane["max_rate"], r["rate"])
            lane["unit"] = lane["unit"] or r["rateuom"]
        if running:
            lane["current"] = {"rate": r["rate"], "unit": r["rateuom"], "since": r["starttime"].isoformat()}
    ordered = sorted(lanes.values(), key=lambda lane: (lane["current"] is None, lane["segments"][0][0]))

    labs = st._rows(f"""
        SELECT itemid, charttime AS t, valuenum AS v, flag = 'abnormal' AS abnormal, storetime
        FROM labs_all WHERE hadm_id = $hadm AND itemid IN ({", ".join(map(str, KEY_LABS))})
          AND charttime BETWEEN $start AND $clock AND coalesce(storetime, charttime) <= $clock
          AND valuenum IS NOT NULL ORDER BY t""", {"hadm": stay["hadm_id"], "start": start, "clock": clock}) \
        if "labs_all" in st.tables else []
    lab_series = {i: {"itemid": i, "key": k, "label": label, "unit": unit, "points": []} for i, (k, label, unit) in KEY_LABS.items()}
    for r in labs:
        lab_series[r["itemid"]]["points"].append([r["t"].isoformat(), r["v"], bool(r["abnormal"])])

    return {
        "start": start.isoformat(), "end": clock.isoformat(), "span_hours": round(span_h, 1),
        "vitals": vitals,
        "drips": ordered[:MAX_LANES], "more_drips": max(0, len(ordered) - MAX_LANES),
        "boluses": boluses,
        "labs": [s for s in lab_series.values() if s["points"]],
        "stats": stats(st, stay, clock),
    }


def stats(st: MimicStore, stay: dict, clock: datetime) -> dict:
    """Headline numbers at the clock, with a 24 h sparkline per vital."""
    since = clock - timedelta(hours=24)
    p = {"stay": stay["stay_id"], "since": since, "clock": clock}
    spark = st._rows("""
        SELECT vital, time_bucket(INTERVAL 1 hour, charttime) AS t, median(value) AS v
        FROM vitals WHERE stay_id = $stay AND charttime BETWEEN $since AND $clock
          AND coalesce(storetime, charttime) <= $clock GROUP BY ALL ORDER BY t""", p)
    latest = {v["vital"]: v for v in st.vitals_latest(stay["stay_id"], clock)}
    vit = {}
    for key, (label, unit, _) in VITALS.items():
        if key in latest:
            vit[key] = {"label": label, "unit": unit, "value": round(latest[key]["value"], 1),
                        "at": latest[key]["charttime"].isoformat(),
                        "spark": [[r["t"].isoformat(), round(r["v"], 1)] for r in spark if r["vital"] == key]}
    fluid = None
    if "outputs" in st.tables:
        [row] = st._rows(f"""
            SELECT
              (SELECT coalesce(sum(amount * epoch(least(greatest(endtime, starttime + INTERVAL 1 minute), $clock)
                                                  - greatest(starttime, $since))
                                   / epoch(greatest(endtime, starttime + INTERVAL 1 minute) - starttime)), 0)
               FROM inputevents WHERE stay_id = $stay AND amountuom = 'mL' AND amount > 0
                 AND starttime < $clock AND greatest(endtime, starttime + INTERVAL 1 minute) > $since) AS intake,
              (SELECT coalesce(sum(CASE WHEN itemid = {IRRIGANT_IN} THEN -value ELSE value END), 0)
               FROM outputs WHERE stay_id = $stay AND valueuom = 'mL' AND charttime > $since AND charttime <= $clock
                 AND coalesce(storetime, charttime) <= $clock) AS output""", p)
        fluid = {"intake": round(row["intake"]), "output": round(row["output"]), "net": round(row["intake"] - row["output"])}
    return {"vitals": vit, "fluid_24h": fluid}
