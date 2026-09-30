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
                ie.orderid, ie.linkorderid, ie.ordercategorydescription,
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


class MimicStore:
    """Read-only queries over the Parquet store."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.con = duckdb.connect()
        for name in ("stays", "inputevents", "labs"):
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
