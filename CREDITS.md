# Credits

## Authors

This simulator was developed as the *Adopt an Airport* team project of the
MGTA course at EETAC – Universitat Politècnica de Catalunya (spring 2026).

| Author | GitHub | Main contributions |
|---|---|---|
| **Dènia Cabrera** | [@deniacabrera](https://github.com/deniacabrera) | WP1 data processing and Excel audit; WP2 RBS slot filling, GDP candidate filtering, cancellation and compression algorithm; WP3 unit-cost validation of the GHP; WP4 train-emission data |
| **Adam Ruiz Herrero** | [@Adh3r](https://github.com/Adh3r) | WP1 aggregate demand (Newell); WP2 debugging and sensitivity analysis; WP3 ground-CO₂ model (FEGP + Spanish grid, replaced in the revision: see C17); WP4 intermodal model; WP5 unified metrics engine and project integration |
| **Josep Ponz** | [@PakytoPaez](https://github.com/PakytoPaez) | WP2 slot generation; WP3 GHP scenarios, Cook & Tanner cost model, fairness and KPI comparison, figures |

The full development history (March–May 2026) is preserved in this
repository. Commit messages were translated and normalised when the project
was published; authorship and dates are unchanged.

## Third-party material

- **`src/emissions_fuel_model.py`** — unmodified copy of
  [luis-uow/emissions_fuel_estimation](https://github.com/luis-uow/emissions_fuel_estimation)
  (commit `6d415b3`), licensed under GPL-3.0. It implements the model in
  A. Montlaur, C. Trapote-Barreira & L. Delgado, *Analytical Models of Flight
  Fuel Consumption and Non-CO₂ Emissions as a Function of Aircraft Capacity*,
  Applied Sciences 15(17), 9688, 2025.
  [doi:10.3390/app15179688](https://doi.org/10.3390/app15179688).
  Because this file is GPL-3.0, the whole project is distributed under GPL-3.0.
- **Delay-cost tables** — A. Cook & G. Tanner, *European airline delay cost
  reference values*, v4.1, University of Westminster for EUROCONTROL, 2015
  (Tables 22, 24, 26, 28), and *The cost of passenger delay to airlines in
  Europe*, 2015 (Annex 3, base passenger loads).
- **Reactionary delay and turnarounds** — EUROCONTROL, *Data Snapshot #20*
  (reactionary delays), cited in the WP3 statement; minimum declared ground
  time of 35 min from the E190 operations-manual summary provided with the
  course material (not redistributed).
- **APU fuel at the gate** — fuel flow from ICAO Doc 9889, *Airport Air
  Quality Manual*, 2nd edition, Table 3-A1-6; share of APU time from Cook &
  Tanner (2015); ground-power obligations from Regulation (EU) 2023/1804
  (AFIR), Art. 12.
- **Arrival capacity of LEBL** — EUROCONTROL Performance Review Commission,
  *Airport Capacity Imbalance* technical note (2020), LEBL section; declared
  capacity (38 + 40 movements/h) from EUROCONTROL Airport Corner as cited in
  the El Prat expansion technical study.
- **Cost price base and turnaround times** — EUROCONTROL, *Standard Inputs
  for Economic Analyses*, edition 10.0.2 (2026), chapters 16 and 18.
- **Load factor** — IATA, Air Passenger Market in Detail, August 2025 (Europe
  87.9 %).
- **Connecting passengers at LEBL** — Cambra de Comerç de Barcelona,
  *Observatori del Transport Aeri* (2025–2026 editions).
- **Traffic data** — the LEBL arrivals and departures of 10 August 2025 used for every result
  in the project were provided by Prof. Adeline Montlaur (EETAC – UPC) for the
  MGTA course. They are **not redistributed** in this repository. The
  simulator runs on a synthetic day instead (`data/sample/`, generated from
  scratch by `tools/make_synthetic_traffic.py`), and uses the real file when it
  is placed at `data/raw/LEBL_10AUG2025.csv`.
- **Fleet table** (`data/raw/fleet_cat_seat.csv`) — RECAT category and average
  seats per aircraft type, provided with the course material.
- **Airport coordinates** (`data/raw/airports.csv`) — extracted from
  [OurAirports](https://ourairports.com/data/) (public domain) with
  `tools/build_airport_table.py`.

## Development assistance

The 2026 clean-up and corrections (dependency pinning, bug fixes, tests and
documentation) were done with help from Claude (Anthropic) as a coding assistant.
