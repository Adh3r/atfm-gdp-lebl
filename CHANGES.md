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
| R2 | 19 of 47 tests were stale and one test file was never collected. | ⏳ |

## Results

| | Issue | Effect on the report | Status |
|---|---|---|---|
| C1 | The "GDP" column used in the report's cost and CO₂ comparison is the GHP validation run with r = 1. With uniform weights every slot permutation is optimal, so that run's cost, CO₂ and fairness are arbitrary. The real RBS allocation costs 4.33 M€, not 5.61 M€. | The headline "GHP reduces cost by 20.3 %" does not hold against RBS | ⏳ |
| C2 | The cost-optimal GHP minimised a *linear* proxy (the Cook cost at 15 min, per minute), while results were evaluated on the convex Cook & Tanner curve. The optimiser concentrated very long delays on a few flights (maximum 674 min, against 224 min under RBS). | GHP cost scenario | ⏳ |
| C3 | The airline fairness RSD reported (9.77 / 35.58 / 84.81 %) is the RSD of the top-4 airline mean delays. A later refactor replaced it with a per-flight RSD, and the delivered run printed 0 %. | Fairness KPI | ⏳ |
| C4 | WP4 "total system CO₂" subtracts the CO₂ of the 11 rail-replaceable flights of the whole day (69.0 t) from a baseline that only covers the regulated window (9 of those flights, 57.0 t). The direct rail saving (43 t) also uses all 11 flights, while the text speaks of 9. | WP4 CO₂ figures | ⏳ |
| C5 | Ground-delay CO₂ used a cruise-burn proxy in the WP2 heatmaps but FEGP ground power in WP3/WP4. | WP2 CO₂ heatmaps | ⏳ |
| C6 | The report states that the GHP lowers the maximum delay; its own appendix table shows the opposite. Compression moved 222 flights (the report says 212). | Text only | ⏳ |
