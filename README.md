# Curie Infusion

A FHIR-native infusion dose ledger and "what not to infuse" safety engine (working title in the PRD:
*SmartInfuse Audit & Safety Engine*).

> **Prototype only.** Illustrative rules on synthetic drug codes, replayed against MIMIC-IV 3.1 data
> kept on this machine. Not clinically validated, not FDA-cleared, not for patient care.

## What it does (v0.1)

Given one patient's FHIR R4 `Bundle` and an `as_of` time:

- **Dose ledger:** rate × elapsed time for every `MedicationAdministration` segment. Titrations are
  grouped under their parent `MedicationRequest`, and volume rates are converted to drug amount
  using `Medication.ingredient.strength`.
- **Reconciliation:** lists active orders with no administration, and administrations with no order.
- **Safety flags:** deterministic rules for lab thresholds, active conditions, allergies, and
  co-infusion/Y-site pairs. Each flag carries its evidence references and a citation.

No LLM is on the flag-raising path. The LLM summary may only explain flags that have already been
raised, and its output is rejected unless it matches those flags one-to-one.

## MIMIC-IV app (v0.2)

A local web app that replays one ICU stay from MIMIC-IV 3.1 as if the pumps were streaming into it:

- **Overview** (default tab): stat tiles with 24 h sparklines (safety now, HR, BP, SpO2, RR, temperature,
  net fluid, running infusions) and one interactive timeline on a shared time axis: a safety-flag lane
  (the rules re-run across the range, so you see when a flag appeared), infusion swimlanes (bar height =
  rate relative to that drug's peak, ◇ = bolus), vital-sign panels, and key-lab lanes. Hover for one
  tooltip with everything at that moment; click to move the pump clock there. Range: 12 h, 24 h,
  3 days, or the whole stay. Hand-written SVG, no chart library or CDN, so it works offline.
- **Pump-clock controls:** a scrubber across the stay, ←/→ (1 h), Shift+←/→ (6 h), Space to replay.
- **Grid** of every infusion and medication given, per drug, by **hour, day, or week**.
- **Vital signs** (heart rate, systolic/diastolic/mean BP from arterial line or cuff, respiratory
  rate, SpO2, temperature in °F) at the top of the grid as the median per hour/day/week; click a
  cell for every reading, its source, and its charting delay. The safety panel lists the latest
  vitals charted by the pump clock. Impossible values (e.g. HR 9999) are dropped at build time.
- **Click any cell** to see the charted events behind it: start/end, rate, how much of each event fell
  in that hour/day/week, the rest of the same bag (e.g. the carrier fluid of an additive), order,
  weight, and charting delay. Shares always sum to the cell value.
- **Data tabs** for the selected stay, all on the same pump clock (nothing is shown before it was
  known: results by their resulted/charted time, running things without their end):
  - *Fluid balance*: intake by route, output by source, net and cumulative balance.
  - *Labs*: every result for the admission, last value per period, red when the lab flagged it.
  - *Assessments*: RASS, GCS, pain, ventilator mode/FiO2/PEEP/tidal volume, daily weight.
  - *Nutrition*: calories, water, protein, etc. delivered by infusions and feeds.
  - *Orders*: pharmacy orders with status at the clock, prescribed dose, and given / not given from eMAR.
  - *eMAR*: barcode-scanned administrations across the admission (not every admission has eMAR).
  - *Microbiology*: specimens, pending until resulted, organisms and susceptibilities.
  - *Lines & procedures*: lines (with location), dialysis, imaging, ventilation; ongoing ones without an end.
  - *Journey & diagnoses*: ED → units → services; ICD diagnoses/procedures and DRGs are coded at
    discharge, so they stay hidden before discharge unless you turn on "hindsight".
  Grid tabs have clickable cells; list tabs have a filter and clickable rows.
- **Pump clock** you can step or replay; nothing after it is shown or used (a drip still running at
  the clock shows "running" and the amount delivered so far, not its eventual end or total).
- **Current-hour safety panel:** flags on what is running, a **"do not start"** list checked against
  current labs, the latest available labs, and a plain-language **AI summary** from a local
  LM Studio model.

```bash
uv pip install -e ".[app,dev]"
.venv/bin/curie-infusion mimic-build ~/AI/mimiciv-v3.1/mimiciv/3.1   # once, a few minutes -> data/mimic/
.venv/bin/curie-infusion serve                                        # http://127.0.0.1:8765
```

### Billing page (`/billing`)

Estimated charges for a stay at CMS reference prices, by day and by line item, with a drill-down to
the charted events behind each drug line.

- **Drugs:** MIMIC item → HCPCS code via `billing/hcpcs_crosswalk.v0.1.json`; billing units derived
  from the CMS dosage descriptor (e.g. `2 MEQ`), rounded up per order per day; priced at the CMS
  Part B payment limit (ASP + 6%). Lines that cannot be priced say why (charted as "dose", no CMS
  limit, concentration not recorded).
- **Administration:** simplified per-day facility rules for the 96360–96376 family (infusion hours,
  sequential/concurrent drugs, IV push, hydration). Shown as units until an OPPS Addendum B file is
  present, then priced; packaged codes are marked.
- **Context:** the admission's MS-DRG / APR-DRG. Medicare pays inpatient stays per DRG, so these
  are reference estimates, not the hospital's charges.

Price files live in `data/cms/` (gitignored): the CMS Part B payment limit zip (public), and
optionally the OPPS Addendum B zip, which you download yourself because cms.gov gates it behind the
AMA CPT license. New files are picked up without a restart. Set `CURIE_CMS_DIR` to move them.

Set `CURIE_LLM_URL` (default `http://127.0.0.1:1234`) and optionally `CURIE_LLM_MODEL`. Without a
reachable model the panel shows the rule text instead. `data/` is gitignored; never commit it.

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/curie-infusion evaluate fixtures/icu_bundle.json --as-of 2026-01-01T10:00:00+00:00
```

## Where everything lives

| Want to know… | Read |
|---|---|
| Scope, requirements, and Curie alignment decisions | [`docs/PRD.md`](docs/PRD.md) |
| How it is built and how it fits the Curie family | [`docs/architecture.md`](docs/architecture.md) |
| Domain vocabulary | [`CONTEXT.md`](CONTEXT.md) |
| Rules for agents working in this repo | [`AGENTS.md`](AGENTS.md) |
| The rule table | [`src/curie_infusion/rulesets/`](src/curie_infusion/rulesets/) |
