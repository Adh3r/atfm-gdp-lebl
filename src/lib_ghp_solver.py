# =============================================================================
# src/lib_ghp_solver.py
# WP3: Ground Holding Problem (GHP) — Solver de Programación Lineal Entera
#
# CONTEXTO:
#   El GHP asigna vuelos a slots de llegada minimizando una función de coste.
#   A diferencia del RBS (FIFO), el LP puede priorizar vuelos más costosos
#   asignándoles slots más tempranos aunque lleguen más tarde.
#
# VERSIÓN COMBINADA:
#   - CO2: FEGP (red eléctrica) en tierra vs. crucero en aire
#   - COSTE: Cook & Tanner (2015), Tables 11 y 12, ajustado por pasajeros reales
# =============================================================================
 
import numpy as np
import pandas as pd
import pulp
 
import config
from config import CFG, FS_CANDIDATE
from emissions_fuel_model import compute_co2_ask
from cost_model import (
    FEGP_KW_PER_RECAT, GRID_EMISSION_FACTOR_KG_KWH, LOAD_FACTOR_EU,
    OTP_THRESHOLD_MIN, _PAX_BASE_TABLE,
    _interpolar_coste, _mapear_tipo_cook,
)
 
# =============================================================================
# CONSTANTES WP3
# =============================================================================
 
MAX_AIR_DELAY_MIN = 90
 
# Turn-around time mínimo por categoría (minutos).
# Fuente: Eurocontrol Standard Inputs for Economic Analyses, §18
TAT_MIN = {
    'narrow':  45,   # A320, B737 y similares (RECAT D, E, F)
    'wide':    80,   # A330, B777 y similares (RECAT A, B, C)
}
 
 
# =============================================================================
# UTILIDAD INTERNA: CO₂ por minuto de vuelo (Aire)
# =============================================================================
 
def _co2_per_min(distancia_km: float, asientos: int, duracion_min: float) -> float:
    if distancia_km <= 0 or asientos <= 0 or duracion_min <= 0:
        return 0.01
    try:
        co2_ask   = compute_co2_ask(distancia_km, int(asientos), force=True)
        co2_total = co2_ask * distancia_km * asientos / 1000.0
    except Exception:
        return 0.01
    return max(co2_total / duracion_min, 0.01)
 
 
# =============================================================================
# PASO 1: CALCULAR COEFICIENTES r_f PARA CADA VUELO
# =============================================================================
 
def calcular_rf_unitario(df_vuelos: pd.DataFrame) -> pd.Series:
    return pd.Series(1.0, index=df_vuelos.index)
 
 
def calcular_rf_emisiones(df_vuelos: pd.DataFrame) -> pd.Series:
    def _apply_co2(fila):
        if fila.get('flight_status') == FS_CANDIDATE:
            recat_str = str(fila.get('recat', 'D')).upper()
            potencia_kw = FEGP_KW_PER_RECAT.get(recat_str, 72.0)
            co2_fegp_min = potencia_kw * (1 / 60.0) * GRID_EMISSION_FACTOR_KG_KWH
            return co2_fegp_min
 
        distancia = float(fila.get('distancia_km', 0) or 0)
        asientos  = int(fila.get('size_seats_avg', 180) or 180)
        duracion  = float(fila.get('duracion_vuelo_min', 60) or 60)
        return _co2_per_min(distancia_km=distancia, asientos=asientos, duracion_min=duracion)
 
    return df_vuelos.apply(_apply_co2, axis=1)
 
 
def calcular_rf_coste(df_vuelos: pd.DataFrame) -> pd.Series:
    rf = pd.Series(index=df_vuelos.index, dtype=float)
 
    for idx, fila in df_vuelos.iterrows():
        tipo_icao = str(fila.get('ATYP', fila.get('f', ''))).upper().strip()
        asientos  = fila.get('size_seats_avg', 180)
        if pd.isna(asientos) or asientos <= 0:
            asientos = 180
        asientos = int(asientos)
 
        recat   = str(fila.get('recat', 'D')).upper()
        es_wide = recat in ('A', 'B', 'C')
 
        tipo_cook = _mapear_tipo_cook(tipo_icao, recat, asientos)
 
        coste_tabla = _interpolar_coste(tipo_cook, OTP_THRESHOLD_MIN, usar_hard=True)
 
        pax_base   = _PAX_BASE_TABLE.get(tipo_cook, 130)
        pax_reales = max(int(asientos * LOAD_FACTOR_EU), 1)
        factor_pax = pax_reales / pax_base
 
        coste_ajustado = coste_tabla * factor_pax
        rf[idx] = round(coste_ajustado / OTP_THRESHOLD_MIN, 4)
 
    return rf
 
 
