# Changes vs. the submitted report

The project was delivered in May 2026 (code and report). Re-running it in
November 2026 reproduced every delivered table exactly, except the airline
fairness figure. A review then found the issues below. They are corrected in this
repository, and each correction is listed with its effect on the published
numbers.

Status legend: ⏳ pending · ✅ fixed

## Reproducibility

| | Issue | Status |
|---|---|---|
| R1 | Unpinned dependencies: PuLP ≥ 3 changed its API and the pipeline crashed in WP3. | ✅ pinned (`requirements.txt`) |
| R2 | 19 of 47 tests were stale and one test file was never collected. | ⏳ WP1 (24) and WP2 (18) suites rewritten |

## Results

| | Issue | Effect on the report | Status |
|---|---|---|---|
| C1 | The "GDP" column used in the report's cost and CO₂ comparison is the GHP validation run with r = 1. With uniform weights every slot permutation is optimal, so that run's cost, CO₂ and fairness are arbitrary. The real RBS allocation costs 4.33 M€, not 5.61 M€. | The headline "GHP reduces cost by 20.3 %" does not hold against RBS | ⏳ |
| C2 | The cost-optimal GHP minimised a *linear* proxy (the Cook cost at 15 min, per minute), while results were evaluated on the convex Cook & Tanner curve. The optimiser concentrated very long delays on a few flights (maximum 674 min, against 224 min under RBS). | GHP cost scenario | ⏳ |
| C3 | The airline fairness RSD reported (9.77 / 35.58 / 84.81 %) is the RSD of the top-4 airline mean delays. A later refactor replaced it with a per-flight RSD, and the delivered run printed 0 %. | Fairness KPI | ⏳ |
| C4 | WP4 "total system CO₂" subtracts the CO₂ of the 11 rail-replaceable flights of the whole day (69.0 t) from a baseline that only covers the regulated window (9 of those flights, 57.0 t). The direct rail saving (43 t) also uses all 11 flights, while the text speaks of 9. | WP4 CO₂ figures | ⏳ |
| C5 | The WP2 sensitivity heatmaps charged ground delay at the flight's *cruise* fuel burn, although the code's own documentation (and WP3/WP4) treats an aircraft held at the gate as running on fixed electrical ground power. That put the CO₂ of ground delay at 4,445 t for the baseline GDP, instead of 11.7 t with FEGP. It also made the "CO₂ savings" heatmap numerically identical to the "ground-delay CO₂" heatmap. Both now use the same FEGP model as WP3/WP4 (`cost_model.co2_tierra_kg_por_min`). | WP2: the ground-delay CO₂ heatmap falls about 380×; the CO₂-savings heatmap now differs from it (4,433 t for R = 3000 km, H<sub>file</sub> = 150 min). Air delay, unrecoverable delay and cost heatmaps unchanged. | ✅ |
| C6 | The report states that the GHP lowers the maximum delay; its own appendix table shows the opposite. Compression moved 222 flights (the report says 212). | Text only | ⏳ |
| C7 | Flight distance was inferred from block time minus taxi time times a RECAT cruise speed. It underestimated short routes badly: Palma (202 km) came out as 0–115 km, and two Vueling flights got 6.8 km and an absurd 4.4 t of CO₂. Madrid flights (483 km) were often above 500 km and so missed the rail filter, although the report describes the filter as "orthodromic distance". Distances are now great-circle, from a coordinates table built from public-domain OurAirports data (`tools/build_airport_table.py`). | WP1: the GDP allocation, delays and costs are unchanged. System CO₂ +1.6 %; delay CO₂ −0.3 %. WP4: 18 rail-replaceable flights in the regulated window instead of 9 (every Madrid flight now qualifies), and the direct rail saving roughly doubles. Final WP4 figures depend on C4. | ✅ |
| C8 | Every arrival from Hstart to midnight was regulated. The course defines controlled flights as those arriving between Hstart and HNoReg; the rest are *NotAffected* and fly as scheduled (WP3 is likewise applied to "the affected flights"). After HNoReg (19:14), 106 flights were still being assigned slots, and 89 of them picked up small delays (154.5 min in total) caused only by slot discretisation. | Affected flights 521 → 415; GDP total delay 35,834.5 → 35,680.0 min; every per-flight average, OTP and fairness figure changes accordingly. The delay of the flights that are actually affected is unchanged. | ✅ |

C4 quotes the figures obtained with the original distance model.

### WP1 data handling (no effect on results)

- Times that cannot be parsed now raise an error instead of silently becoming 00:00.
- The trailing blank rows of the fleet table are dropped on load, and conflicting entries for the same aircraft type are rejected.
- Callsigns that are not ICAO airline flights (one business jet using its registration, N818CA) are labelled `GA` instead of a fake airline "N81".
- The flight list is sorted with a stable sort, so ties in ETA keep a deterministic order.

### WP2 engine (no effect on the published results)

- **Newell capacity curve.** The running capacity counter was not capped at
  cumulative demand while the LVP was active, so capacity left idle during a
  lull would have been "banked" and released later. On 10 August the queue
  never empties during the regulation, so the results are unchanged, but the
  model is now correct for any demand profile. ETAs with seconds are binned to
  their minute instead of being dropped from the demand curve.
- **Missing slots raise.** If the queue does not clear before midnight, the
  run stops with a clear error instead of returning flights without a slot.
- **Naming.** The exemption parameter called `h_noreg` was actually the GDP
  file-time offset (H<sub>file</sub>); it is now `h_file_offset`.
- **Single implementation** of the unrecoverable-delay rule and of the
  ground-CO₂ rate, shared by WP2 and the metrics engine.
- **Speed.** Penalty-and-compression ran inside every one of the 42
  sensitivity simulations and the WP4 re-simulation without being used; it now
  runs once. Full pipeline 208 s → 169 s.

## Terminology

Names were checked against the course material (GDP and GHP lectures, project
statement WP1–WP5) and aligned with it. A glossary with the European ATFM
equivalents is in the README.

| Before | After | Why |
|---|---|---|
| Status labels `GDP CANDIDATE`, `EXEMPT AIRBORNE`, `EXEMPT DISTANCE`, `EXEMPT INTERNATIONAL` | `Controlled`, `ExemptFlying`, `ExemptRadius`, `ExemptInternational` (+ `NotAffected`) | The flight-status outputs named in the course (WP2, "determine flight status"). The report calls controlled flights "GDP CANDIDATES". |
| `AAR`: "Aircraft Arrival Rate"; `PAAR`: "arrival rate while LVP" | Airport Acceptance Rate / (GDP) Program Airport Acceptance Rate | Course definitions. |
| `H_NOREG` described as "minutes window before CTOT"; exemption argument `h_noreg` | `H_FILE_OFFSET` = Hstart − Hfile; argument `h_file_offset`; `CFG.H_FILE` | Hfile is the file time; HNoReg is the computed end of the regulation. The two were conflated. |
| "Freeze horizon HFile", heatmap axis "T File Offset" | file time, axis "Hstart − Hfile (min)" | Course wording ("file time (Hfile)"). |
| "CTD (Calculated Take-Off Time)" | CTD = Controlled Time of Departure (EDCT); European equivalent CTOT | The course defines CTD/CTA as controlled times; "Calculated Take-Off Time" is the CTOT. |
| "RBS is EUROCONTROL's official algorithm" | RBS is the FAA/CDM GDP rule taught in the course; EUROCONTROL uses CASA | Factual correction. |
| — | Output columns `CTA` and `CTD` | Course WP2 asks for the CTA of every flight. |
