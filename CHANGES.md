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
| R2 | 19 of 47 tests were stale and one test file was never collected. | ✅ 89 tests, all rewritten for pytest; run in CI on the synthetic day |

## Results

| | Issue | Effect on the report | Status |
|---|---|---|---|
| C1 | The "GDP" column used in the report's cost and CO₂ comparison, and the "Intermodal (GDP)" column of WP4, were the GHP *validation* run with r<sub>f</sub> = 1 (statement: "Task 1: validation task: do not include it in the report"), not the RBS allocation. With uniform weights every permutation of the slots is optimal, so that run's cost, CO₂ and fairness are arbitrary solver output. The validation is now only checked (GHP total delay = RBS total delay), and the GDP columns are the RBS allocation, costed with the same coefficients as the GHP (statement Task 5). | GDP cost and CO₂ in every comparison; the "20.3 %" saving was measured against the wrong baseline | ✅ |
| C2 | The cost-optimal GHP minimised a *linear* proxy (r<sub>f</sub> = Cook cost at 15 min / 15, constant), while results were evaluated on the non-linear cost curve. The statement asks for r<sub>f</sub>(t) to depend on the length of the delay. Because each slot holds one flight, the GHP is a linear assignment problem: it is now solved exactly with `scipy.optimize.linear_sum_assignment` on the true cost matrix c<sub>ft</sub> = cost<sub>f</sub>(t − ETA<sub>f</sub>). It is cross-checked in the tests against Dènia Cabrera's original MILP formulation and against brute force. The pipeline drops from 169 s to 24 s, and PuLP is no longer a dependency. | GHP cost scenario | ✅ |
| C3 | The airline fairness RSD reported (9.77 / 35.58 / 84.81 %) is the RSD of the top-4 airline mean delays. A later refactor replaced it with a per-flight RSD, and the delivered run printed 0 %. The report's definition is restored as `RSD_Aerolineas_%`, next to the statement's per-airline mean delay and RSD. | Fairness KPI | ✅ |
| C4 | WP4 selected the rail-replaceable flights over the whole day and removed all of them, although the statement restricts the study to the regulation interval ("not during 24h"). Its "total system CO₂" subtracted the CO₂ of those 11 flights (69.0 t) from a baseline covering only the regulated window (9 of them, 57.0 t). The direct saving (43 t) also used all 11, while the report speaks of 9. Now only flights affected by the regulation are replaced, and system CO₂ uses one boundary for every scenario: nominal CO₂ of all arrivals flown + delay CO₂ + rail CO₂. | WP4 figures (see results below) | ✅ |
| C5 | The WP2 sensitivity heatmaps charged ground delay at the flight's *cruise* fuel burn, although the code's own documentation (and WP3/WP4) treats an aircraft held at the gate as running on fixed electrical ground power. That put the CO₂ of ground delay at 4,445 t for the baseline GDP, instead of 11.7 t with FEGP. It also made the "CO₂ savings" heatmap numerically identical to the "ground-delay CO₂" heatmap. Both now use the same FEGP model as WP3/WP4 (`cost_model.co2_tierra_kg_por_min`, later replaced by the APU model of C17). | WP2: the ground-delay CO₂ heatmap falls about 380×; the CO₂-savings heatmap now differs from it (4,433 t for R = 3000 km, H<sub>file</sub> = 150 min). Air delay, unrecoverable delay and cost heatmaps unchanged. | ✅ |
| C6 | Text vs. code: the report states that the GHP lowers the maximum delay, while its own appendix table shows the opposite. Compression moved 222 flights (the report says 212). WP4 air in-vehicle time uses 440 kt (the report says 400 kt) and each flight's real seats (the report says 180). | Text only | noted |
| C7 | Flight distance was inferred from block time minus taxi time times a RECAT cruise speed. It underestimated short routes badly: Palma (202 km) came out as 0–115 km, and two Vueling flights got 6.8 km and an absurd 4.4 t of CO₂. Madrid flights (483 km) were often above 500 km and so missed the rail filter, although the report describes the filter as "orthodromic distance". Distances are now great-circle, from a coordinates table built from public-domain OurAirports data (`tools/build_airport_table.py`). | WP1: the GDP allocation, delays and costs are unchanged. System CO₂ +1.6 %; delay CO₂ −0.3 %. WP4: 18 rail-replaceable flights in the regulated window instead of 9 (every Madrid flight now qualifies), and the direct rail saving roughly doubles. Final WP4 figures depend on C4. | ✅ |
| C8 | Every arrival from Hstart to midnight was regulated. The course defines controlled flights as those arriving between Hstart and HNoReg; the rest are *NotAffected* and fly as scheduled (WP3 is likewise applied to "the affected flights"). After HNoReg (19:14), 106 flights were still being assigned slots, and 89 of them picked up small delays (154.5 min in total) caused only by slot discretisation. | Affected flights 521 → 415; GDP total delay 35,834.5 → 35,680.0 min; every per-flight average, OTP and fairness figure changes accordingly. The delay of the flights that are actually affected is unchanged. | ✅ |
| C9 | **Wrong cost-of-delay tables.** The code used Tables 11 and 12 of Cook & Tanner's *The cost of passenger delay to airlines in Europe* (2015), labelled "Table 12: total, without reactionary" and "Table 11: hard only, with reactionary". Those tables are **passenger costs only** (Table 11: passenger hard costs; Table 12: passenger hard + soft). Neither includes reactionary delay, fuel, crew or maintenance. Switching to "hard only" at 15 min even made the cost *drop* from 14 to 15 min of delay. The method the report describes — primary cost below the 15-min OTP threshold, full cost with reactionary delay above it — is in the main reference (*European airline delay cost reference values* v4.1, 2015): Tables 22/26 (at gate) and 24/28 (en route), base scenario. These tables are now used: at gate for ground delay, en route for air delay. The cost grows linearly beyond the last breakpoint (300 min). The passenger scaling keeps the Annex 3 base loads, now cited correctly. | Every cost figure. With the "full" table for every delay above 15 min, GDP (RBS) 4.33 M€ → 11.83 M€. The report's "reactionary cost >99 %" came from the mislabelled table. The reactionary part is refined in C13. | ✅ |
| C10 | With a correct, non-linear cost and no limit, the optimiser loads delays of up to 10–11 h on a few flights: beyond about 120 min the reactionary component of the Cook & Tanner cost saturates, so each extra minute is cheaper. The statement asks to avoid very high ground delays. A cap of 300 min (the last breakpoint of the cost tables, so every delay stays inside the data) is applied to controlled flights in the GHP (`MAX_GROUND_DELAY_MIN`). A cap below the RBS maximum (224 min) is infeasible, because ETA order already minimises the maximum delay. | With the C13 cost model. Uncapped: GHP cost 6.31 M€ (−25.5 %) with a maximum of 638 min. Capped at 300: 6.62 M€ (−21.7 %). Capped at 240: 7.01 M€ (−17.1 %). | ✅ |
| C11 | **Figure with invented curves.** The WP4 "queue evolution" figure (report Figure 8, "Earlier HNoReg due to HSR substitution") did not plot simulation output. It drew hand-made curves (demand 0.65·t − 0.00018·t², capacity 0.5·t) scaled so that they ended at the two computed HNoReg values. The figure now plots the real Newell queues of the baseline and reduced demand. The two other WP4 figures were rebuilt from the simulation results instead of re-reading the CSVs by their Spanish row labels. | Report Figure 8 | ✅ |
| C12 | The penalty-and-compression results (WP2) were computed but never exported: the Excel sheet built in March was dropped in a May refactor, which left `df_res_comprimido` unused. In the original sheet, "minutes saved" compared against a label that was never assigned, so it was always 0. The sheet is restored, with rows matched by index (two callsigns are duplicated in the traffic file). | Compression: 12 flights cancelled (6 Vueling), 222 moved earlier, 4,100 min saved | ✅ |
| C13 | **Reactionary delay ignored the aircraft rotations.** The WP3 statement asks to "make a difference between short and long delays: costs of flights reaching OTP should be low, whereas long delays create reactionary delays", and to compute, "using the flight registration mark (RM), the turn-around time between arrival and following departure of the same aircraft". The April code did that, but a later commit replaced it with a fixed 65/100-min turnaround on the premise that "the CSV only contains arrivals". The file holds 551 departures from LEBL with their registrations. The fallback value was never used either: every delay above 15 min got Cook & Tanner's *network-average* reactionary cost. The report justified this with "real TAT in most cases is higher than the observed delays", but the median scheduled turnaround is 85 min and the mean delay 86 min. Now 396 of the 415 affected arrivals are paired with the next departure of the same aircraft. The delay above the turnaround buffer (scheduled ground time − minimum turnaround time: 35 min narrow-body, from the E190 operations-manual summary provided in the course, "MIN declared GND time 35 min; delay code 93 if less time available"; 80 min wide-body) is the delay passed on to the next rotation (IATA code 93). The cost is Cook & Tanner's primary cost (Tables 22/24) plus their reactionary cost (full − primary, Tables 26 − 22) of that propagated delay. A flight whose buffer absorbs the delay, or with no further rotation that day, costs only its primary cost. Cook & Tanner's "full" table, which assumes an average buffer, is kept as an upper bound and primary-only as a lower bound. All three are reported in a sensitivity table, as the statement asks ("vary the values of r<sub>f</sub> and see its impact"). | Every cost figure: GDP (RBS) 8.46 M€ (between 5.21 M€ primary only and 11.84 M€ full). GHP cost −21.7 %. The GHP optimised on this model is cheaper than RBS under all five cost models tested; the one optimised on the "full" table is not (it costs 4 % more than RBS on primary costs). New KPIs: delay passed on to the next rotations and the reactionary share of the cost. | ✅ |
| C14 | The course asks to "consider a margin (30 min for example) to exempt flights ready to depart at Hfile (already boarded, door closed, etc.)", and WP3 lists "flights ready to depart" among the exempt flights. The delivered code had the option but used 0 min. The margin is now 30 min (`config.READY_MARGIN`). | One more flight is ExemptFlying (360 controlled, 55 exempt); totals change by less than 0.1 %. | ✅ |
| C15 | The statement notes that an ETD later than the ETA means the flight left the previous day. The "already flying at Hfile" test compared the raw ETD with Hfile, so such a flight would have been treated as controlled. On 10 August all 27 such flights are intercontinental (already exempt), so no result changes, but the rule now follows the statement. | None on this day | ✅ |
| C16 | The WP2 cost heatmap and the WP2 "do-nothing vs. GDP" figure used flat rates of 100 €/min airborne and 35 €/min on the ground, attributed to Cook & Tanner. Neither value is in that report: its 100 €/min is the network average for *all* ATFM delay, mostly ground delay. WP2 now uses the same cost model as WP3–WP5 (do-nothing: the same delay costed en route). The WP2 cost figures are now consistent with the rest of the project. | WP2 cost-savings heatmap and figure | ✅ |
| C17 | **CO₂ of ground delay.** The WP2 statement asks to "look for references for fuel consumption at gate ... one value for each aircraft category" and to convert fuel to CO₂ with 3.16 kg/kg. The delivered code assumed every held aircraft runs only on fixed electrical ground power, priced with the *Spanish* grid factor, although ground delay is absorbed at the *origin* airport (Paris, London, Frankfurt...). The standard references were checked: (1) EU TEN-T airports must supply electricity at every contact stand since 31 Dec 2024 and at every remote stand by 31 Dec 2029 (Regulation (EU) 2023/1804, AFIR, Art. 12), but nothing obliges airlines to use it; (2) the European cost-of-delay reference (Cook & Tanner 2015) charges at-gate APU fuel for about 25 % of the time in its base scenario (0 % low, about 50 % high); (3) emissions accounting (EU ETS monitoring, Implementing Regulation (EU) 2018/2066, Art. 52) counts a flight's APU fuel, while ground-power electricity is part of the airport's footprint. Ground delay therefore burns the APU normal-running fuel flow of ICAO Doc 9889, *Airport Air Quality Manual*, Table 3-A1-6 (101 kg/h below 100 seats, 110 kg/h for 100–199, 164 kg/h for 200–299, 238 kg/h from 300) for 25 % of the time, × 3.16, with 0 % and 50 % as a sensitivity range. | Ground-delay CO₂ of the GDP 10.6 t → 57.6 t (range 0–115 t); delay CO₂ 30.7 t → 76.6 t. The emissions GHP saves 8.2 % of delay CO₂ (it was 2.3 %). WP2 CO₂ heatmaps and WP4 system CO₂ change accordingly. | ✅ |
| C18 | **Cancellation exercise.** The statement asks that *one airline* "with lots of flights" cancels *all* its flights delayed by more than 2–3 h, then substitution within that airline, then compression. The code cancelled the 12 most delayed controlled flights of any airline (6 of them Vueling) and mixed both steps. Now Vueling (146 controlled flights) cancels its 16 flights delayed by more than 3 h; substitution fills its freed slots with its own later flights in their original order, and compression then passes the remaining slots on (owner first, then earliest ETA). The statement's KPIs are reported: cancelled flights, moves by substitution and by compression, delay saved, and the mean delay of the airline's remaining flights. | Report §penalty: 16 cancelled, 96 substitutions + 95 compression moves, 5,302 min saved; Vueling's remaining flights 92.4 → 60.5 min mean delay. | ✅ |
| C19 | The WP2 statement lists the KPIs to report: total, maximum and mean air and ground delay, the relative standard deviation of each, air and ground CO₂, delayed flights and OTP. The summary tables lacked the mean and maximum ground delay, both RSDs and the air/ground CO₂ split. The RSDs were also computed over *all* flights, mixing in the zeros of flights that cannot take that kind of delay. They are now computed over exempt flights (air) and controlled flights (ground), and every KPI is in the WP2–WP5 tables. | WP2 tables (e.g. RSD of ground delay 61 %, mean ground delay 98.8 min per controlled flight) | ✅ |
| C20 | The statement asks for two histograms, arrivals per hour before the regulation (ETA) and after it, as a check of the GDP. The second bar series was drawn from the Newell capacity curve rather than from the allocation; it now counts the CTAs. The slot table also records the airline owning each slot, as the statement's slot matrix does. | WP2 Figure 2 | ✅ |

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

