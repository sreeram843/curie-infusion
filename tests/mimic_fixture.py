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
225158,NaCl 0.9%,NaCl 0.9%,inputevents,Fluids/Intake,mL,Solution,,
220045,Heart Rate,HR,chartevents,Routine Vital Signs,bpm,Numeric,,
220179,Non Invasive Blood Pressure systolic,NBPs,chartevents,Routine Vital Signs,mmHg,Numeric,,
223762,Temperature Celsius,Temperature C,chartevents,Routine Vital Signs,°C,Numeric,,
228096,Richmond-RAS Scale,Richmond-RAS Scale,chartevents,Pain/Sedation,,Text,,
223849,Ventilator Mode,Ventilator Mode,chartevents,Respiratory,,Text,,
226559,Foley,Foley,outputevents,Output,mL,Numeric,,
227488,GU Irrigant Volume In,GU Irrigant Volume In,outputevents,Output,mL,Numeric,,
224263,Multi Lumen,Multi Lumen,procedureevents,Access Lines - Invasive,None,Process,,
225459,Chest X-Ray,Chest X-Ray,procedureevents,Imaging,None,Process,,
226060,Calories,Calories,ingredientevents,Ingredients,Kcal,Ingredient,,
""",
    "icu/chartevents.csv.gz": """subject_id,hadm_id,stay_id,caregiver_id,charttime,storetime,itemid,value,valuenum,valueuom,warning
1,11,101,9,2150-01-01 10:05:00,2150-01-01 10:06:00,220045,80,80,bpm,0
1,11,101,9,2150-01-01 10:35:00,2150-01-01 10:36:00,220045,90,90,bpm,0
1,11,101,9,2150-01-01 10:40:00,2150-01-01 10:41:00,220045,9999,9999,bpm,0
1,11,101,9,2150-01-01 10:10:00,2150-01-01 10:11:00,220179,120,120,mmHg,0
1,11,101,9,2150-01-01 10:00:00,2150-01-01 10:02:00,223762,37,37,°C,0
1,11,101,9,2150-01-01 11:50:00,2150-01-01 12:30:00,220045,130,130,bpm,0
1,11,101,9,2150-01-01 10:00:00,2150-01-01 10:01:00,228096,-2 Light sedation,-2,,0
1,11,101,9,2150-01-01 10:30:00,2150-01-01 10:31:00,228096,0  Alert and calm,0,,0
1,11,101,9,2150-01-01 10:00:00,2150-01-01 10:01:00,223849,CMV/ASSIST,,,0
""",
    "icu/inputevents.csv.gz": """subject_id,hadm_id,stay_id,caregiver_id,starttime,endtime,storetime,itemid,amount,amountuom,rate,rateuom,orderid,linkorderid,ordercategoryname,secondaryordercategoryname,ordercomponenttypedescription,ordercategorydescription,patientweight,totalamount,totalamountuom,isopenbag,continueinnextdept,statusdescription,originalamount,originalrate
1,11,101,9,2150-01-01 10:30:00,2150-01-01 12:30:00,2150-01-01 10:31:00,225166,20,mEq,50,mL/hour,1,1,01-Drips,,Main order parameter,Continuous Med,80,100,mL,0,0,FinishedRunning,20,50
1,11,101,9,2150-01-01 10:30:00,2150-01-01 12:30:00,2150-01-01 10:31:00,225158,100,mL,50,mL/hour,1,1,01-Drips,,Mixed solution,Continuous Med,80,100,mL,0,0,FinishedRunning,100,50
1,11,101,9,2150-01-01 11:10:00,2150-01-01 11:11:00,2150-01-01 11:12:00,221456,2,grams,,,2,2,05-Med Bolus,,Main order parameter,Drug Push,80,100,mL,0,0,FinishedRunning,2,2
1,11,101,9,2150-01-01 20:00:00,2150-01-01 21:00:00,2150-01-01 20:01:00,225855,1,dose,,,3,3,08-Antibiotics (IV),,Main order parameter,Continuous Med,80,50,mL,0,0,FinishedRunning,1,50
""",
    "hosp/drgcodes.csv.gz": """subject_id,hadm_id,drg_type,drg_code,description,drg_severity,drg_mortality
