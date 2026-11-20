# =============================================================================
# src/lib_intermodal.py
# WP4: Intermodality — replace short flights with high-speed rail (HSR)
#
# Following the WP4 statement:
#   1. Within the regulation interval (affected flights, not 24 h), identify
#      the routes with a direct rail option of reasonable duration.
#   2. Compare door-to-door (D2D) time and CO2 of air and rail for those
#      flights, first without delay and then with their regulation delay.
#   3. Remove those flights from the demand and apply GDP and GHP (both
#      objectives) again to the reduced set, with the same hypotheses.
#
# Rail times: direct HSR services from Barcelona (project values, see the
# report). Rail CO2: EcoPassenger per-passenger footprints (UIC/ifeu).
# D2D access/egress: ALG (2024), "Modal competition and substitution: HSR vs
# air transport" - 90 + 60 min for air, 40 + 20 min for rail.
# =============================================================================

import os

import pandas as pd

import lib_gdp_core as gdp
from lib_ghp_solver import ejecutar_ghp_completo
from lib_metrics import evaluar_escenario, resumen_escenarios

# --- PARAMETERS ---
DISTANCIA_MAX_INTERMODAL_KM = 500      # great-circle distance limit for substitution
VELOCIDAD_HSR_KMH = 200                # fallback rail speed (route not in the table)
TIEMPO_ACCESO_AEROPUERTO_MIN = 90      # to the airport + procedures
TIEMPO_EGRESO_AEROPUERTO_MIN = 60      # from the airport to the destination
TIEMPO_ACCESO_ESTACION_MIN = 40
TIEMPO_EGRESO_ESTACION_MIN = 20
VELOCIDAD_CRUCERO_KT = 440             # air in-vehicle time estimate
KT_TO_KMH = 1.852
LOAD_FACTOR_POR_DEFECTO = 0.837        # IATA, Europe 2025
ASIENTOS_POR_DEFECTO = 180

# Direct HSR services to Barcelona (travel time in hours).
DESTINOS_HSR_DESDE_LEBL = {
    'LEMD': {'ciudad': 'Madrid',    'tiempo_tren_h': 3.1},
    'LEVC': {'ciudad': 'Valencia',  'tiempo_tren_h': 3.0},
    'LEZG': {'ciudad': 'Zaragoza',  'tiempo_tren_h': 1.5},
    'LFBO': {'ciudad': 'Toulouse',  'tiempo_tren_h': 4.0},
    'LEAL': {'ciudad': 'Alicante',  'tiempo_tren_h': 5.2},
    'LFML': {'ciudad': 'Marsella',  'tiempo_tren_h': 4.8},
    'LFLL': {'ciudad': 'Lyon',      'tiempo_tren_h': 5.0},
}

# Rail CO2 per passenger, kg (EcoPassenger).
FACTORES_CO2_TREN_KG_PAX = {
    'LEMD': 19.0, 'LEVC': 10.7, 'LEAL': 15.5, 'LEZG': 8.3,
    'LFML': 7.3,  'LFBO': 6.4,  'LFLL': 7.9,
}


def _pasajeros(fila) -> int:
    asientos = fila.get('size_seats_avg', ASIENTOS_POR_DEFECTO)
    asientos = ASIENTOS_POR_DEFECTO if pd.isna(asientos) else asientos
    return int(asientos * LOAD_FACTOR_POR_DEFECTO)