# =============================================================================
# PASO 2: CALCULAR TAT DISPONIBLE DESDE EL CSV
# =============================================================================
 
def enriquecer_con_tat(df_vuelos: pd.DataFrame) -> pd.DataFrame:
    df = df_vuelos.copy()
    recat_col   = df['recat'].fillna('D')
    tat_default = recat_col.apply(
        lambda r: TAT_MIN['wide'] + 20 if r in ('A', 'B', 'C')
                  else TAT_MIN['narrow'] + 20
    )
    df['tat_disponible'] = tat_default
    return df
 
# =============================================================================
# PASO 3: SOLVER GHP — PROGRAMACIÓN LINEAL ENTERA BINARIA
# =============================================================================
 
def resolver_ghp(
    df_candidatos: pd.DataFrame,
    df_exentos: pd.DataFrame,
    slots_disponibles: list,
    rf_series: pd.Series,
    nombre_problema: str = 'GHP',
    max_air_delay: int = MAX_AIR_DELAY_MIN,
    verbose: bool = False,
) -> pd.DataFrame:
    slots = sorted(slots_disponibles)
 
    slots_ocupados       = set()
    asignaciones_exentos = {}
 
    for idx, vuelo in df_exentos.sort_values('minutes_eta').iterrows():
        eta = vuelo['minutes_eta']
        slots_elegibles = [
            s for s in slots
            if s >= eta and s not in slots_ocupados and (s - eta) <= max_air_delay
        ]
        if slots_elegibles:
            slot_asignado = slots_elegibles[0]
        else:
            slots_sin_cap = [s for s in slots if s >= eta and s not in slots_ocupados]
            slot_asignado = slots_sin_cap[0] if slots_sin_cap else eta
 
        asignaciones_exentos[idx] = slot_asignado
        slots_ocupados.add(slot_asignado)
 
    slots_para_candidatos = [s for s in slots if s not in slots_ocupados]
 
    if df_candidatos.empty or not slots_para_candidatos:
        return _construir_resultado(df_candidatos, df_exentos, {}, asignaciones_exentos)
 
    candidatos_idx = list(df_candidatos.index)
    eta_map        = df_candidatos['minutes_eta'].to_dict()
 
    if verbose:
        print(f"   [{nombre_problema}] {len(candidatos_idx)} candidatos, "
              f"{len(slots_para_candidatos)} slots disponibles")
 
    prob = pulp.LpProblem(nombre_problema, pulp.LpMinimize)
 
    x = {
        (f_idx, t_idx): pulp.LpVariable(f"x_{f_idx}_{t_idx}", cat='Binary')
        for f_idx in candidatos_idx
        for t_idx, t_val in enumerate(slots_para_candidatos)
        if t_val >= eta_map[f_idx]
    }
 
    prob += pulp.lpSum(
        rf_series.get(f_idx, 1.0) * (slots_para_candidatos[t_idx] - eta_map[f_idx])
        * x[(f_idx, t_idx)]
        for (f_idx, t_idx) in x
    )
 
    for t_idx in range(len(slots_para_candidatos)):
        vars_en_slot = [x[(f, t)] for (f, t) in x if t == t_idx]
        if vars_en_slot:
            prob += pulp.lpSum(vars_en_slot) <= 1, f"cap_slot_{t_idx}"
 
    for f_idx in candidatos_idx:
        vars_vuelo = [x[(f, t)] for (f, t) in x if f == f_idx]
        if vars_vuelo:
            prob += pulp.lpSum(vars_vuelo) == 1, f"asig_vuelo_{f_idx}"
 
    prob.solve(pulp.PULP_CBC_CMD(msg=0))
 
    if verbose:
        print(f"   [{nombre_problema}] Status: {pulp.LpStatus[prob.status]}")
        print(f"   [{nombre_problema}] Coste óptimo: {pulp.value(prob.objective):.2f}")
 
    asignaciones_candidatos = {
        f_idx: slots_para_candidatos[t_idx]
        for (f_idx, t_idx), var in x.items()
        if pulp.value(var) is not None and pulp.value(var) > 0.5
    }
 
    return _construir_resultado(df_candidatos, df_exentos,
                                asignaciones_candidatos, asignaciones_exentos)
 
 