1,11,HCFA,871,SEPTICEMIA OR SEVERE SEPSIS W/O MV >96 HOURS W MCC,,
1,11,APR,720,SEPTICEMIA & DISSEMINATED INFECTIONS,3,3
""",
    "icu/outputevents.csv.gz": """subject_id,hadm_id,stay_id,caregiver_id,charttime,storetime,itemid,value,valueuom
1,11,101,9,2150-01-01 10:00:00,2150-01-01 10:05:00,226559,200,mL
1,11,101,9,2150-01-01 11:00:00,2150-01-01 11:05:00,226559,150,mL
1,11,101,9,2150-01-01 11:00:00,2150-01-01 11:05:00,227488,50,mL
1,11,101,9,2150-01-01 11:30:00,2150-01-01 13:00:00,226559,999,mL
""",
    "icu/procedureevents.csv.gz": """subject_id,hadm_id,stay_id,caregiver_id,starttime,endtime,storetime,itemid,value,valueuom,location,locationcategory,orderid,linkorderid,ordercategoryname,ordercategorydescription,patientweight,isopenbag,continueinnextdept,statusdescription,originalamount,originalrate
1,11,101,9,2150-01-01 09:00:00,2150-01-03 07:00:00,2150-01-01 09:10:00,224263,2760,min,Right IJ,Invasive Venous,7,7,Invasive Lines,Task,80,0,0,FinishedRunning,2760,0
1,11,101,9,2150-01-01 09:30:00,2150-01-01 09:31:00,2150-01-01 09:40:00,225459,1,None,,,8,8,Imaging,Electrolytes,80,0,0,FinishedRunning,1,0
""",
    "icu/ingredientevents.csv.gz": """subject_id,hadm_id,stay_id,caregiver_id,starttime,endtime,storetime,itemid,amount,amountuom,rate,rateuom,orderid,linkorderid,statusdescription,originalamount,originalrate
1,11,101,9,2150-01-01 10:30:00,2150-01-01 12:30:00,2150-01-01 10:31:00,226060,220,Kcal,,,1,1,FinishedRunning,220,0
""",
    "hosp/admissions.csv.gz": """subject_id,hadm_id,admittime,dischtime,deathtime,admission_type,admit_provider_id,admission_location,discharge_location,insurance,language,marital_status,race,edregtime,edouttime,hospital_expire_flag
1,11,2150-01-01 06:00:00,2150-01-05 12:00:00,,EW EMER.,P1,EMERGENCY ROOM,HOME,Medicare,English,MARRIED,WHITE,2150-01-01 05:00:00,2150-01-01 06:30:00,0
2,22,2150-02-01 06:00:00,2150-02-03 12:00:00,,URGENT,P1,PHYSICIAN REFERRAL,HOME,Other,English,SINGLE,WHITE,,,0
""",
    "hosp/transfers.csv.gz": """subject_id,hadm_id,transfer_id,eventtype,careunit,intime,outtime
1,11,1,ED,Emergency Department,2150-01-01 05:00:00,2150-01-01 08:00:00
1,11,2,admit,MICU,2150-01-01 08:00:00,2150-01-03 08:00:00
1,11,3,transfer,Medicine,2150-01-03 08:00:00,2150-01-05 12:00:00
""",
    "hosp/services.csv.gz": """subject_id,hadm_id,transfertime,prev_service,curr_service
1,11,2150-01-01 06:00:00,,MED
""",
    "hosp/diagnoses_icd.csv.gz": """subject_id,hadm_id,seq_num,icd_code,icd_version
1,11,1,A419,10
""",
    "hosp/d_icd_diagnoses.csv.gz": """icd_code,icd_version,long_title
A419,10,"Sepsis, unspecified organism"
""",
    "hosp/procedures_icd.csv.gz": """subject_id,hadm_id,seq_num,chartdate,icd_code,icd_version
1,11,1,2150-01-01,02HV33Z,10
""",
    "hosp/d_icd_procedures.csv.gz": """icd_code,icd_version,long_title
