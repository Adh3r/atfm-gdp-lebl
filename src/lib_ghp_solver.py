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
#     emissions   c_ft = e_f (t - ETA_f), e_f = ground CO2 per minute (APU at
#                 the gate, ICAO Doc 9889).
#     cost        c_ft = cost of delay of flight f arriving at t (cost_model):
#                 Cook & Tanner at-gate primary cost of the delay, plus the
#                 reactionary cost of the part of the delay that exceeds the
#                 turnaround buffer of the aircraft's next rotation (computed
#                 from its registration mark), scaled by real passengers.
#                 Short delays absorbed by the turnaround cost little; long
#                 delays that propagate cost much more, as WP3 Task 3 asks.
# =============================================================================

import os

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from config import FS_CONTROLLED, FS_NOT_AFFECTED
from cost_model import (
    DEFAULT_COST_BASE, _cook_type_and_factor, primary_cost, ground_co2_kg_per_min,
    reactionary_cost, flight_cost, turnaround_buffer,
)
from lib_gdp_core import compute_delays

# Maximum feasible air (holding) delay for exempt flights (WP3 Task 1). Project
# choice, justified in the report; the result is checked in every run.
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
_FORBIDDEN = 1e12

OBJECTIVES = ('validation', 'emissions', 'cost')

# Scenario keys in the results dictionary.
SCENARIOS = {
    'validation': 'GHP_Validation',
    'emissions':  'GHP_Emissions',
    'cost':       'GHP_Cost',
}


# =============================================================================
# EXEMPT FLIGHTS: served first, in ETA order (air delay)
# =============================================================================

def assign_exempt(df_exempt: pd.DataFrame, slots: np.ndarray) -> tuple[dict, set]:
    """First free slot at or after the ETA, in ETA order (same rule as RBS)."""
    assigned, taken = {}, set()
    for idx, eta in df_exempt['minutes_eta'].sort_values(kind='stable').items():
        free = [s for s in slots if s >= eta and s not in taken]
        if not free:
            raise RuntimeError(f"No slot left for exempt flight {idx} (ETA {eta}).")
        assigned[idx] = free[0]
        taken.add(free[0])
    return assigned, taken


# =============================================================================
# COST MATRIX c_ft FOR THE CONTROLLED FLIGHTS
# =============================================================================

def build_cost_matrix(
    df_controlled: pd.DataFrame,
    slots: np.ndarray,
    objective: str,
    max_ground_delay: float | None = MAX_GROUND_DELAY_MIN,
    base: str = DEFAULT_COST_BASE,
    mtt_extra: float = 0.0,
) -> np.ndarray:
    """c_ft for every controlled flight f and free slot t.

    Forbidden (infinite cost) when t < ETA_f, or when t - ETA_f exceeds
    max_ground_delay. base and mtt_extra select the cost model of the cost
    objective (cost_model.flight_cost), for the sensitivity analysis.
    """
    if objective not in OBJECTIVES:
        raise ValueError(f"objective must be one of {OBJECTIVES}")
    eta = df_controlled['minutes_eta'].to_numpy(dtype=float)
    delay = slots[None, :] - eta[:, None]                     # t - ETA_f
    feasible = delay >= -1e-9
    d = np.clip(delay, 0, None)

    if objective == 'validation':
        c = d.copy()
    elif objective == 'emissions':
        e_f = ground_co2_kg_per_min(df_controlled['size_seats_avg'])
        c = e_f[:, None] * d
    else:  # cost
        c = np.empty_like(d)
        buffer = turnaround_buffer(df_controlled, mtt_extra).to_numpy(dtype=float)
        for i, (_, row) in enumerate(df_controlled.iterrows()):
            cook_type, fpax = _cook_type_and_factor(row)
            c[i] = flight_cost(cook_type, fpax, d[i], 0.0, buffer[i], base)

    if max_ground_delay is not None:
        feasible &= delay <= max_ground_delay + 1e-9
    c[~feasible] = _FORBIDDEN
    return c


# =============================================================================
# SOLVER
# =============================================================================

