from datetime import datetime

import pytest

from curie_infusion.billing.charges import admin_lines, drug_lines, load_crosswalk
from curie_infusion.billing.prices import (
    PriceFile,
    billing_units,
    load_asp,
    load_opps,
    parse_dosage,
)

DAY = datetime(2150, 1, 1)
ASP = PriceFile("test", "", {
    "J3480": {"description": "Inj potassium chloride", "dosage": "2 MEQ", "limit": 0.13},
    "J2704": {"description": "Inj, propofol, 10 mg", "dosage": "10 MG", "limit": 0.093},
    "J1308": {"description": "Inj, famotidine, 0.25 mg", "dosage": "0.25 MG", "limit": 0.009},
})
XWALK = load_crosswalk()


def at(hhmm):
    h, m = map(int, hhmm.split(":"))
    return DAY.replace(hour=h, minute=m)


@pytest.mark.parametrize(("amount", "unit", "dosage", "expected"), [
    (40, "mEq", "2 MEQ", 20), (40, "mEq.", "2 MEQ", 20), (125, "mg", "10 MG", 12.5),
    (150, "mcg", "0.1 MG", 1.5), (2, "grams", "500 MG", 4), (1500, "mL", "1000 CC", 1.5),
    (5000, "units", "1000 UNITS", 5), (1, "dose", "10 MG", None), (1, "mg", "10 MG/3MG", None),
    (10, "mg", "2 MEQ", None),
])
def test_billing_units(amount, unit, dosage, expected):
    got = billing_units(amount, unit, dosage)
    assert got == (pytest.approx(expected) if expected is not None else None)


def test_parse_dosage():
    assert parse_dosage("0.1 MG") == (0.1, "mg")
    assert parse_dosage("50 MCG (250 IU)") is None


def row(itemid, label, amount, unit, order=1, cat="01-Drips", category="Medications"):
    return {"day": DAY, "itemid": itemid, "label": label, "category": category, "linkorderid": order,
            "ordercategoryname": cat, "amountuom": unit, "amount": amount}


def test_units_round_up_per_order_then_sum():
    [line] = drug_lines([row(225166, "Potassium Chloride", 3, "mEq", 1), row(225166, "Potassium Chloride", 3, "mEq", 2)], XWALK, ASP)
    assert (line["code"], line["units"], line["charge"]) == ("J3480", 4, pytest.approx(0.52))
    assert line["amount"] == pytest.approx(6)


def test_unpriced_lines_say_why():
    lines = drug_lines([
        row(225907, "Famotidine (Pepcid)", 1, "dose"),
        row(221906, "Norepinephrine", 4, "mg"),
        row(999999, "Mystery drug", 1, "mg"),
        row(222056, "Nitroglycerin", 5, "mg"),  # mapped, but code absent from this ASP file
    ], XWALK, ASP)
    reasons = {line["labels"][0]: line["reason"] for line in lines}
    assert all(line["status"] == "unpriced" for line in lines)
    assert "strength not recorded" in reasons["Famotidine (Pepcid)"]
    assert "no ASP payment limit" in reasons["Norepinephrine"]
    assert reasons["Mystery drug"] == "no HCPCS crosswalk for this item"
    assert "no CMS payment limit" in reasons["Nitroglycerin"]


def test_oral_intake_and_uncoded_fluids_are_not_billed():
    assert drug_lines([
        row(222168, "Propofol", 100, "mg", cat="14-Oral/Gastric Intake"),
        row(225943, "Solution", 250, "mL", category="Fluids/Intake"),
    ], XWALK, ASP) == []


def order(start, end, cat="01-Drips", desc="Continuous Med", items=((222168, "Propofol", "Medications"),), oid=1):
    return {"orderid": oid, "start": at(start), "end": at(end), "ordercategoryname": cat,
            "ordercategorydescription": desc, "items": list(items)}


def codes(lines):
    return {line["code"]: line["units"] for line in lines}


@pytest.mark.parametrize(("end", "expected"), [
    ("10:10", {}),                           # 10 min drip: that is a push, see below
    ("11:00", {"96365": 1}),
    ("12:30", {"96365": 1, "96366": 1}),     # 90 extra min: 1 h + 30 min (not > 30)
    ("12:40", {"96365": 1, "96366": 2}),     # 100 extra min: 1 h + 40 min
])
def test_infusion_hours(end, expected):
    got = codes(admin_lines([order("10:00", end)], None))
    got.pop("96374", None)
    assert got == expected


def test_short_drip_counts_as_push():
    assert codes(admin_lines([order("10:00", "10:10")], None)) == {"96374": 1}


PUSH = dict(cat="05-Med Bolus", desc="Drug Push")
FAM = ((225907, "Famotidine (Pepcid)", "Medications"),)
LORAZ = ((221385, "Lorazepam (Ativan)", "Medications"),)


