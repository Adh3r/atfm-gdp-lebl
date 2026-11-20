# atfm-gdp-lebl

**Air traffic flow management simulator for Barcelona–El Prat (LEBL).**
It models a low-visibility capacity drop and compares the ways of absorbing the
resulting arrival delay: a Ground Delay Program allocated by Ration-By-Schedule,
Ground Holding optimisation for cost or for CO₂, and replacing short-haul
flights with high-speed rail.

Built on one real traffic day (10 August 2025, 550 arrivals). The capacity is
cut from 44 to 20 arrivals/h between 06:00 and 13:00 UTC.

> **Status:** the project is being cleaned up after its academic delivery
> (May 2026). Corrections to the submitted results are tracked in
> [`CHANGES.md`](CHANGES.md). The results section will be filled in once the
> correction pass is finished.

## What it does

| Work package | Module | Content |
|---|---|---|
| WP1 — Data | `lib_data_prep.py` | Filter LEBL arrivals, join fleet data (RECAT, seats), estimate flown distance and baseline CO₂ ([Montlaur et al., 2025](https://doi.org/10.3390/app15179688)) |
| WP2 — GDP | `lib_gdp_core.py`, `lib_compression.py`, `lib_sensitivity.py` | Newell cumulative curves and H<sub>NoReg</sub>; exemption rules (non-ECAC, airborne, outside radius); RBS slot allocation; air/ground delay split; unrecoverable delay; penalty-and-compression with airline slot ownership; radius × file-time sensitivity heatmaps |
| WP3 — GHP | `lib_ghp_solver.py`, `cost_model.py` | Slot assignment as an integer programme minimising delay CO₂ (FEGP ground power vs. airborne burn) or delay cost (Cook & Tanner 2015, scaled by real passengers); fairness across airlines |
| WP4 — Intermodality | `lib_intermodal.py` | Door-to-door time and CO₂ for flights with a high-speed-rail alternative; network re-simulation without them |
| WP5 — Reporting | `lib_metrics.py`, `lib_excel_export.py`, `lib_visualization.py` | One metrics engine for every scenario; multi-sheet Excel audit; figures |

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt

cd src
python main.py                  # full pipeline, about 3 minutes
```

Outputs are written to `data/processed/` (CSV tables and `auditoria_completa.xlsx`)
and `output/figures/` (SVG). The scenario parameters (capacities, regulation
window, radius, file time) live in `src/config.py`.

**Data.** The real traffic day was provided by the course and is not
redistributed. Out of the box, the simulator runs on a synthetic day with the
same layout (`data/sample/synthetic_LEBL_day.csv`), so its numbers differ from
the published results. With access to the original file, place it at
`data/raw/LEBL_10AUG2025.csv`, or point `ATFM_TRAFFIC` at any CSV with the
same columns.

```bash
python -m pytest test -q        # from the repository root
```

## Repository layout

```
data/raw/         fleet and airport tables (real traffic file goes here, git-ignored)
data/sample/      synthetic traffic day
tools/            scripts that rebuild the airport table and the synthetic day
src/              simulator (main.py is the entry point)
test/             pytest suite
data/processed/   generated tables      (git-ignored)
output/figures/   generated figures     (git-ignored)
```

## Authors

Dènia Cabrera, Adam Ruiz Herrero and Josep Ponz. Developed as a team project for
the MGTA course at EETAC – UPC. See [`CREDITS.md`](CREDITS.md) for who did what
and for the third-party material.

## Licence

GPL-3.0 (required by the bundled emissions model). See [`LICENSE`](LICENSE).
