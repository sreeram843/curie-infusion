# Product Requirements Document: SmartInfuse Audit & Safety Engine

**Working title.** Repository: `curie-infusion` · **Status:** Draft

## Project overview

- **Target audience:** ICU clinicians, hospital pharmacists, clinical risk auditors.
- **Core value proposition:** A portable, FHIR-native clinical decision support application that tracks
  real-time infusion metrics and flags absolute co-infusion and clinical contraindications to reduce
  high-alert medication errors.

## 1. Objectives and success metrics

### 1.1 Goals

- Provide real-time and retrospective dashboards of continuous medication administration totals.
- Intercept unsafe concurrent infusions (drug-drug and drug-line incompatibilities) before or during delivery.
- Use structured clinical context (labs, vital signs, diagnoses) to generate actionable
  "What Not to Infuse" safety summaries.

### 1.2 Success metrics

- **Zero missing administrations:** 100% reconciliation between medical orders (`MedicationRequest`) and
  active line deliveries (`MedicationAdministration`).
- **Incompatibility catch rate:** 100% of defined absolute co-infusion contraindications flagged within
  <2 seconds of a state change.
- **Low alert fatigue:** a false-positive alarm rate below 5%, achieved by clinical context filters
  (for example, active lab trends instead of static generic alerts).

## 2. Personas and user stories

### 2.1 Personas

- **ICU charge nurse:** needs an at-a-glance view of running continuous titrations, and an immediate
  warning if an ordered drip conflicts with an active line.
- **Clinical pharmacist / quality auditor:** needs a retrospective summary of cumulative doses given
  over a shift, plus any near-miss contraindication overrides.

### 2.2 User stories

- As an ICU nurse, I want the application to monitor all running IV channels at once, so that I am
  warned instantly if two incompatible drugs are about to run on the same line or concurrently.
- As an auditor, I want an exact calculation of the milligrams or milliequivalents of high-alert
  medications infused during a specific time window.
- As a clinician, I want an AI-generated summary of which medications are contraindicated given the
  patient's changing lab values (for example, stopping potassium drips if serum potassium spikes).

## 3. Functional requirements

### 3.1 Infusion and dose tracking

- **Cumulative volume and mass:** parse rate changes over time to compute exact medication totals.
- **Titration string mapping:** group individual rate adjustments under one parent order to track the
  full lifecycle of a continuous drip.

### 3.2 Contraindication engine ("What Not to Infuse")

- **Co-infusion incompatibility:** flag drugs that cannot run at the same time or through the same lumen.
- **Clinical context cross-referencing:** update the list of blocked medications from the patient's
  current physiology.

### 3.3 Feature matrix

| Feature | Description | Priority | v0.1 |
|---|---|---|---|
| Real-time dose ledger | Cumulative totals from infusion `rate` over elapsed `effectivePeriod`. | P0 | ✅ `ledger.py` (incl. MIMIC units, weight-based, bolus) |
| Hourly / daily / weekly grid | Per-patient amounts delivered per drug per time bucket. | P0 | ✅ v0.2 `mimic/store.py` + UI |
| Co-infusion conflict matrix | Active drug codes checked against a drug-drug / Y-site incompatibility table. | P0 | ✅ engine; illustrative table only |
| Physiological safety blocks | "Do Not Infuse" when current labs or diagnoses conflict. | P0 | ✅ labs, conditions, allergies |
| "Do not start" list | Every drug in the rule table checked against current physiology, even if not ordered. | P0 | ✅ v0.2 `blocked_medications` |
| AI contraindication summary | Natural-language explanation of why an infusion is flagged, citing data points. | P1 | ✅ v0.2 local LM Studio, validated |
| Audit ledger export | Reports of infusions, titrations, and cleared flags for pharmacy audits. | P1 | JSON state only |

## 4. Technical architecture and FHIR mapping

### 4.1 Ingestion mapping

```
[FHIR resources] ──► [ingestion] ──► [deterministic safety rules] ──► (optional) LLM summary
```