### WP3 results after C1–C3, C8–C10 and C13–C19 (real traffic day, 415 affected flights)

| | GDP (RBS) | GHP emissions | GHP cost |
|---|---|---|---|
| Total delay (min) | 35,680 | 35,680 | 35,680 |
| Max delay (min) | 224 | 300 | 300 |
| OTP, delay < 15 min | 18.8 % | 50.1 % | 24.1 % |
| Unrecoverable delay (min) | 1,545 | 1,362 | 1,585 |
| Delay passed on to next rotations (min) | 18,895 | 22,834 | **17,990** |
| Delay CO₂ (kg) | 76,570 | **70,323** (−8.2 %) | 74,471 |
| Cost of delay (EUR) | 8.46 M | 9.79 M | **6.62 M** (−21.7 %) |
| of which reactionary (EUR) | 3.25 M | 3.62 M | 1.65 M |
| Airline fairness, RSD of top-4 mean delays | **26.8 %** | 86.3 % | 40.9 % |

The GHP lowers the cost (cost objective) or the CO₂ (emissions objective) of
the same total delay. The cost objective does it mostly by holding first the
aircraft whose next departure has slack, which halves the reactionary cost, at
the expense of fairness between airlines and of a longer maximum delay. The
emissions objective holds the aircraft with the smallest APUs longest whatever
their rotation or airline: it propagates more delay, costs 16 % more than RBS
and is by far the least fair. Minimising CO₂ and cost are conflicting
objectives.