def identificar_vuelos_substituibles(df_vuelos: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Flag the arrivals with a direct HSR alternative within the distance limit."""
    df = df_vuelos.copy()
    df['es_substituible'] = (
        (df['distancia_km'] <= DISTANCIA_MAX_INTERMODAL_KM)
        & df['ADEP'].isin(DESTINOS_HSR_DESDE_LEBL.keys())
        & (df['ADES'] == 'LEBL')
    )
    if verbose:
        n = int(df['es_substituible'].sum())
        pct = n / len(df) * 100 if len(df) else 0
        print(f"\n 🚄 INTERMODAL: {n} rail-replaceable flights ({pct:.1f}% of {len(df)})")
    return df


def calcular_tiempo_d2d(distancia_km: float, origen_icao: str, es_avion: bool = True) -> float:
    """Door-to-door time in minutes, without delay."""
    if es_avion:
        tiempo_vuelo_h = distancia_km / (VELOCIDAD_CRUCERO_KT * KT_TO_KMH)
        return TIEMPO_ACCESO_AEROPUERTO_MIN + tiempo_vuelo_h * 60 + TIEMPO_EGRESO_AEROPUERTO_MIN
    tiempo_tren_h = DESTINOS_HSR_DESDE_LEBL.get(origen_icao, {}).get(
        'tiempo_tren_h', distancia_km / VELOCIDAD_HSR_KMH)
    return TIEMPO_ACCESO_ESTACION_MIN + tiempo_tren_h * 60 + TIEMPO_EGRESO_ESTACION_MIN


def calcular_emision_tren(distancia_km: float, origen_icao: str, n_pasajeros: int) -> float:
    """Rail CO2 (kg) for n passengers; generic per-km factor when the route is not tabulated."""
    factor_kg_pax = FACTORES_CO2_TREN_KG_PAX.get(origen_icao)
    if factor_kg_pax is not None:
        return factor_kg_pax * n_pasajeros
    factor_g_pax_km = 15.0 if origen_icao.startswith('LE') else 20.0
    return distancia_km * n_pasajeros * factor_g_pax_km / 1000


def generar_comparativa_modal(df_sustituidos: pd.DataFrame) -> pd.DataFrame:
    """Air vs rail D2D time and CO2 for each replaced flight (air with its GDP delay)."""
    filas = []
    for _, vuelo in df_sustituidos.iterrows():
        distancia, origen = vuelo['distancia_km'], vuelo['ADEP']
        n_pax = _pasajeros(vuelo)
        retraso = vuelo.get('total_delay', 0)
        t_avion = calcular_tiempo_d2d(distancia, origen, es_avion=True)
        t_tren = calcular_tiempo_d2d(distancia, origen, es_avion=False)
        co2_avion = vuelo.get('co2_kg_vuelo', 0)
        co2_tren = calcular_emision_tren(distancia, origen, n_pax)
        filas.append({
            'ARCID': vuelo.get('ARCID', 'N/A'),
            'ADEP': origen,
            'distancia_km': round(distancia, 1),
            'pasajeros': n_pax,
            'retraso_ATFM_min': round(retraso, 1),
            'tiempo_d2d_avion_NOMINAL_min': round(t_avion, 1),
            'tiempo_d2d_avion_CON_RETRASO_min': round(t_avion + retraso, 1),
            'tiempo_d2d_tren_min': round(t_tren, 1),
            'ahorro_tiempo_TREN_min': round(t_avion + retraso - t_tren, 1),
            'co2_avion_kg': round(co2_avion, 1),
            'co2_tren_kg': round(co2_tren, 1),
            'ahorro_co2_neto_kg': round(co2_avion - co2_tren, 1),
        })
    return pd.DataFrame(filas)


def simular_sin_vuelos(df_vuelos: pd.DataFrame, indices_eliminados, params: dict) -> dict:
    """Re-run GDP and GHP (both objectives) without the replaced flights."""
    df_reducido = df_vuelos.drop(index=indices_eliminados)
    res_gdp = gdp.ejecutar_nucleo_gdp(df_reducido, params, run_compression=False)
    res_ghp = ejecutar_ghp_completo(
        res_gdp['vuelos_asignados'], list(res_gdp['slots']['slot_start_min']), params)
    return {'resultados_gdp': res_gdp, 'resultados_ghp': res_ghp, 'df_reducido': df_reducido}


def ejecutar_analisis_intermodal(
    df_vuelos: pd.DataFrame,
    resultados_base: dict,
    resultados_base_ghp: dict,
    params: dict,
    base_dir: str,
) -> dict:
    """
    WP4 pipeline. The system CO2 boundary is every arrival of the day, so the
    baseline and the intermodal scenarios are directly comparable:

        system CO2 = nominal CO2 of all flights flown + delay CO2 + rail CO2

    Returns a dict with the modal comparison, the summary table, the scenario
    tables and the re-simulation results.
    """
    print("\n" + "=" * 80)
    print("🚄 WP4: INTERMODALITY — REPLACING SHORT FLIGHTS BY HIGH-SPEED RAIL")
    print("=" * 80)
    h_start = params['H_START']

    # 1. Rail-replaceable flights among the flights affected by the regulation.
    base_afectados = resultados_base['vuelos_asignados']
    marcados = identificar_vuelos_substituibles(base_afectados, verbose=True)
    sustituidos = marcados[marcados['es_substituible']]
    df_modal = generar_comparativa_modal(sustituidos)

    # 2. Re-simulation without them.
    red = simular_sin_vuelos(df_vuelos, sustituidos.index, params)

    # 3. KPIs of the six scenarios (base and reduced, GDP and both GHP).
    escenarios_base = {
        'GDP (RBS)':     base_afectados,
        'GHP emissions': resultados_base_ghp['GHP_Emissions'],
        'GHP cost':      resultados_base_ghp['GHP_Cost'],
    }
    escenarios_red = {
        'Intermodal GDP (RBS)':     red['resultados_gdp']['vuelos_asignados'],
        'Intermodal GHP emissions': red['resultados_ghp']['GHP_Emissions'],
        'Intermodal GHP cost':      red['resultados_ghp']['GHP_Cost'],
    }
    kpis = {n: evaluar_escenario(d, n, h_start)
            for n, d in {**escenarios_base, **escenarios_red}.items()}

    co2_vuelos_dia = df_vuelos['co2_kg_vuelo'].sum()
    co2_vuelos_sustituidos = sustituidos['co2_kg_vuelo'].sum()
    co2_tren = df_modal['co2_tren_kg'].sum() if len(df_modal) else 0.0
    h_noreg = {**{n: resultados_base['h_noreg'] for n in escenarios_base},
               **{n: red['resultados_gdp']['h_noreg'] for n in escenarios_red}}
    co2_nominal = {**{n: co2_vuelos_dia for n in escenarios_base},
                   **{n: co2_vuelos_dia - co2_vuelos_sustituidos for n in escenarios_red}}
    co2_rail = {**{n: 0.0 for n in escenarios_base}, **{n: co2_tren for n in escenarios_red}}
    co2_sistema = {n: co2_nominal[n] + kpis[n]['CO2_Total_Retraso_kg'] + co2_rail[n] for n in kpis}

    df_comp = resumen_escenarios(kpis, extras={
        'HNoReg (min UTC)':                    h_noreg,
        'Flights replaced by rail':            {**{n: 0 for n in escenarios_base},
                                                **{n: len(sustituidos) for n in escenarios_red}},
        'Nominal CO2 of all flights flown (kg)': {n: round(v, 1) for n, v in co2_nominal.items()},
        'Rail CO2 (kg)':                       {n: round(v, 1) for n, v in co2_rail.items()},
        'System CO2: flights + delay + rail (kg)': {n: round(v, 1) for n, v in co2_sistema.items()},
    })

    print(f"   • Replaced flights (within the regulation): {len(sustituidos)}")
    print(f"   • HNoReg: {resultados_base['h_noreg']} -> {red['resultados_gdp']['h_noreg']} min UTC")
    print(f"   • Direct CO2 saving of the substitution: "
          f"{co2_vuelos_sustituidos - co2_tren:,.0f} kg (flights {co2_vuelos_sustituidos:,.0f} - rail {co2_tren:,.0f})")
    print(df_comp.to_string(index=False))

    os.makedirs(os.path.join(base_dir, 'data/processed'), exist_ok=True)
    df_modal.to_csv(os.path.join(base_dir, 'data/processed/wp4_comparativa_modal.csv'), index=False)
    df_comp.to_csv(os.path.join(base_dir, 'data/processed/wp4_comparativa_intermodal.csv'), index=False)

    return {
        'df_modal': df_modal,
        'df_comparativa': df_comp,
        'resultados_reducido': red,
        'escenarios_reducidos': escenarios_red,
        'kpis': kpis,
        'co2': {'vuelos_sustituidos': co2_vuelos_sustituidos, 'tren': co2_tren},
    }