def solve_ghp(
    df_affected: pd.DataFrame,
    available_slots,
    objective: str,
    max_ground_delay: float | None = MAX_GROUND_DELAY_MIN,
    base: str = DEFAULT_COST_BASE,
    mtt_extra: float = 0.0,
) -> pd.DataFrame:
    """
    Solve one GHP instance and return the affected flights with their CTA,
    CTD and delays (same columns as the RBS result).

    Raises:
        RuntimeError: if some controlled flight cannot be given a slot.
    """
    slots = np.array(sorted(available_slots), dtype=float)
    controlled_mask = df_affected['flight_status'] == FS_CONTROLLED
    df_ctrl = df_affected[controlled_mask]
    df_exempt = df_affected[~controlled_mask]

    assigned, taken = assign_exempt(df_exempt, slots)
    free = np.array([s for s in slots if s not in taken], dtype=float)

    if len(df_ctrl):
        if len(free) < len(df_ctrl):
            raise RuntimeError("Fewer free slots than controlled flights.")
        cost = build_cost_matrix(df_ctrl, free, objective, max_ground_delay, base, mtt_extra)
        rows, cols = linear_sum_assignment(cost)
        if (cost[rows, cols] >= _FORBIDDEN).any():
            raise RuntimeError(
                "Infeasible GHP: a controlled flight has no slot between its ETA and "
                f"ETA + {max_ground_delay} min (raise MAX_GROUND_DELAY_MIN).")
        for f, t in zip(rows, cols):
            assigned[df_ctrl.index[f]] = free[t]

    res = df_affected.copy()
    res['assigned_slot'] = pd.Series(assigned)
    return compute_delays(res).sort_values('minutes_eta', kind='stable')


