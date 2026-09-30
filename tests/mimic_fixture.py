"""Tiny synthetic files in the MIMIC-IV 3.1 layout. Invented values; no patient data."""

import gzip
from pathlib import Path

FILES = {
    "icu/icustays.csv.gz": """subject_id,hadm_id,stay_id,first_careunit,last_careunit,intime,outtime,los
1,11,101,MICU,MICU,2150-01-01 08:00:00,2150-01-03 08:00:00,2.0
2,22,202,SICU,SICU,2150-02-01 08:00:00,2150-02-02 08:00:00,1.0
""",
    "hosp/patients.csv.gz": """subject_id,gender,anchor_age,anchor_year,anchor_year_group,dod
1,F,60,2150,2017 - 2019,
2,M,70,2150,2017 - 2019,
""",
    "icu/d_items.csv.gz": """itemid,label,abbreviation,linksto,category,unitname,param_type,lownormalvalue,highnormalvalue
225166,Potassium Chloride,KCL,inputevents,Medications,mEq,Solution,,
225855,Ceftriaxone,Ceftriaxone,inputevents,Antibiotics,dose,Solution,,
221456,Calcium Gluconate,Calcium Gluconate,inputevents,Medications,grams,Solution,,
""",
    "icu/inputevents.csv.gz": """subject_id,hadm_id,stay_id,caregiver_id,starttime,endtime,storetime,itemid,amount,amountuom,rate,rateuom,orderid,linkorderid,ordercategoryname,secondaryordercategoryname,ordercomponenttypedescription,ordercategorydescription,patientweight,totalamount,totalamountuom,isopenbag,continueinnextdept,statusdescription,originalamount,originalrate
1,11,101,9,2150-01-01 10:30:00,2150-01-01 12:30:00,2150-01-01 10:31:00,225166,20,mEq,50,mL/hour,1,1,02-Fluids,,Main order parameter,Continuous Med,80,100,mL,0,0,FinishedRunning,20,50
1,11,101,9,2150-01-01 11:10:00,2150-01-01 11:11:00,2150-01-01 11:12:00,221456,2,grams,,,2,2,05-Med,,Main order parameter,Drug Push,80,100,mL,0,0,FinishedRunning,2,2
1,11,101,9,2150-01-01 20:00:00,2150-01-01 21:00:00,2150-01-01 20:01:00,225855,1,dose,,,3,3,08-Antibiotics,,Main order parameter,Continuous Med,80,50,mL,0,0,FinishedRunning,1,50
""",
    "hosp/d_labitems.csv.gz": """itemid,label,fluid,category
50971,Potassium,Blood,Chemistry
50983,Sodium,Blood,Chemistry
""",
    "hosp/labevents.csv.gz": """labevent_id,subject_id,hadm_id,specimen_id,itemid,order_provider_id,charttime,storetime,value,valuenum,valueuom,ref_range_lower,ref_range_upper,flag,priority,comments
1,1,11,1,50971,,2150-01-01 10:00:00,2150-01-01 10:40:00,6.2,6.2,mEq/L,3.5,5.1,abnormal,STAT,
2,1,11,1,50983,,2150-01-01 10:00:00,2150-01-01 10:40:00,140,140,mEq/L,135,145,,STAT,
3,1,11,2,50971,,2150-01-01 11:50:00,2150-01-01 13:00:00,4.1,4.1,mEq/L,3.5,5.1,,STAT,
""",
}


def write_mimic(root: Path) -> Path:
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt") as f:
            f.write(text)
    return root
