# =============================================================================
# src/lib_ghp_solver.py
# WP3: Ground Holding Problem (GHP)
#
# MODEL (deterministic GHP, Ball 2007, as in the course):
#     min   sum_f sum_t  c_ft x_ft
#     s.t.  sum_f x_ft <= b_t = 1      every slot holds at most one flight
#           sum_t x_ft  = 1            every flight lands in exactly one slot
#           x_ft = 0 for t < ETA_f     no flight arrives before its ETA
#           x_ft in {0, 1}
#     with  c_ft = r_f(t) * (t - ETA_f).
#
# The flights are the affected flights of WP2 and the slots are the WP2 slots
# (one slot per interval, so b_t = 1). Following the WP3 statement ("Task 1:
# additional feasibility constraints"), exempt flights keep the GDP philosophy:
# they are served first, in ETA order, and absorb air delay, which must stay
# below MAX_AIR_DELAY_MIN. The controlled flights are then optimised over the
# remaining slots.
#
# With b_t = 1 the problem is a rectangular linear assignment problem, so the
# integer programme is solved exactly by scipy.optimize.linear_sum_assignment.
# Any cost matrix is allowed, which lets the cost scenario use the non-linear
# Cook & Tanner curve directly: c_ft = cost_f(t - ETA_f), i.e. r_f(t) varies
# with the delay as the statement asks.
#
# OBJECTIVES (WP3 tasks):
#     validation  r_f = 1: minimises total delay. Must give the same total
#                 delay as RBS (Task 1 validation; not a scenario to report:
#                 every slot permutation of the same slots is optimal).
#     emissions   c_ft = e_f (t - ETA_f), e_f = ground CO2 per minute (FEGP).
#     cost        c_ft = Cook & Tanner at-gate tactical cost of the delay,
#                 primary below 15 min and full (with reactionary) from 15 min,
#                 scaled by the flight's real passengers.
# =============================================================================

import os

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from config import FS_CONTROLLED, FS_NOT_AFFECTED
from cost_model import (
    _mapear_tipo_cook, co2_tierra_kg_por_min, coste_tabla_vec,
    factor_pasajeros,
)
from lib_gdp_core import calcular_delays

# Maximum feasible air (holding) delay for exempt flights. Project choice,
# from EUROCONTROL turnaround/holding references plus a 20 min margin (report).
MAX_AIR_DELAY_MIN = 90

# Maximum ground delay for controlled flights (WP3 Task 1: "very high ground
# delays should be avoided"). Beyond about 120 min the Cook & Tanner reactionary
# cost saturates, so an uncapped cost optimiser piles very long delays (over
# 10 h) on a few flights. The cap is the last breakpoint of the cost tables
# (300 min): delays stay inside the range where the cost model is backed by
# data. None disables it. It must be at least the RBS maximum delay, because
# serving flights in ETA order already minimises the maximum delay.
MAX_GROUND_DELAY_MIN = 300

# Cost of a forbidden assignment (slot earlier than the flight's ETA).
_PROHIBIDO = 1e12

OBJETIVOS = ('validation', 'emissions', 'cost')

# Scenario keys in the results dictionary.
ESCENARIOS = {
    'validation': 'GHP_Validation',
    'emissions':  'GHP_Emissions',
    'cost':       'GHP_Cost',
}


# =============================================================================
# EXEMPT FLIGHTS: served first, in ETA order (air delay)
# =============================================================================

def asignar_exentos(df_exentos: pd.DataFrame, slots: np.ndarray) -> tuple[dict, set]:
    """First free slot at or after the ETA, in ETA order (same rule as RBS)."""
    asignados, ocupados = {}, set()
    for idx, eta in df_exentos['minutes_eta'].sort_values(kind='stable').items():
        libres = [s for s in slots if s >= eta and s not in ocupados]
        if not libres:
            raise RuntimeError(f"No slot left for exempt flight {idx} (ETA {eta}).")
        asignados[idx] = libres[0]
        ocupados.add(libres[0])
    return asignados, ocupados


# =============================================================================
# COST MATRIX c_ft FOR THE CONTROLLED FLIGHTS
# =============================================================================