Cost-model sensitivity (`wp3_cost_sensitivity.csv`): optimising on primary
costs only saves 8.6 %, on the rotation model 21.7 % and on the "full" table
22.3 %, each measured with its own model. Only the rotation-optimised schedule
beats RBS under every model, including ±10–15 min on the minimum turnaround
times.

### WP4 results after C4 (real traffic day)

14 flights affected by the regulation have a direct high-speed-rail alternative
(10 Madrid, 3 Alicante, 1 Marseille). Replacing them:

| | GDP (RBS) | Intermodal GDP | GHP cost | Intermodal GHP cost |
|---|---|---|---|---|
| HNoReg | 19:14 | **18:09** (−65 min) | 19:14 | 18:09 |
| Affected flights | 415 | 367 | 415 | 367 |
| Total delay (min) | 35,680 | 30,454 (−14.6 %) | 35,680 | 30,454 |
| Delay CO₂ (kg) | 76,570 | 67,881 | 74,471 | 66,301 |
| Cost of delay (EUR) | 8.46 M | 6.91 M | 6.62 M | **5.49 M** |
| Airline fairness RSD | 26.8 % | **13.8 %** | 40.9 % | 28.1 % |
| System CO₂ (t) | 12,086.5 | 12,011.4 | 12,084.4 | 12,009.9 |