def run_ghp(
    df_classified: pd.DataFrame,
    available_slots: list,
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
    df = df_classified[df_classified['flight_status'] != FS_NOT_AFFECTED]
    results = {}
    for objective, key in SCENARIOS.items():
        results[key] = solve_ghp(df, available_slots, objective, max_ground_delay)
        if verbose:
            r = results[key]
            print(f"   [{key}] total delay {r['total_delay'].sum():.1f} min, "
                  f"max {r['total_delay'].max():.1f} min")
    results['max_air_delay_ok'] = all(
        results[k]['air_delay'].max() <= MAX_AIR_DELAY_MIN for k in SCENARIOS.values()
    )
    return results


# =============================================================================
# SENSITIVITY OF THE COST OBJECTIVE (WP3 Task 3: "vary the values of r_f")
# =============================================================================

def _airline_rsd(df: pd.DataFrame, top_n: int = 4) -> float:
    top = df['airline'].value_counts().head(top_n).index
    means = df[df['airline'].isin(top)].groupby('airline')['total_delay'].mean()
    return float(means.std(ddof=0) / means.mean() * 100) if means.mean() > 0 else 0.0


SENSITIVITY_MODELS = [
    ('primary', 0.0, 'Primary only'),
    ('rotation', 0.0, 'Rotation (default)'),
    ('full', 0.0, "Cook 'full' (network average)"),
    ('rotation', -10.0, 'Rotation, MTT -10 min'),
    ('rotation', 15.0, 'Rotation, MTT +15 min'),
]


def cost_model_sensitivity(
    df_rbs: pd.DataFrame,
    available_slots,
    max_ground_delay: float | None = MAX_GROUND_DELAY_MIN,
) -> pd.DataFrame:
    """
    Re-solve the cost GHP with each cost model and evaluate every solution
    under all of them.

    Cost models (cost_model.flight_cost): primary only, rotation (the
    default), Cook's network-average 'full', and rotation with the minimum
    turnaround times shifted by -10 / +15 min.

    Returns one row per solution (RBS first) with its cost under each model,
    the saving against RBS under the model it was optimised for, the maximum
    delay and the airline fairness index.
    """
    from cost_model import flight_costs

    df = df_rbs[df_rbs['flight_status'] != FS_NOT_AFFECTED]

    def cost(sol, base, mtt):
        return flight_costs(sol, base=base, mtt_extra=mtt)['delay_cost_eur'].sum()

    solutions = [('GDP (RBS)', None, df)]
    for base, mtt, name in SENSITIVITY_MODELS:
        solutions.append((f'GHP cost, optimised on: {name}', (base, mtt),
                           solve_ghp(df, available_slots, 'cost', max_ground_delay, base, mtt)))

    rows = []
    for name, model, sol in solutions:
        row = {'Solution': name}
        for base, mtt, label in SENSITIVITY_MODELS:
            row[f'Cost, {label} (EUR)'] = round(cost(sol, base, mtt), 0)
        if model is not None:
            ref = cost(df, *model)
            row['Saving vs RBS, own model (%)'] = round((ref - cost(sol, *model)) / ref * 100, 1)
        else:
            row['Saving vs RBS, own model (%)'] = 0.0
        row['Max delay (min)'] = round(sol['total_delay'].max(), 1)
        row['Airline fairness RSD (%)'] = round(_airline_rsd(sol), 1)
        rows.append(row)
    return pd.DataFrame(rows)


# =============================================================================
# WP3 FIGURES
# =============================================================================

def plot_wp3_figures(scenarios: dict, out_dir: str, top_n: int = 4) -> list[str]:
    """
    WP3 figures (SVG), saved in out_dir:
      1. delay CO2 by airline in the emissions scenario (air vs ground);
      2. cost of delay vs delay for an A320: primary only, with the reactionary
         cost of a tight and of a loose turnaround, and Cook's network average;
      3. mean delay per airline: GDP vs both GHP scenarios (fairness).

    scenarios: {'GDP': df, 'GHP_Emissions': df, 'GHP_Cost': df}
    """
    import matplotlib.pyplot as plt

    os.makedirs(out_dir, exist_ok=True)
    paths = []
    gdp = scenarios['GDP']
    airlines = gdp['airline'].value_counts().head(top_n).index.tolist()

    # --- 1. CO2 of delay by airline, emissions scenario ---
    df = scenarios['GHP_Emissions']
    air_rate = df['flight_co2_kg'] / df['block_time_min'].clip(lower=1)
    ground_rate = ground_co2_kg_per_min(df['size_seats_avg'])
    air = (air_rate * df['air_delay']).groupby(df['airline']).sum().reindex(airlines, fill_value=0)
    ground = (ground_rate * df['ground_delay']).groupby(df['airline']).sum().reindex(airlines, fill_value=0)
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(airlines, air, label='Air delay CO₂', color='#1D9E75')
    ax.bar(airlines, ground, bottom=air, label='Ground delay CO₂ (APU)', color='#9FE1CB')
    ax.set_ylabel('CO₂ (kg)')
    ax.set_xlabel('Airline')
    ax.set_title('Delay CO₂ by airline — GHP, emissions objective')
    ax.legend()
    fig.tight_layout()
    paths.append(os.path.join(out_dir, 'fig1_co2_by_airline.svg'))
    fig.savefig(paths[-1], format='svg')
    plt.close(fig)

    # --- 2. Cost of delay curves ---
    delays = np.linspace(0, 300, 601)
    fig, ax = plt.subplots(figsize=(9, 5))
    prim = primary_cost('A320', delays, 'at_gate')
    curves = (
        ('Primary only (no knock-on)', prim, '#4A6FA5', '-'),
        ('Turnaround buffer 15 min', prim + reactionary_cost('A320', np.clip(delays - 15, 0, None)), '#E67E22', '-'),
        ('Turnaround buffer 60 min', prim + reactionary_cost('A320', np.clip(delays - 60, 0, None)), '#2ECC71', '-'),
        ("Cook & Tanner 'full' (network average)", prim + reactionary_cost('A320', delays), 'gray', '--'),
    )
    for label, y, color, style in curves:
        ax.plot(delays, y / 1000, label=label, color=color, linestyle=style, linewidth=1.8)
    ax.set_xlabel('Ground delay (min)')
    ax.set_ylabel('Cost of delay (k€, 2014)')
    ax.set_title('Cost of delay of an A320 (Cook & Tanner 2015, at gate, base scenario)')
    ax.legend()
    fig.tight_layout()
    paths.append(os.path.join(out_dir, 'fig2_cost_of_delay_curves.svg'))
    fig.savefig(paths[-1], format='svg')
    plt.close(fig)

    # --- 3. Fairness: mean delay per airline ---
    names = list(scenarios)
    means = pd.DataFrame({
        n: scenarios[n].groupby('airline')['total_delay'].mean().reindex(airlines)
        for n in names
    })
    x = np.arange(len(airlines))
    w = 0.8 / len(names)
    fig, ax = plt.subplots(figsize=(9, 5))
    for k, n in enumerate(names):
        ax.bar(x + (k - (len(names) - 1) / 2) * w, means[n], w, label=n.replace('_', ' '))
    ax.set_xticks(x)
    ax.set_xticklabels(airlines)
    ax.set_ylabel('Mean delay per flight (min)')
    ax.set_title(f'Mean delay of the {top_n} airlines with most affected flights')
    ax.legend()
    fig.tight_layout()
    paths.append(os.path.join(out_dir, 'fig3_fairness_by_airline.svg'))
    fig.savefig(paths[-1], format='svg')
    plt.close(fig)
    return paths


# =============================================================================
# DEBUG MODE
# =============================================================================

if __name__ == '__main__':
    import config
    import lib_data_prep as prep
    import lib_gdp_core as gdp
    from config import CFG

    params = CFG.to_params_dict()
    df_flights = prep.prepare_flights(config.traffic_file(), config.FLEET_TABLE)
    res_gdp = gdp.run_gdp(df_flights, params, run_compression=False)
    rbs = res_gdp['allocation']
    res = run_ghp(rbs, list(res_gdp['slots']['slot_start_min']), params, verbose=True)
    print(f"RBS total delay: {rbs['total_delay'].sum():.1f} min "
          f"(validation GHP: {res['GHP_Validation']['total_delay'].sum():.1f})")