def matriz_costes(
    df_controlados: pd.DataFrame,
    slots: np.ndarray,
    objetivo: str,
    max_ground_delay: float | None = MAX_GROUND_DELAY_MIN,
) -> np.ndarray:
    """c_ft for every controlled flight f and free slot t.

    Forbidden (infinite cost) when t < ETA_f, or when t - ETA_f exceeds
    max_ground_delay.
    """
    if objetivo not in OBJETIVOS:
        raise ValueError(f"objetivo must be one of {OBJETIVOS}")
    eta = df_controlados['minutes_eta'].to_numpy(dtype=float)
    delay = slots[None, :] - eta[:, None]                     # t - ETA_f
    factible = delay >= -1e-9
    d = np.clip(delay, 0, None)

    if objetivo == 'validation':
        c = d.copy()
    elif objetivo == 'emissions':
        e_f = df_controlados['recat'].map(co2_tierra_kg_por_min).to_numpy(dtype=float)
        c = e_f[:, None] * d
    else:  # cost
        c = np.empty_like(d)
        for i, (_, fila) in enumerate(df_controlados.iterrows()):
            asientos = fila.get('size_seats_avg', 180)
            asientos = 180 if pd.isna(asientos) else asientos
            tipo = _mapear_tipo_cook(fila.get('ATYP', ''), fila.get('recat', 'D'), int(asientos))
            c[i] = coste_tabla_vec(tipo, d[i], 'at_gate') * factor_pasajeros(tipo, asientos)

    if max_ground_delay is not None:
        factible &= delay <= max_ground_delay + 1e-9
    c[~factible] = _PROHIBIDO
    return c


# =============================================================================
# SOLVER
# =============================================================================

def resolver_ghp(
    df_afectados: pd.DataFrame,
    slots_disponibles,
    objetivo: str,
    max_ground_delay: float | None = MAX_GROUND_DELAY_MIN,
) -> pd.DataFrame:
    """
    Solve one GHP instance and return the affected flights with their CTA,
    CTD and delays (same columns as the RBS result).

    Raises:
        RuntimeError: if some controlled flight cannot be given a slot.
    """
    slots = np.array(sorted(slots_disponibles), dtype=float)
    controlados = df_afectados['flight_status'] == FS_CONTROLLED
    df_ctrl = df_afectados[controlados]
    df_exen = df_afectados[~controlados]

    asignados, ocupados = asignar_exentos(df_exen, slots)
    libres = np.array([s for s in slots if s not in ocupados], dtype=float)

    if len(df_ctrl):
        if len(libres) < len(df_ctrl):
            raise RuntimeError("Fewer free slots than controlled flights.")
        coste = matriz_costes(df_ctrl, libres, objetivo, max_ground_delay)
        filas, columnas = linear_sum_assignment(coste)
        if (coste[filas, columnas] >= _PROHIBIDO).any():
            raise RuntimeError(
                "Infeasible GHP: a controlled flight has no slot between its ETA and "
                f"ETA + {max_ground_delay} min (raise MAX_GROUND_DELAY_MIN).")
        for f, t in zip(filas, columnas):
            asignados[df_ctrl.index[f]] = libres[t]

    res = df_afectados.copy()
    res['assigned_slot'] = pd.Series(asignados)
    return calcular_delays(res).sort_values('minutes_eta', kind='stable')


def ejecutar_ghp_completo(
    df_vuelos_etiquetados: pd.DataFrame,
    slots_disponibles: list,
    params: dict | None = None,
    verbose: bool = False,
    max_ground_delay: float | None = MAX_GROUND_DELAY_MIN,
) -> dict:
    """
    Run the three GHP objectives on the affected flights.

    Returns:
        {'GHP_Validation': df, 'GHP_Emissions': df, 'GHP_Cost': df,
         'max_air_delay_ok': bool}
    """
    df = df_vuelos_etiquetados[df_vuelos_etiquetados['flight_status'] != FS_NOT_AFFECTED]
    resultados = {}
    for objetivo, clave in ESCENARIOS.items():
        resultados[clave] = resolver_ghp(df, slots_disponibles, objetivo, max_ground_delay)
        if verbose:
            r = resultados[clave]
            print(f"   [{clave}] total delay {r['total_delay'].sum():.1f} min, "
                  f"max {r['total_delay'].max():.1f} min")
    resultados['max_air_delay_ok'] = all(
        resultados[k]['air_delay'].max() <= MAX_AIR_DELAY_MIN for k in ESCENARIOS.values()
    )
    return resultados


# =============================================================================
# WP3 FIGURES
# =============================================================================

