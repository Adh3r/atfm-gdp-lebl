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
| R1 | Unpinned dependencies: PuLP ≥ 3 changed its API and the pipeline crashed in WP3. | ✅ versions pinned; PuLP no longer needed (see C2) |
| R2 | 19 of 47 tests were stale and one test file was never collected. | ⏳ WP1 (24), WP2 (21) and WP3 (17) suites rewritten |

## Results

| | Issue | Effect on the report | Status |
|---|---|---|---|
| C1 | The "GDP" column used in the report's cost and CO₂ comparison, and the "Intermodal (GDP)" column of WP4, were the GHP *validation* run with r<sub>f</sub> = 1 (statement: "Task 1: validation task: do not include it in the report"), not the RBS allocation. With uniform weights every permutation of the slots is optimal, so that run's cost, CO₂ and fairness are arbitrary solver output. The validation is now only checked (GHP total delay = RBS total delay), and the GDP columns are the RBS allocation, costed with the same coefficients as the GHP (statement Task 5). | GDP cost and CO₂ in every comparison; the "20.3 %" saving was measured against the wrong baseline | ✅ |
| C2 | The cost-optimal GHP minimised a *linear* proxy (r<sub>f</sub> = Cook cost at 15 min / 15, constant), while results were evaluated on the non-linear cost curve. The statement asks for r<sub>f</sub>(t) to depend on the length of the delay. Because each slot holds one flight, the GHP is a linear assignment problem: it is now solved exactly with `scipy.optimize.linear_sum_assignment` on the true cost matrix c<sub>ft</sub> = cost<sub>f</sub>(t − ETA<sub>f</sub>). It is cross-checked in the tests against Dènia Cabrera's original MILP formulation and against brute force. The pipeline drops from 169 s to 24 s, and PuLP is no longer a dependency. | GHP cost scenario | ✅ |
| C3 | The airline fairness RSD reported (9.77 / 35.58 / 84.81 %) is the RSD of the top-4 airline mean delays. A later refactor replaced it with a per-flight RSD, and the delivered run printed 0 %. The report's definition is restored as `RSD_Aerolineas_%`, next to the statement's per-airline mean delay and RSD. | Fairness KPI | ✅ |
| C4 | WP4 selected the rail-replaceable flights over the whole day and removed all of them, although the statement restricts the study to the regulation interval ("not during 24h"). Its "total system CO₂" subtracted the CO₂ of those 11 flights (69.0 t) from a baseline covering only the regulated window (9 of them, 57.0 t). The direct saving (43 t) also used all 11, while the report speaks of 9. Now only flights affected by the regulation are replaced, and system CO₂ uses one boundary for every scenario: nominal CO₂ of all arrivals flown + delay CO₂ + rail CO₂. | WP4 figures (see results below) | ✅ |
| C5 | The WP2 sensitivity heatmaps charged ground delay at the flight's *cruise* fuel burn, although the code's own documentation (and WP3/WP4) treats an aircraft held at the gate as running on fixed electrical ground power. That put the CO₂ of ground delay at 4,445 t for the baseline GDP, instead of 11.7 t with FEGP. It also made the "CO₂ savings" heatmap numerically identical to the "ground-delay CO₂" heatmap. Both now use the same FEGP model as WP3/WP4 (`cost_model.co2_tierra_kg_por_min`). | WP2: the ground-delay CO₂ heatmap falls about 380×; the CO₂-savings heatmap now differs from it (4,433 t for R = 3000 km, H<sub>file</sub> = 150 min). Air delay, unrecoverable delay and cost heatmaps unchanged. | ✅ |
| C6 | Text vs. code: the report states that the GHP lowers the maximum delay, while its own appendix table shows the opposite. Compression moved 222 flights (the report says 212). WP4 air in-vehicle time uses 440 kt (the report says 400 kt) and each flight's real seats (the report says 180). | Text only | noted |
| C7 | Flight distance was inferred from block time minus taxi time times a RECAT cruise speed. It underestimated short routes badly: Palma (202 km) came out as 0–115 km, and two Vueling flights got 6.8 km and an absurd 4.4 t of CO₂. Madrid flights (483 km) were often above 500 km and so missed the rail filter, although the report describes the filter as "orthodromic distance". Distances are now great-circle, from a coordinates table built from public-domain OurAirports data (`tools/build_airport_table.py`). | WP1: the GDP allocation, delays and costs are unchanged. System CO₂ +1.6 %; delay CO₂ −0.3 %. WP4: 18 rail-replaceable flights in the regulated window instead of 9 (every Madrid flight now qualifies), and the direct rail saving roughly doubles. Final WP4 figures depend on C4. | ✅ |
| C8 | Every arrival from Hstart to midnight was regulated. The course defines controlled flights as those arriving between Hstart and HNoReg; the rest are *NotAffected* and fly as scheduled (WP3 is likewise applied to "the affected flights"). After HNoReg (19:14), 106 flights were still being assigned slots, and 89 of them picked up small delays (154.5 min in total) caused only by slot discretisation. | Affected flights 521 → 415; GDP total delay 35,834.5 → 35,680.0 min; every per-flight average, OTP and fairness figure changes accordingly. The delay of the flights that are actually affected is unchanged. | ✅ |
| C9 | **Wrong cost-of-delay tables.** The code used Tables 11 and 12 of Cook & Tanner's *The cost of passenger delay to airlines in Europe* (2015), labelled "Table 12: total, without reactionary" and "Table 11: hard only, with reactionary". Those tables are **passenger costs only** (Table 11: passenger hard costs; Table 12: passenger hard + soft). Neither includes reactionary delay, fuel, crew or maintenance. Switching to "hard only" at 15 min even made the cost *drop* from 14 to 15 min of delay. The method the report describes — primary cost below the 15-min OTP threshold, full cost with reactionary delay above it — is in the main reference (*European airline delay cost reference values* v4.1, 2015): Tables 22/26 (at gate) and 24/28 (en route), base scenario. These tables are now used: at gate for ground delay, en route for air delay. The cost grows linearly beyond the last breakpoint (300 min). The passenger scaling keeps the Annex 3 base loads, now cited correctly. | Every cost figure. GDP (RBS) 4.33 M€ → 11.83 M€ for the affected flights. The report's "reactionary cost >99 %" came from the mislabelled table. | ✅ |
| C10 | With a correct, non-linear cost and no limit, the optimiser loads delays of up to 10–11 h on a few flights: beyond about 120 min the reactionary component of the Cook & Tanner cost saturates, so each extra minute is cheaper. The statement asks to avoid very high ground delays. A cap of 300 min (the last breakpoint of the cost tables, so every delay stays inside the data) is applied to controlled flights in the GHP (`MAX_GROUND_DELAY_MIN`). A cap below the RBS maximum (224 min) is infeasible, because ETA order already minimises the maximum delay. | Uncapped: GHP cost 8.78 M€ (−25.8 %) with a maximum of 603 min. Capped at 300: 9.19 M€ (−22.3 %). Capped at 240: 9.49 M€ (−19.8 %). | ✅ |
| C11 | **Figure with invented curves.** The WP4 "queue evolution" figure (report Figure 8, "Earlier HNoReg due to HSR substitution") did not plot simulation output. It drew hand-made curves (demand 0.65·t − 0.00018·t², capacity 0.5·t) scaled so that they ended at the two computed HNoReg values. The figure now plots the real Newell queues of the baseline and reduced demand. The two other WP4 figures were rebuilt from the simulation results instead of re-reading the CSVs by their Spanish row labels. | Report Figure 8 | ✅ |

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