The system saves about 75 t of CO₂: 66.4 t direct (104.1 t of flights replaced
by 37.7 t of rail) and 8.7 t less delay CO₂. Door to door, the train beats
the delayed flight only on Madrid (246 min vs 261 min on average).

### WP5

- Final comparison of the six methods (GDP, GHP emissions and GHP cost, each
  with and without rail substitution). Every KPI is mapped to a SESAR KPA
  (capacity, operational efficiency, predictability, flexibility, safety,
  environment, cost effectiveness, access and equity), as the WP5 statement
  asks: `data/processed/wp5_comparacion_final.csv` and sheet
  `WP5_Final_by_KPA` of the workbook.
- Excel workbook: dashboard, WP3/WP4/WP5 summaries, flight-by-flight audit of
  the six scenarios (with CTD/CTA), compression, parameters and slots.
- Figures use the course terminology (Hstart, Hend, HNoReg, AAR/PAAR, file
  time). The README figures are rebuilt with `tools/make_readme_figures.py`.
- GitHub Actions runs the tests and the full pipeline on the synthetic day.
  The synthetic day now also contains departures (same registrations), so
  the turnaround model is exercised without the real file.
- Code, comments, docstrings, console output, output files, Excel sheets and
  KPI keys are in English. Identifiers were renamed in one pass from the
  team's original Spanish names (e.g. `ejecutar_nucleo_gdp` → `run_gdp`,
  `etiquetar_vuelos_gdp` → `classify_flights`, `Coste_Cook_Total_EUR` →
  `delay_cost_total_eur`); the git history keeps the originals.

### Points examined and left as they are

- **Connecting passengers.** WP3 asks for references on connecting passengers
  at the airport. Barcelona is mainly an origin-destination airport: about
  9–10 % of its passengers connect (Cambra de Comerç de Barcelona, *Observatori
  del Transport Aeri* 2025–2026). Cook & Tanner's passenger costs are built from European passenger
  itineraries and already include the average cost of missed connections. A
  single airport-wide factor would scale every flight alike and would not
  change the optimal order, and the traffic file has no flight-level
  connection data. The model therefore keeps Cook & Tanner's average; a
  flight-level weighting (e.g. by the hub carrier's banks) is listed as future
  work.
- **Cost year.** Cook & Tanner values are in 2014 euros and are reported as
  such; they are used to rank allocations, where a common inflation factor
  does not matter.
- **Fairness.** WP3 asks to propose (not implement) a way to make the GHP
  fairer. Proposal: add one constraint per airline limiting its share of the
  total delay to its share of affected flights (± a tolerance), or keep RBS as
  the allocation and let each airline reorder delay among its own flights
  only, as in EUROCONTROL's User-Driven Prioritisation Process (UDPP). Both
  keep the per-airline mean delay close to RBS.