02HV33Z,10,Insertion of infusion device into superior vena cava
""",
    "hosp/microbiologyevents.csv.gz": """microevent_id,subject_id,hadm_id,micro_specimen_id,order_provider_id,chartdate,charttime,spec_itemid,spec_type_desc,test_seq,storedate,storetime,test_itemid,test_name,org_itemid,org_name,isolate_num,quantity,ab_itemid,ab_name,dilution_text,dilution_comparison,dilution_value,interpretation,comments
1,1,11,500,,2150-01-01 00:00:00,2150-01-01 09:00:00,70012,BLOOD CULTURE,1,2150-01-02 00:00:00,2150-01-02 10:00:00,90201,Blood Culture,80002,ESCHERICHIA COLI,1,,90004,CEFTRIAXONE,<=1,<=,1,S,
2,1,11,500,,2150-01-01 00:00:00,2150-01-01 09:00:00,70012,BLOOD CULTURE,1,2150-01-02 00:00:00,2150-01-02 10:00:00,90201,Blood Culture,80002,ESCHERICHIA COLI,1,,90005,AMPICILLIN,>=32,>=,32,R,
""",
    "hosp/pharmacy.csv.gz": """subject_id,hadm_id,pharmacy_id,poe_id,starttime,stoptime,medication,proc_type,status,entertime,verifiedtime,route,frequency,disp_sched,infusion_type,sliding_scale,lockout_interval,basal_rate,one_hr_max,doses_per_24_hrs,duration,duration_interval,expiration_value,expiration_unit,expirationdate,dispensation,fill_quantity
1,11,900,1-1,2150-01-01 10:00:00,2150-01-03 10:00:00,Ceftriaxone,IV Piggyback,Discontinued via patient discharge,2150-01-01 09:50:00,2150-01-01 09:55:00,IV,Q24H,,,,,,,1,,,,,,Omnicell,
1,11,901,1-2,2150-01-02 10:00:00,2150-01-04 10:00:00,Famotidine,Unit Dose,Discontinued,2150-01-02 09:00:00,2150-01-02 09:05:00,IV,Q12H,,,,,,,2,,,,,,Omnicell,
""",
    "hosp/prescriptions.csv.gz": """subject_id,hadm_id,pharmacy_id,poe_id,poe_seq,order_provider_id,starttime,stoptime,drug_type,drug,formulary_drug_cd,gsn,ndc,prod_strength,form_rx,dose_val_rx,dose_unit_rx,form_val_disp,form_unit_disp,doses_per_24_hrs,route
1,11,900,1-1,1,P1,2150-01-01 10:00:00,2150-01-03 10:00:00,MAIN,CefTRIAXone,CEFT1,1,1,1g Vial,,1,g,1,VIAL,1,IV
""",
    "hosp/emar.csv.gz": """subject_id,hadm_id,emar_id,emar_seq,poe_id,pharmacy_id,enter_provider_id,charttime,medication,event_txt,scheduletime,storetime
1,11,1-1,1,1-1,900,,2150-01-01 10:05:00,Ceftriaxone,Administered,2150-01-01 10:00:00,2150-01-01 10:06:00
1,11,1-2,2,1-1,900,,2150-01-02 10:05:00,Ceftriaxone,Not Given,2150-01-02 10:00:00,2150-01-02 10:06:00
""",
    "hosp/emar_detail.csv.gz": """subject_id,emar_id,emar_seq,parent_field_ordinal,administration_type,pharmacy_id,barcode_type,reason_for_no_barcode,complete_dose_not_given,dose_due,dose_due_unit,dose_given,dose_given_unit,will_remainder_of_dose_be_given,product_amount_given,product_unit,product_code,product_description,product_description_other,prior_infusion_rate,infusion_rate,infusion_rate_adjustment,infusion_rate_adjustment_amount,infusion_rate_unit,route,infusion_complete,completion_interval,new_iv_bag_hung,continued_infusion_in_other_location,restart_interval,side,site,non_formulary_visual_verification
1,1-1,1,1.1,,900,if,,,,,1,g,,1,VIAL,CEFT1,Ceftriaxone 1 g Vial,,,,,,,IV,,,,,,,,
1,1-1,1,,IV Antibiotic,,,,No,1,g,,,,,,,,,,,,,,,,,,,,,,
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