def generar_graficas_wp3(escenarios: dict, out_dir: str, top_n: int = 4) -> list[str]:
    """
    WP3 figures (SVG), saved in out_dir:
      1. delay CO2 by airline in the emissions scenario (air vs ground);
      2. Cook & Tanner at-gate cost of delay vs delay for five aircraft types;
      3. mean delay per airline: GDP vs both GHP scenarios (fairness).

    escenarios: {'GDP': df, 'GHP_Emissions': df, 'GHP_Cost': df}
    """
    import matplotlib.pyplot as plt

    os.makedirs(out_dir, exist_ok=True)
    rutas = []
    gdp = escenarios['GDP']
    aerolineas = gdp['airline'].value_counts().head(top_n).index.tolist()

    # --- 1. CO2 of delay by airline, emissions scenario ---
    df = escenarios['GHP_Emissions']
    tasa_aire = df['co2_kg_vuelo'] / df['duracion_vuelo_min'].clip(lower=1)
    tasa_tierra = df['recat'].map(co2_tierra_kg_por_min)
    aire = (tasa_aire * df['air_delay']).groupby(df['airline']).sum().reindex(aerolineas, fill_value=0)
    tierra = (tasa_tierra * df['ground_delay']).groupby(df['airline']).sum().reindex(aerolineas, fill_value=0)
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(aerolineas, aire, label='Air delay CO₂', color='#1D9E75')
    ax.bar(aerolineas, tierra, bottom=aire, label='Ground delay CO₂ (FEGP)', color='#9FE1CB')
    ax.set_ylabel('CO₂ (kg)')
    ax.set_xlabel('Airline')
    ax.set_title('Delay CO₂ by airline — GHP, emissions objective')
    ax.legend()
    fig.tight_layout()
    rutas.append(os.path.join(out_dir, 'fig1_co2_by_airline.svg'))
    fig.savefig(rutas[-1], format='svg')
    plt.close(fig)

    # --- 2. Cost of delay curves ---
    delays = np.linspace(0, 300, 601)
    fig, ax = plt.subplots(figsize=(9, 5))
    for tipo in ('A320', 'B738', 'E190', 'A332', 'B744'):
        ax.plot(delays, coste_tabla_vec(tipo, delays, 'at_gate') / 1000, label=tipo, linewidth=1.8)
    ax.axvline(15, color='gray', linestyle='--', linewidth=1, label='OTP threshold (15 min)')
    ax.set_xlabel('Delay (min)')
    ax.set_ylabel('Cost of delay (k€, 2014)')
    ax.set_title('At-gate tactical cost of delay — Cook & Tanner (2015), base scenario')
    ax.legend()
    fig.tight_layout()
    rutas.append(os.path.join(out_dir, 'fig2_cost_of_delay_curves.svg'))
    fig.savefig(rutas[-1], format='svg')
    plt.close(fig)

    # --- 3. Fairness: mean delay per airline ---
    nombres = list(escenarios)
    medias = pd.DataFrame({
        n: escenarios[n].groupby('airline')['total_delay'].mean().reindex(aerolineas)
        for n in nombres
    })
    x = np.arange(len(aerolineas))
    w = 0.8 / len(nombres)
    fig, ax = plt.subplots(figsize=(9, 5))
    for k, n in enumerate(nombres):
        ax.bar(x + (k - (len(nombres) - 1) / 2) * w, medias[n], w, label=n.replace('_', ' '))
    ax.set_xticks(x)
    ax.set_xticklabels(aerolineas)
    ax.set_ylabel('Mean delay per flight (min)')
    ax.set_title(f'Mean delay of the {top_n} airlines with most affected flights')
    ax.legend()
    fig.tight_layout()
    rutas.append(os.path.join(out_dir, 'fig3_fairness_by_airline.svg'))
    fig.savefig(rutas[-1], format='svg')
    plt.close(fig)
    return rutas


# =============================================================================
# DEBUG MODE
# =============================================================================

if __name__ == '__main__':
    import config
    import lib_data_prep as prep
    import lib_gdp_core as gdp
    from config import CFG

    params = CFG.to_params_dict()
    df_vuelos = prep.preparar_vuelos(config.traffic_file(), config.FLEET_TABLE)
    res_gdp = gdp.ejecutar_nucleo_gdp(df_vuelos, params, run_compression=False)
    rbs = res_gdp['vuelos_asignados']
    res = ejecutar_ghp_completo(rbs, list(res_gdp['slots']['slot_start_min']), params, verbose=True)
    print(f"RBS total delay: {rbs['total_delay'].sum():.1f} min "
          f"(validation GHP: {res['GHP_Validation']['total_delay'].sum():.1f})")