def test_push_sequence():
    lines = admin_lines([
        order("08:00", "08:01", items=FAM, **PUSH),
        order("08:05", "08:06", items=LORAZ, **PUSH),
        order("08:15", "08:16", items=FAM, **PUSH),   # same drug within 30 min: not billed again
        order("09:00", "09:01", items=FAM, **PUSH),   # same drug > 30 min later
    ], None)
    assert codes(lines) == {"96374": 1, "96375": 1, "96376": 1}


def test_push_during_an_infusion_is_not_initial():
    lines = admin_lines([order("08:00", "10:00"), order("09:00", "09:01", items=FAM, **PUSH)], None)
    assert codes(lines) == {"96365": 1, "96366": 1, "96375": 1}


NACL = ((225158, "NaCl 0.9%", "Fluids/Intake"),)


def test_hydration_alone_and_drug_in_fluid():
    assert codes(admin_lines([order("08:00", "09:40", cat="02-Fluids (Crystalloids)", desc="Continuous IV", items=NACL)], None)) == {
        "96360": 1, "96361": 1}
    kcl_bag = NACL + ((225166, "Potassium Chloride", "Medications"),)
    assert codes(admin_lines([order("08:00", "09:00", cat="02-Fluids (Crystalloids)", desc="Continuous IV", items=kcl_bag)], None)) == {
        "96365": 1}


def test_hydration_under_a_drip_is_not_billed():
    lines = admin_lines([
        order("08:00", "12:00"),
        order("08:00", "12:00", cat="02-Fluids (Crystalloids)", desc="Continuous IV", items=NACL, oid=2),
    ], None)
    assert "96360" not in codes(lines) and "96361" not in codes(lines)


def test_sequential_versus_concurrent_drugs():
    seq = admin_lines([order("08:00", "09:00"), order("09:00", "10:00", items=FAM, oid=2)], None)
    assert codes(seq) == {"96365": 1, "96366": 1, "96367": 1}
    conc = admin_lines([order("08:00", "10:00"), order("08:00", "10:00", items=FAM, oid=2)], None)
    assert codes(conc) == {"96365": 1, "96366": 1, "96368": 1}


def test_infusion_across_midnight_splits_by_day():
    o = order("22:00", "22:00")
    o["end"] = datetime(2150, 1, 2, 2, 0)
    lines = admin_lines([o], None)
    assert {(line["day"].day, line["code"]): line["units"] for line in lines} == {
        (1, "96365"): 1, (1, "96366"): 1, (2, "96365"): 1, (2, "96366"): 1}


def test_administration_pricing_states():
    [line] = admin_lines([order("08:00", "09:00")], None)
    assert line["status"] == "units_only" and line["charge"] is None
    opps = PriceFile("t", "", {"96365": {"rate": 190.5, "si": "S"}})
    [line] = admin_lines([order("08:00", "09:00")], opps)
    assert (line["status"], line["charge"]) == ("priced", 190.5)
    opps = PriceFile("t", "", {"96365": {"rate": None, "si": "N"}})
    [line] = admin_lines([order("08:00", "09:00")], opps)
    assert line["status"] == "packaged" and "N" in line["reason"]


def test_load_asp_reads_windows_1252(tmp_path):
    text = ("Payment Allowance Limits for Medicare Part B Drugs,,,\n"
            '"Effective October 1, 2026 through December 31, 2026",,,\n'
            "Note:\xa0Windows byte,,,\n"
            "HCPCS Code,Short Description,HCPCS Code Dosage,Payment Limit\n"
            "J3480,Inj potassium chloride,2 MEQ,0.130\n"
            "J9999,No limit,1 MG,\n")
    (tmp_path / "x Payment Limit File y.csv").write_bytes(text.encode("cp1252"))
    (tmp_path / "Drugs Not Payable Payment Limit File.csv").write_text("ignored")
    asp = load_asp(tmp_path)
    assert asp.effective.startswith("Effective October 1, 2026")
    assert asp.codes == {"J3480": {"description": "Inj potassium chloride", "dosage": "2 MEQ", "limit": 0.13}}
    assert load_asp(tmp_path / "missing") is None


def test_load_opps_finds_header_and_rates(tmp_path):
    import zipfile

    csv_text = ("Addendum B title,,,,\n,,,,\nHCPCS Code,Short Descriptor,SI,APC,Payment Rate\n"
                "96365,x,S,5692,$190.50\n96366,x,N,,\n99999,x,S,1,$5.00\n")
    with zipfile.ZipFile(tmp_path / "july-2026-opps-addendum-b.zip", "w") as z:
        z.writestr("addendum_b.csv", csv_text)
    opps = load_opps(tmp_path, {"96365", "96366"})
    assert opps.codes == {"96365": {"rate": 190.5, "si": "S"}, "96366": {"rate": None, "si": "N"}}
    assert load_opps(tmp_path / "none", {"96365"}) is None


def test_float32_noise_does_not_add_a_unit():
    # MIMIC stores 10 mEq as 10.000000298...; that is 5 units of J3480, not 6.
    [line] = drug_lines([row(225166, "Potassium Chloride", 10.000000298023224, "mEq")], XWALK, ASP)
    assert line["units"] == 5
