"""MIMIC-IV 3.1 -> Parquet store, and the queries the app runs against it.

Build once (minutes; labevents is 2.6 GB gzipped), then every per-stay query reads Parquet sorted
by stay/subject so DuckDB can skip row groups.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb

# Blood chemistry / blood gas results shown next to the grid and fed to physiologic rules.
LAB_ITEMS = {
    50971: "Potassium",
    52610: "Potassium",
    50822: "Potassium, Whole Blood",
    50983: "Sodium",
    50912: "Creatinine",
    50931: "Glucose",
    50893: "Calcium, Total",
    50960: "Magnesium",
    50813: "Lactate",
}

GRAINS = {"hour": "1 hour", "day": "1 day", "week": "1 week"}

# icu/d_items itemid -> vital. Arterial-line and cuff pressures share a vital; the label keeps the source.
VITALS = {
    "hr": ("Heart rate", "bpm", [220045]),
    "sbp": ("Systolic BP", "mmHg", [220050, 220179, 225309]),
    "dbp": ("Diastolic BP", "mmHg", [220051, 220180, 225310]),
    "map": ("Mean arterial pressure", "mmHg", [220052, 220181, 225312]),
    "rr": ("Respiratory rate", "insp/min", [220210, 224690]),
    "spo2": ("SpO2", "%", [220277]),
    "temp": ("Temperature", "°F", [223761, 223762]),
}
# Physiologically impossible values (charting errors such as 9999) are dropped at build time.
# These are plausibility bounds, not clinical normal ranges.
PLAUSIBLE = {"hr": (0, 300), "sbp": (0, 300), "dbp": (0, 250), "map": (0, 300), "rr": (0, 100),
             "spo2": (0, 100), "temp": (77, 113)}


def build_store(source: Path, out: Path) -> None:
    """Write stays, inputevents and labs Parquet files from a MIMIC-IV 3.1 directory."""
    out.mkdir(parents=True, exist_ok=True)
    hosp, icu = source / "hosp", source / "icu"
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")

    con.execute(f"""
        COPY (
            SELECT
                ie.stay_id, ie.subject_id, ie.hadm_id, ie.starttime, ie.endtime, ie.storetime,
                ie.itemid, di.label, di.category, ie.amount, ie.amountuom, ie.rate, ie.rateuom,
                ie.orderid, ie.linkorderid, ie.ordercategoryname, ie.ordercategorydescription,
                ie.ordercomponenttypedescription, ie.patientweight, ie.statusdescription
            FROM read_csv('{icu}/inputevents.csv.gz') ie
            JOIN read_csv('{icu}/d_items.csv.gz') di USING (itemid)
            ORDER BY ie.stay_id, ie.starttime
        ) TO '{out}/inputevents.parquet' (FORMAT parquet, ROW_GROUP_SIZE 100000)
    """)

    con.execute(f"""
        COPY (
            SELECT s.subject_id, s.hadm_id, s.stay_id, s.first_careunit, s.last_careunit,
                   s.intime, s.outtime, s.los, p.gender, p.anchor_age,
                   coalesce(n.n_rows, 0) AS n_infusion_rows
            FROM read_csv('{icu}/icustays.csv.gz') s
            JOIN read_csv('{hosp}/patients.csv.gz') p USING (subject_id)
            LEFT JOIN (
                SELECT stay_id, count(*) AS n_rows FROM '{out}/inputevents.parquet' GROUP BY 1
            ) n USING (stay_id)
            ORDER BY s.stay_id
        ) TO '{out}/stays.parquet' (FORMAT parquet)
    """)

    items = ", ".join(str(i) for i in LAB_ITEMS)
    con.execute(f"""
        COPY (
            SELECT le.subject_id, le.hadm_id, le.itemid, dl.label, le.charttime, le.storetime,
                   le.valuenum, le.valueuom, le.flag
            FROM read_csv('{hosp}/labevents.csv.gz', types={{'value': 'VARCHAR', 'comments': 'VARCHAR'}}) le
            JOIN read_csv('{hosp}/d_labitems.csv.gz') dl USING (itemid)
            WHERE le.itemid IN ({items}) AND le.valuenum IS NOT NULL
              AND le.subject_id IN (SELECT subject_id FROM '{out}/stays.parquet')
            ORDER BY le.subject_id, le.charttime
        ) TO '{out}/labs.parquet' (FORMAT parquet, ROW_GROUP_SIZE 100000)
    """)

    con.execute(f"""
        COPY (
            SELECT hadm_id, drg_type, drg_code, description, drg_severity, drg_mortality
            FROM read_csv('{hosp}/drgcodes.csv.gz', types={{'drg_code': 'VARCHAR'}})
            WHERE hadm_id IN (SELECT hadm_id FROM '{out}/stays.parquet')
            ORDER BY hadm_id
        ) TO '{out}/drgcodes.parquet' (FORMAT parquet)
    """)
    build_vitals(source, out)


def build_vitals(source: Path, out: Path) -> None:
    """Vital signs from icu/chartevents (3.5 GB gzipped; the slowest step). Celsius -> Fahrenheit."""
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    cases = " ".join(f"WHEN itemid IN ({', '.join(map(str, ids))}) THEN '{key}'" for key, (_, _, ids) in VITALS.items())
    bounds = " OR ".join(f"(vital = '{k}' AND value BETWEEN {lo} AND {hi})" for k, (lo, hi) in PLAUSIBLE.items())
    ids = ", ".join(str(i) for _, _, v in VITALS.values() for i in v)
    con.execute(f"""
        COPY (
            WITH raw AS (
                SELECT ce.stay_id, ce.charttime, ce.storetime, ce.itemid, di.label,
                       CASE {cases} END AS vital,
                       CASE WHEN ce.itemid = 223762 THEN ce.valuenum * 9 / 5 + 32 ELSE ce.valuenum END AS value
                FROM read_csv('{source}/icu/chartevents.csv.gz',
                              types={{'value': 'VARCHAR', 'valueuom': 'VARCHAR', 'warning': 'VARCHAR'}}) ce
                JOIN read_csv('{source}/icu/d_items.csv.gz') di USING (itemid)
                WHERE ce.itemid IN ({ids}) AND ce.valuenum IS NOT NULL AND ce.stay_id IS NOT NULL
            )
            SELECT * FROM raw WHERE {bounds}
            ORDER BY stay_id, charttime
        ) TO '{out}/vitals.parquet' (FORMAT parquet, ROW_GROUP_SIZE 100000)
    """)


class MimicStore:
    """Read-only queries over the Parquet store."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.con = duckdb.connect()
        for name in ("stays", "inputevents", "labs", "drgcodes", "vitals"):
            self.con.execute(f"CREATE VIEW {name} AS SELECT * FROM '{self.root / name}.parquet'")

    def _rows(self, sql: str, params: list | dict) -> list[dict]:
        cur = self.con.cursor().execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def stays(self, q: str | None = None, limit: int = 50) -> list[dict]:
        where, params = "n_infusion_rows > 0", []
        if q:
            where += " AND (CAST(stay_id AS VARCHAR) LIKE ? OR CAST(subject_id AS VARCHAR) LIKE ?)"
            params += [f"{q}%", f"{q}%"]
        return self._rows(
            f"SELECT * FROM stays WHERE {where} ORDER BY stay_id LIMIT ?", [*params, limit]
        )

    def stay(self, stay_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM stays WHERE stay_id = ?", [stay_id])
        return rows[0] if rows else None

    def item(self, stay_id: int, itemid: int) -> dict | None:
        """Label/category of an item as charted in this stay (stay-scoped so row groups are skipped)."""
        rows = self._rows(
            "SELECT itemid, label, category FROM inputevents WHERE stay_id = ? AND itemid = ? LIMIT 1",
            [stay_id, itemid],
        )
        return rows[0] if rows else None

    def infusions(
        self, stay_id: int, until: datetime | None = None, since: datetime | None = None
    ) -> list[dict]:
        """Inputevents rows of a stay that had started by `until` and not ended before `since`."""
        sql, params = "SELECT * FROM inputevents WHERE stay_id = ?", [stay_id]
        if until:
            sql += " AND starttime <= ?"
            params.append(until)
        if since:
            sql += " AND endtime >= ?"
            params.append(since)
        return self._rows(sql + " ORDER BY starttime, orderid, itemid", params)

    def labs(self, subject_id: int, start: datetime, until: datetime) -> list[dict]:
        """Lab results drawn in [start, until]. Availability (storetime) is left to the caller."""
        return self._rows(
            "SELECT * FROM labs WHERE subject_id = ? AND charttime BETWEEN ? AND ? ORDER BY charttime",
            [subject_id, start, until],
        )

    def grid(self, stay_id: int, grain: str, start: datetime, end: datetime) -> list[dict]:
        """Amount delivered per drug per bucket in [start, end).

        Each row's charted `amount` is spread over the buckets it overlaps in proportion to time,
        counting only time before `end` (the replay clock). Rows shorter than a minute (boluses)
        land entirely in the bucket of their start time.
        """
        if grain not in GRAINS:
            raise ValueError(f"grain must be one of {sorted(GRAINS)}")
        step = GRAINS[grain]
        return self._rows(
            f"""
            WITH ev AS (
                SELECT itemid, label, category, amountuom, amount, rate, rateuom,
                       ordercategorydescription AS kind, starttime,
                       greatest(endtime, starttime + INTERVAL 1 minute) AS endtime
                FROM inputevents
                WHERE stay_id = $stay AND amount IS NOT NULL
                  AND starttime < $end AND endtime >= $start
            ),
            buckets AS (
                SELECT b AS bstart, b + INTERVAL {step} AS bend
                FROM generate_series(time_bucket(INTERVAL {step}, $start::TIMESTAMP),
                                     $end::TIMESTAMP - INTERVAL 1 second, INTERVAL {step}) t(b)
            )
            SELECT itemid, label, category, amountuom AS unit, bstart AS bucket,
                   sum(amount * epoch(least(endtime, bend, $end::TIMESTAMP) - greatest(starttime, bstart))
                       / epoch(endtime - starttime)) AS amount,
                   max(rate) FILTER (WHERE rate IS NOT NULL) AS max_rate,
                   any_value(rateuom) FILTER (WHERE rateuom IS NOT NULL) AS rate_unit,
                   bool_or(kind = 'Continuous IV' OR kind = 'Continuous Med') AS continuous
            FROM ev JOIN buckets ON starttime < bend AND endtime > bstart
            GROUP BY ALL
            HAVING sum(epoch(least(endtime, bend, $end::TIMESTAMP) - greatest(starttime, bstart))) > 0
            ORDER BY category, label, bucket
            """,
            {"stay": stay_id, "start": start, "end": end},
        )

    def cell_events(
        self, stay_id: int, itemid: int, grain: str, bucket: datetime, end: datetime
    ) -> list[dict]:
        """The charted rows behind one grid cell, each with its share (`in_bucket`) of the cell.

        Same overlap arithmetic as `grid`, so the shares sum to the cell's amount.
        """
        if grain not in GRAINS:
            raise ValueError(f"grain must be one of {sorted(GRAINS)}")
        rows = self._rows(
            f"""
            WITH b AS (SELECT $bucket::TIMESTAMP AS bstart,
                              $bucket::TIMESTAMP + INTERVAL {GRAINS[grain]} AS bend),
            ev AS (
                SELECT *, greatest(endtime, starttime + INTERVAL 1 minute) AS eff_end
                FROM inputevents
                WHERE stay_id = $stay AND itemid = $item AND amount IS NOT NULL
            )
            SELECT starttime, endtime, amount, amountuom, rate, rateuom, orderid, linkorderid,
                   ordercategorydescription, ordercomponenttypedescription, statusdescription,
                   patientweight, storetime, bstart AS bucket, bend AS bucket_end,
                   amount * epoch(least(eff_end, bend, $end::TIMESTAMP) - greatest(starttime, bstart))
                       / epoch(eff_end - starttime) AS in_bucket
            FROM ev, b
            WHERE starttime < bend AND eff_end > bstart
              AND least(eff_end, bend, $end::TIMESTAMP) > greatest(starttime, bstart)
            ORDER BY starttime
            """,
            {"stay": stay_id, "item": itemid, "bucket": bucket, "end": end},
        )
        # Everything else charted under the same order: the carrier fluid of an additive, etc.
        bag: dict[int, list[dict]] = {}
        if orders := sorted({r["orderid"] for r in rows}):
            for b in self._rows(
                f"""SELECT orderid, label, amount, amountuom AS unit, rate, rateuom AS rate_unit
                    FROM inputevents WHERE stay_id = ? AND itemid <> ?
                      AND orderid IN ({", ".join("?" * len(orders))})
                    ORDER BY label""",
                [stay_id, itemid, *orders],
            ):
                bag.setdefault(b.pop("orderid"), []).append(b)
        for r in rows:
            r["bag"] = bag.get(r["orderid"], [])
        return rows

    def billing_rows(self, stay_id: int) -> list[dict]:
        """Charted amount per calendar day x item x order, split across days by overlap."""
        return self._rows(
            """
            WITH ev AS (
                SELECT *, greatest(endtime, starttime + INTERVAL 1 minute) AS eff_end
                FROM inputevents WHERE stay_id = $stay AND amount IS NOT NULL
                  -- the carrier a drug is mixed in is part of the drug preparation, not billed alone
                  AND NOT (category = 'Fluids/Intake' AND ordercomponenttypedescription = 'Mixed solution')
            ),
            days AS (
                SELECT d AS dstart, d + INTERVAL 1 day AS dend
                FROM ev, generate_series(date_trunc('day', starttime), eff_end, INTERVAL 1 day) t(d)
                GROUP BY d
            )
            SELECT dstart AS day, itemid, label, category, linkorderid, ordercategoryname, amountuom,
                   sum(amount * epoch(least(eff_end, dend) - greatest(starttime, dstart))
                       / epoch(eff_end - starttime)) AS amount
            FROM ev JOIN days ON starttime < dend AND eff_end > dstart
            GROUP BY ALL
            HAVING sum(epoch(least(eff_end, dend) - greatest(starttime, dstart))) > 0
            ORDER BY day, label
            """,
            {"stay": stay_id},
        )

    def orders(self, stay_id: int) -> list[dict]:
        """One row per charted order segment (orderid) with its components."""
        rows = self._rows(
            """
            SELECT orderid, min(starttime) AS start,
                   max(greatest(endtime, starttime + INTERVAL 1 minute)) AS "end",
                   any_value(ordercategoryname) AS ordercategoryname,
                   any_value(ordercategorydescription) AS ordercategorydescription,
                   list(DISTINCT struct_pack(itemid := itemid, label := label, category := category)) AS items
            FROM inputevents WHERE stay_id = ?
            GROUP BY orderid ORDER BY start
            """,
            [stay_id],
        )
        for r in rows:
            r["items"] = [(i["itemid"], i["label"], i["category"]) for i in r["items"]]
        return rows

    def drg(self, hadm_id: int) -> list[dict]:
        return self._rows("SELECT * FROM drgcodes WHERE hadm_id = ? ORDER BY drg_type", [hadm_id])

    def vitals_grid(self, stay_id: int, grain: str, start: datetime, end: datetime) -> list[dict]:
        """Median / min / max / last / count of each vital per bucket, readings up to `end`."""
        if grain not in GRAINS:
            raise ValueError(f"grain must be one of {sorted(GRAINS)}")
        step = GRAINS[grain]
        return self._rows(
            f"""
            SELECT vital, time_bucket(INTERVAL {step}, charttime) AS bucket,
                   median(value) AS median, min(value) AS min, max(value) AS max,
                   arg_max(value, charttime) AS last, count(*) AS n
            FROM vitals
            WHERE stay_id = $stay AND charttime <= $end
              AND charttime >= time_bucket(INTERVAL {step}, $start::TIMESTAMP)
            GROUP BY ALL ORDER BY vital, bucket
            """,
            {"stay": stay_id, "start": start, "end": end},
        )

    def vitals_latest(self, stay_id: int, as_of: datetime) -> list[dict]:
        """Latest reading of each vital that had been charted (stored) by as_of."""
        return self._rows(
            """
            SELECT vital, arg_max(value, charttime) AS value, max(charttime) AS charttime,
                   arg_max(label, charttime) AS label
            FROM vitals
            WHERE stay_id = ? AND charttime <= ? AND coalesce(storetime, charttime) <= ?
              AND charttime >= ?::TIMESTAMP - INTERVAL 24 hour
            GROUP BY vital
            """,
            [stay_id, as_of, as_of, as_of],
        )

    def vital_readings(
        self, stay_id: int, vital: str, grain: str, bucket: datetime, end: datetime
    ) -> list[dict]:
        if grain not in GRAINS:
            raise ValueError(f"grain must be one of {sorted(GRAINS)}")
        return self._rows(
            f"""
            SELECT charttime, storetime, value, label, itemid FROM vitals
            WHERE stay_id = $stay AND vital = $vital AND charttime <= $end
              AND charttime >= $bucket::TIMESTAMP
              AND charttime < $bucket::TIMESTAMP + INTERVAL {GRAINS[grain]}
            ORDER BY charttime
            """,
            {"stay": stay_id, "vital": vital, "bucket": bucket, "end": end},
        )