def _construir_resultado(
    df_candidatos: pd.DataFrame,
    df_exentos: pd.DataFrame,
    asig_candidatos: dict,
    asig_exentos: dict,
) -> pd.DataFrame:
    partes = []
 
    if not df_candidatos.empty:
        df_c = df_candidatos.copy()
        df_c['assigned_slot'] = df_c.index.map(asig_candidatos)
        df_c['total_delay']   = (df_c['assigned_slot'] - df_c['minutes_eta']).clip(lower=0)
        df_c['ground_delay']  = df_c['total_delay']
        df_c['air_delay']     = 0.0
        partes.append(df_c)
 
    if not df_exentos.empty:
        df_e = df_exentos.copy()
        df_e['assigned_slot'] = df_e.index.map(asig_exentos)
        df_e['total_delay']   = (df_e['assigned_slot'] - df_e['minutes_eta']).clip(lower=0)
        df_e['air_delay']     = df_e['total_delay']
        df_e['ground_delay']  = 0.0
        partes.append(df_e)
 
    if not partes:
        return pd.DataFrame()
 
    return pd.concat(partes).sort_values('minutes_eta').reset_index(drop=False)
 
 
# =============================================================================
# PASO 4: ORQUESTADOR WP3
# =============================================================================
 
def ejecutar_ghp_completo(
    df_vuelos_etiquetados: pd.DataFrame,
    slots_disponibles: list,
    params: dict,
    verbose: bool = True,
) -> dict:
    df_enriched   = enriquecer_con_tat(df_vuelos_etiquetados)
    h_start       = params['H_START']
    df_en_ventana = df_enriched[df_enriched['minutes_eta'] >= h_start].copy()
 
    df_candidatos = df_en_ventana[df_en_ventana['flight_status'] == FS_CANDIDATE].copy()
    df_exentos    = df_en_ventana[df_en_ventana['flight_status'] != FS_CANDIDATE].copy()
 
    if verbose:
        print(f"   GHP: {len(df_candidatos)} candidatos, {len(df_exentos)} exentos")
        print(f"        {len(slots_disponibles)} slots disponibles")
 
    resultados = {}
 
    for tarea, rf_fn, nombre in [
        ('GDP_Aditional_Constraints', calcular_rf_unitario,  'GHP_Task1_Validation'),
        ('GHP_Opt_Emissions',  calcular_rf_emisiones, 'GHP_Task2_Emissions'),
        ('GHP_Opt_Cost',       calcular_rf_coste,     'GHP_Task3_Cost'),
    ]:
        if verbose:
            print(f"\n   [{nombre}] Calculando r_f...")
        rf = rf_fn(df_en_ventana)
        resultados[tarea] = resolver_ghp(
            df_candidatos.copy(), df_exentos.copy(),
            slots_disponibles, rf,
            nombre_problema=nombre, verbose=verbose,
        )
        resultados[f'rf_{tarea.split("_")[1]}'] = rf
 
    resultados['rf_unitario']  = resultados.pop('rf_task1', resultados.get('rf_validation'))
    resultados['rf_emisiones'] = resultados.pop('rf_task2', resultados.get('rf_emissions'))
    resultados['rf_coste']     = resultados.pop('rf_task3', resultados.get('rf_cost'))
 
    return resultados
 
 
# =============================================================================
# PASO 5: KPIs WP3
# =============================================================================
 
