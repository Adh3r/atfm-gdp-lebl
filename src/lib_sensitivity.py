# =============================================================================
# src/lib_sensitivity.py
# WP2 - Sensitivity of the GDP to its two design parameters:
#   - the radius of exemption R (flights from further away are exempt);
#   - the file time Hfile, set by Hstart - Hfile (flights already flying at
#     Hfile are exempt).
#
# Grid search: the full GDP is simulated for every (R, Hstart - Hfile) pair in
# RADII_KM x HFILE_MINS (6 x 7 = 42 runs). Each KPI becomes a 6 x 7 matrix,
# drawn as a heatmap by lib_visualization.plot_heatmap().
# =============================================================================

import os
import warnings

import numpy as np
import pandas as pd

import config
import lib_visualization as vis
from config import CFG

# =============================================================================
# PARAMETER GRID
# =============================================================================

RADII_KM  = [500, 1000, 1500, 2000, 2500, 3000]    # radius of exemption (km)
HFILE_MINS = [30, 60, 90, 120, 150, 180, 210]        # Hstart - Hfile (min)

# =============================================================================
# HEATMAPS: (column, title, unit, 'min' | 'max' = which value is best)
# =============================================================================

HEATMAPS = [
    # KPIs asked for in the statement
    ('air_delay_total',     'Trade-off: Total AIR Delay',               'min', 'min'),
    ('ground_delay_co2',    'CO2 Emissions Due to GROUND Delay',        'kg',  'min'),
    ('air_delay_co2',      'CO2 Emissions Due to AIR Delay',           'kg',  'min'),
    ('unrecoverable_delay', 'Unrecoverable Delay (GDP cancelled)',      'min', 'min'),
    # additional KPIs
    ('co2_savings',         'Net CO2 Savings vs. Do-Nothing',           'kg',  'max'),
    ('cost_savings',        'Total Economic Savings vs. Do-Nothing',    'EUR', 'max'),
]

# =============================================================================
# ONE GDP RUN PER GRID CELL
# =============================================================================

def _run_cell(
    df_flights: pd.DataFrame,
    params: dict,
    radius_km: int,
    h_file: int,
) -> dict:
    """
    Run the GDP for one (R, Hstart - Hfile) pair and return the heatmap KPIs.

    It reuses run_gdp() (without compression), so the sensitivity
    analysis always runs exactly the same engine as the main scenario.

    Args:
        df_flights: prepared flights (WP1).
        params:    GDP parameters (H_START, H_END, AAR, PAAR, ...).
        radius_km: radius of exemption for this run.
        h_file:    Hstart - Hfile (min) for this run.
    """
    from lib_gdp_core import gdp_vs_do_nothing, run_gdp

    results = run_gdp(
        df_flights,
        params,
        radius_km=radius_km,
        h_file_offset=h_file,
        run_compression=False,
    )
    df_result = results['allocation']
    kpis = gdp_vs_do_nothing(df_result, params['H_START'])

    return {
        'radius_km':           radius_km,
        'h_file':              h_file,
        'air_delay_total':     float(df_result['air_delay'].sum()),
        'unrecoverable_delay': kpis['unrecoverable_delay'],
        'cost_savings':        kpis['cost_savings'],
        'co2_savings':         kpis['co2_savings'],
        'air_delay_co2':      kpis['air_delay_co2'],
        'ground_delay_co2':    kpis['ground_delay_co2'],
    }

# =============================================================================
# ORCHESTRATOR
# =============================================================================

def run_sensitivity(
    df_flights: pd.DataFrame,
    params: dict,
    base_dir: str,
) -> pd.DataFrame:
    """
    Run the 42-cell grid, draw one heatmap per KPI (rows: Hstart - Hfile,
    columns: R, best cell highlighted) and save the grid to
    data/processed/sensitivity_grid.csv.

    Args:
        df_flights: prepared flights (WP1).
        params:    GDP parameters.
        base_dir:  project root.

    Returns:
        DataFrame with one row per run.
    """
    heatmap_dir = os.path.join(base_dir, 'output', 'figures', 'heatmaps')
    total = len(RADII_KM) * len(HFILE_MINS)
    print(f"   Grid: {len(RADII_KM)} radii x {len(HFILE_MINS)} file times = {total} GDP runs")

    cells = []
    for i, radius in enumerate(RADII_KM):
        for j, offset in enumerate(HFILE_MINS):
            # pandas chained-assignment warnings are known and would be printed 42 times
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                cell = _run_cell(df_flights, params, radius, offset)
            cells.append(cell)
            n = i * len(HFILE_MINS) + j + 1
            print(f"   [{n:02d}/{total}] R={radius:4d} km  Hstart-Hfile={offset:3d} min  "
                  f"air delay={cell['air_delay_total']:.0f} min", end='\r')
    print()

    grid = pd.DataFrame(cells)

    for kpi, title, unit, best in HEATMAPS:
        matrix = grid.pivot(index='h_file', columns='radius_km', values=kpi)
        print(f"\n   {title} ({unit}); rows Hstart - Hfile (min), columns R (km)")
        print(matrix.round(1).to_string())

        values_list = matrix.values.astype(float)
        flat = np.argmin(values_list) if best == 'min' else np.argmax(values_list)
        row, col = np.unravel_index(flat, values_list.shape)

        path = os.path.join(heatmap_dir, f"heatmap_{kpi.replace('_', '-')}.svg")
        vis.plot_heatmap(matrix, title, unit, best, path, int(row), int(col))

    csv_path = os.path.join(base_dir, 'data', 'processed', 'sensitivity_grid.csv')
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    grid.to_csv(csv_path, index=False)
    print("\n   Grid: data/processed/sensitivity_grid.csv; heatmaps: output/figures/heatmaps/")

    return grid


# =============================================================================
# DEBUG MODE: python src/lib_sensitivity.py
# =============================================================================
if __name__ == "__main__":
    import lib_data_prep as prep

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    df_flights = prep.prepare_flights(config.traffic_file(), config.FLEET_TABLE)
    run_sensitivity(df_flights, CFG.to_params_dict(), base)
