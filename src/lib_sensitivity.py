# =============================================================================
# src/lib_sensitivity.py
# WP2 - Sensitivity of the GDP to its two design parameters:
#   - the radius of exemption R (flights from further away are exempt);
#   - the file time Hfile, set by Hstart - Hfile (flights already flying at
#     Hfile are exempt).
#
# Grid search: the full GDP is simulated for every (R, Hstart - Hfile) pair in
# RADIOS_KM x HFILE_MINS (6 x 7 = 42 runs). Each KPI becomes a 6 x 7 matrix,
# drawn as a heatmap by lib_visualization.generar_heatmap().
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

RADIOS_KM  = [500, 1000, 1500, 2000, 2500, 3000]    # radius of exemption (km)
HFILE_MINS = [30, 60, 90, 120, 150, 180, 210]        # Hstart - Hfile (min)

# =============================================================================
# HEATMAPS: (column, title, unit, 'min' | 'max' = which value is best)
# =============================================================================

HEATMAPS = [
    # KPIs asked for in the statement
    ('air_delay_total',     'Trade-off: Total AIR Delay',               'min', 'min'),
    ('co2_tierra_delay',    'CO2 Emissions Due to GROUND Delay',        'kg',  'min'),
    ('co2_aire_delay',      'CO2 Emissions Due to AIR Delay',           'kg',  'min'),
    ('unrecoverable_delay', 'Unrecoverable Delay (GDP cancelled)',      'min', 'min'),
    # additional KPIs
    ('co2_savings',         'Net CO2 Savings vs. Do-Nothing',           'kg',  'max'),
    ('cost_savings',        'Total Economic Savings vs. Do-Nothing',    'EUR', 'max'),
]

# =============================================================================
# ONE GDP RUN PER GRID CELL
# =============================================================================

def _simular_gdp_ligero(
    df_vuelos: pd.DataFrame,
    params: dict,
    radius_km: int,
    h_file: int,
) -> dict:
    """
    Run the GDP for one (R, Hstart - Hfile) pair and return the heatmap KPIs.

    It reuses ejecutar_nucleo_gdp() (without compression), so the sensitivity
    analysis always runs exactly the same engine as the main scenario.

    Args:
        df_vuelos: prepared flights (WP1).
        params:    GDP parameters (H_START, H_END, AAR, PAAR, ...).
        radius_km: radius of exemption for this run.
        h_file:    Hstart - Hfile (min) for this run.
    """
    from lib_gdp_core import calcular_kpis_economicos, ejecutar_nucleo_gdp

    resultados = ejecutar_nucleo_gdp(
        df_vuelos,
        params,
        radius_km=radius_km,
        h_file_offset=h_file,
        run_compression=False,
    )
    df_resultado = resultados['vuelos_asignados']
    kpis = calcular_kpis_economicos(df_resultado, params['H_START'])

    return {
        'radius_km':           radius_km,
        'h_file':              h_file,
        'air_delay_total':     float(df_resultado['air_delay'].sum()),
        'unrecoverable_delay': kpis['unrecoverable_delay'],
        'cost_savings':        kpis['cost_savings'],
        'co2_savings':         kpis['co2_savings'],
        'co2_aire_delay':      kpis['co2_aire_delay'],
        'co2_tierra_delay':    kpis['co2_tierra_delay'],
    }

# =============================================================================
# ORCHESTRATOR
# =============================================================================

def ejecutar_analisis_sensibilidad(
    df_vuelos: pd.DataFrame,
    params: dict,
    base_dir: str,
) -> pd.DataFrame:
    """
    Run the 42-cell grid, draw one heatmap per KPI (rows: Hstart - Hfile,
    columns: R, best cell highlighted) and save the grid to
    data/processed/sensitivity_grid.csv.

    Args:
        df_vuelos: prepared flights (WP1).
        params:    GDP parameters.
        base_dir:  project root.

    Returns:
        DataFrame with one row per run.
    """
    carpeta_heatmaps = os.path.join(base_dir, 'output', 'figures', 'heatmaps')
    total = len(RADIOS_KM) * len(HFILE_MINS)
    print(f"   Grid: {len(RADIOS_KM)} radii x {len(HFILE_MINS)} file times = {total} GDP runs")

    lista_resultados = []
    for i, radio in enumerate(RADIOS_KM):
        for j, hfile in enumerate(HFILE_MINS):
            # pandas chained-assignment warnings are known and would be printed 42 times
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                resultado_celda = _simular_gdp_ligero(df_vuelos, params, radio, hfile)
            lista_resultados.append(resultado_celda)
            n = i * len(HFILE_MINS) + j + 1
            print(f"   [{n:02d}/{total}] R={radio:4d} km  Hstart-Hfile={hfile:3d} min  "
                  f"air delay={resultado_celda['air_delay_total']:.0f} min", end='\r')
    print()

    df_cuadricula = pd.DataFrame(lista_resultados)

    for nombre_kpi, titulo, unidad, mejor in HEATMAPS:
        df_matriz = df_cuadricula.pivot(index='h_file', columns='radius_km', values=nombre_kpi)
        print(f"\n   {titulo} ({unidad}); rows Hstart - Hfile (min), columns R (km)")
        print(df_matriz.round(1).to_string())

        valores = df_matriz.values.astype(float)
        plano = np.argmin(valores) if mejor == 'min' else np.argmax(valores)
        fila, columna = np.unravel_index(plano, valores.shape)

        ruta = os.path.join(carpeta_heatmaps, f"heatmap_{nombre_kpi.replace('_', '-')}.svg")
        vis.generar_heatmap(df_matriz, titulo, unidad, mejor, ruta, int(fila), int(columna))

    ruta_csv = os.path.join(base_dir, 'data', 'processed', 'sensitivity_grid.csv')
    os.makedirs(os.path.dirname(ruta_csv), exist_ok=True)
    df_cuadricula.to_csv(ruta_csv, index=False)
    print(f"\n   Grid: data/processed/sensitivity_grid.csv; heatmaps: output/figures/heatmaps/")

    return df_cuadricula


# =============================================================================
# DEBUG MODE: python src/lib_sensitivity.py
# =============================================================================
if __name__ == "__main__":
    import lib_data_prep as prep

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    df_vuelos = prep.preparar_vuelos(config.traffic_file(), config.FLEET_TABLE)
    ejecutar_analisis_sensibilidad(df_vuelos, CFG.to_params_dict(), base)