def calcular_kpis_ghp(
    df_ghp: pd.DataFrame,
    rf_series: pd.Series,
    nombre_escenario: str = 'GHP',
) -> dict:
    if df_ghp.empty:
        return {}
 
    df = df_ghp.copy()
    for col in ('total_delay', 'air_delay', 'ground_delay'):
        if col not in df.columns:
            df[col] = 0.0
 
    total_delay  = df['total_delay'].sum()
    mean_delay   = df['total_delay'].mean()
    air_delay    = df['air_delay'].sum()
    ground_delay = df['ground_delay'].sum()
    n_delayed    = (df['total_delay'] > 0).sum()
    n_flights    = len(df)
    max_air_obs    = df['air_delay'].max()
    infeasible_air = (df['air_delay'] > MAX_AIR_DELAY_MIN).sum()
 
    def _co2_rate_aire(row):
        return _co2_per_min(
            distancia_km=float(row.get('distancia_km', 0) or 0),
            asientos=int(row.get('size_seats_avg', 180) or 180),
            duracion_min=max(float(row.get('duracion_vuelo_min', 60) or 60), 1),
        )
 
    def _get_fegp_co2(recat):
        recat_str = str(recat).upper() if pd.notna(recat) else 'D'
        potencia_kw = FEGP_KW_PER_RECAT.get(recat_str, 72.0)
        return potencia_kw * (1 / 60.0) * GRID_EMISSION_FACTOR_KG_KWH
 
    co2_rate_aire   = df.apply(_co2_rate_aire, axis=1)
    co2_rate_tierra = df.get('recat', pd.Series('D', index=df.index)).apply(_get_fegp_co2)
 
    co2_aire_delay   = (co2_rate_aire   * df['air_delay']).sum()
    co2_tierra_delay = (co2_rate_tierra * df['ground_delay']).sum()
    co2_total_delay  = co2_aire_delay + co2_tierra_delay
 
    coste_total         = 0.0
    coste_primary_total = 0.0
    coste_react_total   = 0.0
 
    for i, row in df.iterrows():
        delay_real = float(row.get('total_delay', 0) or 0)
        if delay_real <= 0:
            continue
 
        tipo_icao = str(row.get('ATYP', row.get('f', ''))).upper().strip()
        recat     = str(row.get('recat', 'D')).upper()
        asientos  = int(row.get('size_seats_avg', 180) or 180)
        if asientos <= 0:
            asientos = 180
 
        tipo_cook = _mapear_tipo_cook(tipo_icao, recat, asientos)
        usar_hard = delay_real >= OTP_THRESHOLD_MIN
        coste_tabla = _interpolar_coste(tipo_cook, delay_real, usar_hard)
 
        pax_base   = _PAX_BASE_TABLE.get(tipo_cook, 130)
        pax_reales = max(int(asientos * LOAD_FACTOR_EU), 1)
        factor_pax = pax_reales / pax_base
 
        coste_vuelo  = coste_tabla * factor_pax
        coste_total += coste_vuelo
        if usar_hard:
            coste_react_total   += coste_vuelo
        else:
            coste_primary_total += coste_vuelo
 
    kpis_aerolinea = {}
    if 'airline' in df.columns:
        top_aerolineas = (
            df[df['total_delay'] > 0]
            .groupby('airline').size()
            .nlargest(4).index.tolist()
        )
        for al in top_aerolineas:
            sub = df[df['airline'] == al]
            kpis_aerolinea[al] = {
                'n_vuelos':    len(sub),
                'delay_medio': round(sub['total_delay'].mean(), 2),
                'delay_total': round(sub['total_delay'].sum(), 2),
                'delay_std':   round(sub['total_delay'].std(), 2),
            }
 
    return {
        'escenario':              nombre_escenario,
        'n_flights':              n_flights,
        'n_delayed':              int(n_delayed),
        'total_delay_min':        round(total_delay, 2),
        'mean_delay_min':         round(mean_delay, 2),
        'air_delay_min':          round(air_delay, 2),
        'ground_delay_min':       round(ground_delay, 2),
        'max_air_delay_obs':      round(max_air_obs, 2),
        'infeasible_air':         int(infeasible_air),
        'co2_aire_delay_kg':      round(co2_aire_delay, 2),
        'co2_tierra_delay_kg':    round(co2_tierra_delay, 2),
        'co2_total_delay_kg':     round(co2_total_delay, 2),
        'coste_delay_eur':        round(coste_total, 2),
        'coste_primary_eur':      round(coste_primary_total, 2),
        'coste_reactionary_eur':  round(coste_react_total, 2),
        'kpis_aerolinea':         kpis_aerolinea,
    }
 
 
# =============================================================================
# PASO 6: GENERACIÓN DE GRÁFICAS WP3
# =============================================================================
 