- `MedicationAdministration` (P0): live execution, start/stop times (`effective[x]`), and rate.
- `MedicationRequest` (P0): intent, parameters, and hard-stop boundaries set by the provider.
- `Observation` (P0): lab values (LOINC serum chemistry) and vitals for physiological rules.
- `Condition` and `AllergyIntolerance` (P0): active problems (ICD-10/SNOMED) and drug allergies.

### 4.2 Data flow example

1. Fetch `MedicationAdministration` resources where `status = in-progress`.
2. Match the drug's ingredients (`Medication.ingredient.itemCodeableConcept`) against active
   `Condition`s and `Observation` results.
3. If, for example, serum potassium is >5.5 mEq/L and an active medication contains potassium
   chloride, raise a `do_not_infuse` flag.

## 5. Non-functional requirements

### 5.1 Performance

- A new FHIR payload triggers safety evaluation with total processing latency under 500 ms.
- Safety alert banners render in the browser within 200 ms of the backend state being generated.

### 5.2 Regulatory and privacy

- Data in transit uses TLS 1.3; data at rest uses AES-256.
- Every automated alert, and every clinician acknowledgment or override, is written to an append-only ledger.
- Any AI-generated summary is shown with citations of the rules and source data behind it.

## 6. Curie alignment decisions (v0.1)

These change or clarify the draft above so it matches how the other Curie projects work.

1. **The AI does not decide flags.** §4 originally called the rules an "AI Safety Logic Matrix".
   In Curie, deterministic rules raise every flag, and an LLM only writes the P1 summary for a flag
   that already exists (the same rule as `curie-prediction-pipeline`).
2. **RxNorm has no compatibility data.** RxNorm identifies drugs; it does not say which drugs are
   Y-site compatible. That data comes from licensed references (for example Trissel's, King Guide,
   or Micromedex). v0.1 ships a small **illustrative** table on a **synthetic** drug code system.
   Real content needs a licensing decision.
3. **Flag, don't physically block.** The app cannot stop a pump. "Absolute Block" becomes the
   `do_not_infuse` status.
4. **Missing data is shown, not hidden.** A stale, missing, not-yet-available, or wrong-unit lab
   result produces `insufficient_context`. It never silently passes a rule.
5. **Line/lumen needs an extension.** FHIR R4 has no standard field for it, so v0.1 uses a
   project-local extension. When the line is unknown for a same-line rule, the result is `review`.
6. **Claims language.** "Eliminate errors" becomes "reduce". The 100% catch rate only holds for the
   rules we define, not for real-world safety. The <5% false-positive target needs a labeled dataset.
   MIMIC-IV `inputevents` (rates, `linkorderid` titration links) plus `labevents` is the natural
   candidate, as with the pipeline's Stage B work.
7. **Reuse the audit plane.** The append-only alert/override ledger in §5.2 should be sent to
   `curie-audit-plane` rather than built again here.
8. **TLS/AES and HIPAA** are deployment concerns, out of scope for the synthetic prototype.

## 7. v0.2 scope changes (2026-09-30, approved by the project owner)

- **MIMIC-IV 3.1 is in scope** as the data source, used locally only. ICU `inputevents` is replayed
  in time order to stand in for the pump feed; a `datetime` "pump clock" (`as_of`) hides everything
  after it. Note: `inputevents` is nurse-verified charting, not raw pump telemetry.
- **A lightweight UI is in scope:** FastAPI + one static HTML page (`curie-infusion serve`).
- **The LLM summary runs on a local LM Studio server** so MIMIC data never leaves the machine.
- **Weight-based rates and bolus doses** are now handled by the ledger.

## 8. Non-goals for v0.2

Live pump connectivity (the seam is FHIR `MedicationAdministration`; a PCD-01 gateway via `curie-fhir`
would feed it), line/lumen data (MIMIC has none, so same-line rules return `review`), hospital-ward
`emar` administrations, real drug codes, and any claim of clinical validity.
