"""Build steps for the data tabs: one named part per MIMIC-IV source, each writing one Parquet file.

Hospital tables are limited to admissions that have an ICU stay (`stays.parquet` must exist).
Rows keep their availability times (storetime, verifiedtime, ...) so the app can hide what was not
yet known at the pump clock.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import duckdb

# icu/d_items itemid -> assessment. GCS, RASS, pain and ventilator mode are charted as text;
# `valuenum` is kept when MIMIC provides it.
ASSESSMENTS = {
    "rass": ("RASS (sedation)", "", [228096]),
    "rass_goal": ("RASS goal", "", [228299]),
    "gcs_eye": ("GCS eye", "", [220739]),
    "gcs_verbal": ("GCS verbal", "", [223900]),
    "gcs_motor": ("GCS motor", "", [223901]),
    "pain": ("Pain level", "", [223791]),
    "vent_mode": ("Ventilator mode", "", [223849]),
    "fio2": ("FiO2", "%", [223835]),
    "peep": ("PEEP set", "cmH2O", [220339]),
    "vt_obs": ("Tidal volume (observed)", "mL", [224685]),
    "vt_set": ("Tidal volume (set)", "mL", [224684]),
    "weight": ("Daily weight", "kg", [224639]),
}


def _icu_hadm(out: Path) -> str:
    return f"(SELECT hadm_id FROM '{out}/stays.parquet')"


def _copy(con, sql: str, out: Path, name: str) -> None:
    con.execute(f"COPY ({sql}) TO '{out}/{name}.parquet' (FORMAT parquet, ROW_GROUP_SIZE 100000)")


def outputs(con, src: Path, out: Path) -> None:
    _copy(con, f"""
        SELECT o.stay_id, o.charttime, o.storetime, o.itemid, d.label, d.category, o.value, o.valueuom
        FROM read_csv('{src}/icu/outputevents.csv.gz') o JOIN read_csv('{src}/icu/d_items.csv.gz') d USING (itemid)
        WHERE o.value IS NOT NULL ORDER BY o.stay_id, o.charttime""", out, "outputs")


def procedures(con, src: Path, out: Path) -> None:
    _copy(con, f"""
        SELECT p.stay_id, p.starttime, p.endtime, p.storetime, p.itemid, d.label, d.category,
               p.value, p.valueuom, p.location, p.locationcategory, p.ordercategoryname, p.statusdescription
        FROM read_csv('{src}/icu/procedureevents.csv.gz', types={{'location': 'VARCHAR', 'locationcategory': 'VARCHAR'}}) p
        JOIN read_csv('{src}/icu/d_items.csv.gz') d USING (itemid)
        ORDER BY p.stay_id, p.starttime""", out, "procedures")


def ingredients(con, src: Path, out: Path) -> None:
    _copy(con, f"""
        SELECT i.stay_id, i.starttime, i.endtime, i.storetime, i.itemid, d.label, i.amount, i.amountuom,
               i.orderid, i.statusdescription
        FROM read_csv('{src}/icu/ingredientevents.csv.gz') i JOIN read_csv('{src}/icu/d_items.csv.gz') d USING (itemid)
        WHERE i.amount IS NOT NULL AND i.amount > 0 ORDER BY i.stay_id, i.starttime""", out, "ingredients")


def assessments(con, src: Path, out: Path) -> None:
    cases = " ".join(f"WHEN itemid IN ({', '.join(map(str, ids))}) THEN '{k}'" for k, (_, _, ids) in ASSESSMENTS.items())
    ids = ", ".join(str(i) for _, _, v in ASSESSMENTS.values() for i in v)
    _copy(con, f"""
        SELECT ce.stay_id, ce.charttime, ce.storetime, ce.itemid, CASE {cases} END AS key,
               ce.value, ce.valuenum, ce.valueuom
        FROM read_csv('{src}/icu/chartevents.csv.gz',
                      types={{'value': 'VARCHAR', 'valueuom': 'VARCHAR', 'warning': 'VARCHAR'}}) ce
        WHERE ce.itemid IN ({ids}) AND ce.stay_id IS NOT NULL AND ce.value IS NOT NULL
        ORDER BY ce.stay_id, ce.charttime""", out, "assessments")


def labs_all(con, src: Path, out: Path) -> None:
    _copy(con, f"""
        SELECT le.subject_id, le.hadm_id, le.itemid, dl.label, dl.fluid, dl.category, le.charttime, le.storetime,
               le.value, le.valuenum, le.valueuom, le.ref_range_lower, le.ref_range_upper, le.flag, le.priority
        FROM read_csv('{src}/hosp/labevents.csv.gz',
                      types={{'value': 'VARCHAR', 'comments': 'VARCHAR', 'valueuom': 'VARCHAR', 'flag': 'VARCHAR',
                              'priority': 'VARCHAR', 'order_provider_id': 'VARCHAR'}}) le
        JOIN read_csv('{src}/hosp/d_labitems.csv.gz') dl USING (itemid)
        WHERE le.hadm_id IN {_icu_hadm(out)}
        ORDER BY le.hadm_id, le.charttime""", out, "labs_all")


def admissions(con, src: Path, out: Path) -> None:
    _copy(con, f"""
        SELECT * EXCLUDE (admit_provider_id)
        FROM read_csv('{src}/hosp/admissions.csv.gz') WHERE hadm_id IN {_icu_hadm(out)}""", out, "admissions")


def transfers(con, src: Path, out: Path) -> None:
    _copy(con, f"""
        SELECT hadm_id, transfer_id, eventtype, careunit, intime, outtime
        FROM read_csv('{src}/hosp/transfers.csv.gz') WHERE hadm_id IN {_icu_hadm(out)}
        ORDER BY hadm_id, intime""", out, "transfers")


def services(con, src: Path, out: Path) -> None:
    _copy(con, f"""
        SELECT hadm_id, transfertime, prev_service, curr_service
        FROM read_csv('{src}/hosp/services.csv.gz') WHERE hadm_id IN {_icu_hadm(out)}
        ORDER BY hadm_id, transfertime""", out, "services")


def diagnoses(con, src: Path, out: Path) -> None:
    _copy(con, f"""
        SELECT d.hadm_id, d.seq_num, d.icd_code, d.icd_version, t.long_title
        FROM read_csv('{src}/hosp/diagnoses_icd.csv.gz', types={{'icd_code': 'VARCHAR'}}) d
        LEFT JOIN read_csv('{src}/hosp/d_icd_diagnoses.csv.gz', types={{'icd_code': 'VARCHAR'}}) t
          USING (icd_code, icd_version)
        WHERE d.hadm_id IN {_icu_hadm(out)} ORDER BY d.hadm_id, d.seq_num""", out, "diagnoses")


def icd_procedures(con, src: Path, out: Path) -> None:
    _copy(con, f"""
        SELECT p.hadm_id, p.seq_num, p.chartdate, p.icd_code, p.icd_version, t.long_title
        FROM read_csv('{src}/hosp/procedures_icd.csv.gz', types={{'icd_code': 'VARCHAR'}}) p
        LEFT JOIN read_csv('{src}/hosp/d_icd_procedures.csv.gz', types={{'icd_code': 'VARCHAR'}}) t
          USING (icd_code, icd_version)
        WHERE p.hadm_id IN {_icu_hadm(out)} ORDER BY p.hadm_id, p.seq_num""", out, "icd_procedures")


def micro(con, src: Path, out: Path) -> None:
    _copy(con, f"""
        SELECT TRY_CAST(hadm_id AS BIGINT) AS hadm_id, micro_specimen_id,
               coalesce(TRY_CAST(charttime AS TIMESTAMP), TRY_CAST(chartdate AS TIMESTAMP)) AS charttime,
               coalesce(TRY_CAST(storetime AS TIMESTAMP), TRY_CAST(storedate AS TIMESTAMP)) AS storetime,
               spec_type_desc, test_name, org_name, TRY_CAST(isolate_num AS INT) AS isolate_num, quantity,
               ab_name, dilution_text, interpretation, comments
        FROM read_csv('{src}/hosp/microbiologyevents.csv.gz', all_varchar = true)
        WHERE TRY_CAST(hadm_id AS BIGINT) IN {_icu_hadm(out)}
        ORDER BY hadm_id, charttime""", out, "micro")


def orders(con, src: Path, out: Path) -> None:
    """Pharmacy orders with the prescribed dose (prescriptions, MAIN component) joined on."""
    _copy(con, f"""
        WITH rx AS (
            SELECT pharmacy_id,
                   string_agg(DISTINCT drug, ' + ') AS drug,
                   string_agg(DISTINCT dose_val_rx || ' ' || dose_unit_rx, ' + ')
                       FILTER (WHERE drug_type = 'MAIN') AS dose,
                   any_value(prod_strength) AS prod_strength,
                   any_value(order_provider_id) IS NOT NULL AS has_provider
            FROM read_csv('{src}/hosp/prescriptions.csv.gz', all_varchar = true)
            WHERE TRY_CAST(hadm_id AS BIGINT) IN {_icu_hadm(out)}
            GROUP BY pharmacy_id
        )
        SELECT TRY_CAST(p.hadm_id AS BIGINT) AS hadm_id, p.pharmacy_id, p.poe_id,
               TRY_CAST(p.starttime AS TIMESTAMP) AS starttime, TRY_CAST(p.stoptime AS TIMESTAMP) AS stoptime,
               TRY_CAST(p.entertime AS TIMESTAMP) AS entertime, TRY_CAST(p.verifiedtime AS TIMESTAMP) AS verifiedtime,
               p.medication, rx.drug, rx.dose, rx.prod_strength, p.route, p.frequency, p.status, p.proc_type,
               p.infusion_type, p.sliding_scale, p.doses_per_24_hrs, p.duration, p.duration_interval, p.dispensation
        FROM read_csv('{src}/hosp/pharmacy.csv.gz', all_varchar = true) p
        LEFT JOIN rx USING (pharmacy_id)
        WHERE TRY_CAST(p.hadm_id AS BIGINT) IN {_icu_hadm(out)}
        ORDER BY hadm_id, starttime""", out, "orders")


def emar(con, src: Path, out: Path) -> None:
    """eMAR events with their detail rows collapsed to one row per administration."""
    _copy(con, f"""
        WITH d AS (
            SELECT emar_id,
                   any_value(dose_given || coalesce(' ' || dose_given_unit, '')) FILTER (WHERE dose_given IS NOT NULL) AS dose_given,
                   any_value(dose_due || coalesce(' ' || dose_due_unit, '')) FILTER (WHERE dose_due IS NOT NULL) AS dose_due,
                   any_value(route) FILTER (WHERE route IS NOT NULL) AS route,
                   any_value(infusion_rate || coalesce(' ' || infusion_rate_unit, '')) FILTER (WHERE infusion_rate IS NOT NULL) AS infusion_rate,
                   any_value(administration_type) FILTER (WHERE administration_type IS NOT NULL) AS administration_type,
                   string_agg(DISTINCT product_description, '; ') AS products,
                   any_value(site) FILTER (WHERE site IS NOT NULL) AS site,
                   any_value(reason_for_no_barcode) FILTER (WHERE reason_for_no_barcode IS NOT NULL) AS no_barcode_reason,
                   any_value(complete_dose_not_given) FILTER (WHERE complete_dose_not_given IS NOT NULL) AS complete_dose_not_given
            FROM read_csv('{src}/hosp/emar_detail.csv.gz', all_varchar = true)
            GROUP BY emar_id
        )
        SELECT TRY_CAST(e.hadm_id AS BIGINT) AS hadm_id, e.emar_id, e.pharmacy_id, e.poe_id,
               TRY_CAST(e.charttime AS TIMESTAMP) AS charttime, TRY_CAST(e.scheduletime AS TIMESTAMP) AS scheduletime,
               TRY_CAST(e.storetime AS TIMESTAMP) AS storetime, e.medication, e.event_txt, d.* EXCLUDE (emar_id)
        FROM read_csv('{src}/hosp/emar.csv.gz', all_varchar = true) e LEFT JOIN d USING (emar_id)
        WHERE TRY_CAST(e.hadm_id AS BIGINT) IN {_icu_hadm(out)}
        ORDER BY hadm_id, charttime""", out, "emar")


PARTS: dict[str, Callable] = {
    f.__name__: f
    for f in (outputs, procedures, ingredients, assessments, labs_all, admissions, transfers, services,
              diagnoses, icd_procedures, micro, orders, emar)
}


def build_extras(source: Path, out: Path, only: list[str] | None = None) -> None:
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    for name in only or list(PARTS):
        PARTS[name](con, Path(source), Path(out))