def generar_graficas_wp3(resultados_ghp: dict, resultados_gdp: dict = None) -> None:
    """
    Genera las tres figuras del WP3 y las guarda en disco.
 
    Figura 1 — CO2 por aerolínea (Scenario I, emissions optimisation).
               Barras apiladas: air delay CO2 + ground delay CO2.
               Se guarda en: figura1_co2_aerolinea.png
 
    Figura 2 — Curva de coste marginal vs delay (Cook & Tanner 2015).
               Muestra la no-linealidad para 5 tipos de aeronave.
               Se guarda en: figura2_curva_coste.png
 
    Figura 3 — Delay medio por aerolínea: GDP vs Scenario I vs Scenario II.
               Barras agrupadas para las 4 aerolíneas dominantes.
               Se guarda en: figura3_fairness_aerolineas.png
    """
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
 
    AIRLINES = ['VLG', 'RYR', 'EJU', 'DLH']
    COLOR_AIRE   = '#1D9E75'
    COLOR_TIERRA = '#9FE1CB'
    COLOR_GDP    = '#888780'
    COLOR_SC1    = '#1D9E75'
    COLOR_SC2    = '#378ADD'
 
    # ------------------------------------------------------------------
    # Figura 1: CO2 por aerolínea — Scenario I (emissions optimisation)
    # ------------------------------------------------------------------
    df_sc1 = resultados_ghp.get('task2_emissions', pd.DataFrame())
 
    if not df_sc1.empty and 'airline' in df_sc1.columns:
 
        def _co2_aire_row(row):
            return _co2_per_min(
                distancia_km=float(row.get('distancia_km', 0) or 0),
                asientos=int(row.get('size_seats_avg', 180) or 180),
                duracion_min=max(float(row.get('duracion_vuelo_min', 60) or 60), 1),
            )
 
        def _co2_tierra_row(recat):
            recat_str = str(recat).upper() if pd.notna(recat) else 'D'
            potencia_kw = FEGP_KW_PER_RECAT.get(recat_str, 72.0)
            return potencia_kw * (1 / 60.0) * GRID_EMISSION_FACTOR_KG_KWH
 
        df_sc1 = df_sc1.copy()
        df_sc1['_co2_rate_aire']   = df_sc1.apply(_co2_aire_row, axis=1)
        df_sc1['_co2_rate_tierra'] = df_sc1['recat'].apply(_co2_tierra_row) \
            if 'recat' in df_sc1.columns else 0.34
 
        co2_aire_al   = {}
        co2_tierra_al = {}
        for al in AIRLINES:
            sub = df_sc1[df_sc1['airline'] == al]
            co2_aire_al[al]   = (sub['_co2_rate_aire']   * sub['air_delay']).sum()
            co2_tierra_al[al] = (sub['_co2_rate_tierra'] * sub['ground_delay']).sum()
 
        aire_vals   = [co2_aire_al.get(a, 0)   for a in AIRLINES]
        tierra_vals = [co2_tierra_al.get(a, 0) for a in AIRLINES]
 
        x = np.arange(len(AIRLINES))
        w = 0.5
 
        fig1, ax1 = plt.subplots(figsize=(8, 5))
        ax1.bar(x, aire_vals,   w, label='Air delay CO₂',    color=COLOR_AIRE)
        ax1.bar(x, tierra_vals, w, label='Ground delay CO₂', color=COLOR_TIERRA,
                bottom=aire_vals)
 
        for i, (a, g) in enumerate(zip(aire_vals, tierra_vals)):
            total = a + g
            if total > 0:
                ax1.text(i, total + 20, f'{total:.0f} kg',
                         ha='center', va='bottom', fontsize=9)
 
        ax1.set_xticks(x)
        ax1.set_xticklabels(AIRLINES)
        ax1.set_ylabel('CO₂ (kg)')
        ax1.set_xlabel('Airline')
        ax1.set_title('CO₂ delay emissions by airline — Scenario I (emissions optimisation)')
        ax1.legend()
        fig1.tight_layout()
        fig1.savefig('figura1_co2_aerolinea.png', dpi=150)
        plt.close(fig1)
        print("   [Figura 1] Guardada: figura1_co2_aerolinea.png")
 
    # ------------------------------------------------------------------
    # Figura 2: Curva de coste (Cook & Tanner 2015) — no-linealidad
    # ------------------------------------------------------------------
    delays = np.linspace(0, 300, 600)
 
    tipos_plot  = ['A320', 'B738', 'A332', 'B744', 'E190']
    colores_plot = ['#1D9E75', '#378ADD', '#D85A30', '#7F77DD', '#888780']
    labels_plot  = ['A320 (narrowbody)', 'B738 (narrowbody)',
                    'A332 (widebody)',   'B744 (heavy)',
                    'E190 (regional)']
 
    fig2, ax2 = plt.subplots(figsize=(9, 5))
    for tipo, color, label in zip(tipos_plot, colores_plot, labels_plot):
        costes = []
        pax_base = _PAX_BASE_TABLE.get(tipo, 130)
        pax_reales = pax_base * LOAD_FACTOR_EU
        factor_pax = pax_reales / pax_base
        for d in delays:
            usar_hard = d >= OTP_THRESHOLD_MIN
            c = _interpolar_coste(tipo, d, usar_hard)
            costes.append(c * factor_pax)
        ax2.plot(delays, costes, label=label, color=color, linewidth=1.8)
 
    ax2.axvline(OTP_THRESHOLD_MIN, color='gray', linestyle='--',
                linewidth=1.0, label=f'OTP threshold ({OTP_THRESHOLD_MIN} min)')
    ax2.set_xlabel('Delay (min)')
    ax2.set_ylabel('Total delay cost (EUR)')
    ax2.set_title('Delay cost by aircraft type — Cook & Tanner 2015 (non-linear)')
    ax2.legend(fontsize=9)
    fig2.tight_layout()
    fig2.savefig('figura2_curva_coste.png', dpi=150)
    plt.close(fig2)
    print("   [Figura 2] Guardada: figura2_curva_coste.png")