### WP3 results after C1–C3 and C8–C10 (real traffic day, 415 affected flights)

| | GDP (RBS) | GHP emissions | GHP cost |
|---|---|---|---|
| Total delay (min) | 35,680 | 35,680 | 35,680 |
| Max delay (min) | 224 | 300 | 300 |
| OTP, delay < 15 min | 18.6 % | 39.5 % | 24.6 % |
| Unrecoverable delay (min) | 1,581 | 1,667 | 1,522 |
| Delay CO₂ (kg) | 30,679 | **29,964** (−2.3 %) | 30,053 |
| Cost of delay (EUR) | 11.83 M | 11.13 M | **9.19 M** (−22.3 %) |
| Airline fairness, RSD of top-4 mean delays | **27.2 %** | 32.9 % | 48.7 % |

The report's qualitative conclusions survive, now against the right baseline
and the right cost data: the GHP lowers the cost (cost objective) or the CO₂
(emissions objective) of the same total delay, at the expense of fairness
between airlines and of a longer maximum delay.

### WP4 results after C4 (real traffic day)

14 flights affected by the regulation have a direct high-speed-rail alternative
(10 Madrid, 3 Alicante, 1 Marseille). Replacing them:

| | GDP (RBS) | Intermodal GDP | GHP cost | Intermodal GHP cost |
|---|---|---|---|---|
| HNoReg | 19:14 | **18:09** (−65 min) | 19:14 | 18:09 |
| Affected flights | 415 | 367 | 415 | 367 |
| Total delay (min) | 35,680 | 30,454 (−14.6 %) | 35,680 | 30,454 |
| Delay CO₂ (kg) | 30,679 | 28,918 | 30,053 | 28,444 |
| Cost of delay (EUR) | 11.83 M | 9.93 M | 9.19 M | **7.77 M** |
| System CO₂ (t) | 12,040.6 | 11,972.5 | 12,040.0 | 11,972.0 |

The system saves about 68 t of CO₂: 66.4 t direct (104.1 t of flights replaced
by 37.7 t of rail) and about 1.7 t less delay CO₂. Door to door, the train beats
the delayed flight only on Madrid (246 min vs 261 min on average).