# =============================================================================
# MODO DEBUG
# =============================================================================
 
if __name__ == '__main__':
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
 
    import lib_data_prep as prep
    import lib_gdp_core  as gdp
 
    print("🛠️  MODO DEBUG: lib_ghp_solver.py")
    base     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p_vuelos = config.traffic_file()
    p_flota  = config.FLEET_TABLE
 
    params    = CFG.to_params_dict()
    df_vuelos = prep.preparar_vuelos(p_vuelos, p_flota)
 
    resultados_gdp = gdp.ejecutar_nucleo_gdp(df_vuelos, params)
    df_etiquetado  = resultados_gdp['vuelos_asignados']
    slots_list     = list(resultados_gdp['slots']['slot_start_min'])
 
    resultados_ghp = ejecutar_ghp_completo(
        df_etiquetado, slots_list, params, verbose=True
    )
 
    df_val    = resultados_ghp['task1_validation']
    rf_1      = resultados_ghp['rf_unitario']
    kpis_val  = calcular_kpis_ghp(df_val, rf_1, 'Task1_Validation')
    coste_gdp = resultados_gdp['vuelos_asignados']['total_delay'].sum()
 
    print(f"\n{'='*50}")
    print(f"  VALIDACIÓN Task 1:")
    print(f"  Delay total GHP: {kpis_val['total_delay_min']:.2f} min")
    print(f"  Delay total GDP: {coste_gdp:.2f} min")
    diff_pct = abs(kpis_val['total_delay_min'] - coste_gdp) / max(coste_gdp, 1) * 100
    print(f"  Diferencia: {diff_pct:.1f}%  {'✅ OK' if diff_pct < 5 else '⚠️ Revisar'}")
    print(f"{'='*50}")
 
    for escenario, rf_key in [
        ('task1_validation', 'rf_unitario'),
        ('task2_emissions',  'rf_emisiones'),
        ('task3_cost',       'rf_coste'),
    ]:
        df_esc = resultados_ghp[escenario]
        rf_esc = resultados_ghp[rf_key]
        kpis   = calcular_kpis_ghp(df_esc, rf_esc, escenario)
        print(f"\n  {escenario.upper()}:")
        print(f"    Total delay:        {kpis['total_delay_min']:.1f} min")
        print(f"    Air delay:          {kpis['air_delay_min']:.1f} min")
        print(f"    Ground delay:       {kpis['ground_delay_min']:.1f} min")
        print(f"    CO2 aire delay:     {kpis['co2_aire_delay_kg']:.1f} kg")
        print(f"    CO2 tierra delay:   {kpis['co2_tierra_delay_kg']:.1f} kg")
        print(f"    CO2 total delay:    {kpis['co2_total_delay_kg']:.1f} kg")
        print(f"    Coste delay (Cook): {kpis['coste_delay_eur']:.0f} EUR")
        print(f"      (primary flights):  {kpis['coste_primary_eur']:.0f} EUR")
        print(f"      (reactionary flts): {kpis['coste_reactionary_eur']:.0f} EUR")
        print(f"    Air infeasible:     {kpis['infeasible_air']} vuelos")
        if kpis['kpis_aerolinea']:
            print(f"    KPIs aerolíneas:")
            for al, kpi in kpis['kpis_aerolinea'].items():
                print(f"      {al}: n={kpi['n_vuelos']}, "
                      f"delay_medio={kpi['delay_medio']:.1f}, "
                      f"delay_total={kpi['delay_total']:.1f}, "
                      f"delay_std={kpi['delay_std']:.1f}")
 
    # ------------------------------------------------------------------
    # Generar las figuras del WP3
    # ------------------------------------------------------------------
    print(f"\n{'='*50}")
    print("  Generando figuras WP3...")
    generar_graficas_wp3(resultados_ghp, resultados_gdp)
    print("  Figuras generadas correctamente.")
    print(f"{'='*50}")