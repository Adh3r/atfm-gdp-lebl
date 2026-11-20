"""Tests for the WP3 Ground Holding Problem (src/lib_ghp_solver.py) and the cost model."""

import itertools
import os
import sys

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import Bounds, LinearConstraint, milp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

import config  # noqa: E402
import cost_model  # noqa: E402
import lib_data_prep as prep  # noqa: E402
import lib_gdp_core as gdp  # noqa: E402
import lib_ghp_solver as ghp  # noqa: E402
from config import CFG, FS_CONTROLLED, FS_EXEMPT_INTERNATIONAL  # noqa: E402
from lib_metrics import evaluar_escenario  # noqa: E402

PARAMS = CFG.to_params_dict()


# -----------------------------------------------------------------------------
# Reference implementation: Dènia Cabrera's original integer programme
# (lib_gdp_core.resolve_ghp_intlinprog, April 2026), generalised to an
# arbitrary cost matrix. It builds the full binary programme of the course
# formulation and solves it with SciPy's MILP solver, independently of the
# assignment-problem solver used in production.
# -----------------------------------------------------------------------------

def ghp_milp_reference(cost_matrix: np.ndarray) -> float:
    n_f, n_t = cost_matrix.shape
    c = cost_matrix.flatten()
    a_vuelos = np.zeros((n_f, n_f * n_t))          # each flight exactly one slot
    for f in range(n_f):
        a_vuelos[f, f * n_t:(f + 1) * n_t] = 1
    a_slots = np.zeros((n_t, n_f * n_t))           # each slot at most one flight
    for t in range(n_t):
        a_slots[t, t::n_t] = 1
    res = milp(
        c=c,
        constraints=[LinearConstraint(a_vuelos, 1, 1), LinearConstraint(a_slots, 0, 1)],
        integrality=np.ones_like(c),
        bounds=Bounds(0, 1),
    )
    assert res.success
    return float(res.fun)


def _toy_flights(n=6, seed=0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({
        'ARCID': [f'F{i}' for i in range(n)],
        'ATYP': rng.choice(['A320', 'B738', 'E190', 'A332'], n),
        'recat': rng.choice(['D', 'E', 'B'], n),
        'size_seats_avg': rng.choice([100, 180, 280], n),
        'minutes_eta': np.sort(rng.integers(360, 380, n)).astype(float),
        'minutes_etd': 300.0,
        'flight_status': FS_CONTROLLED,
        'airline': 'TST',
    })
    return df


@pytest.mark.parametrize('objetivo', ghp.OBJETIVOS)
def test_assignment_solver_matches_milp_reference(objetivo):
    df = _toy_flights()
    slots = np.arange(360, 360 + 3 * 10, 3, dtype=float)          # 10 slots, 3 min apart
    coste = ghp.matriz_costes(df, slots, objetivo)
    res = ghp.resolver_ghp(df, slots, objetivo)
    idx = {s: k for k, s in enumerate(slots)}
    obtenido = sum(coste[i, idx[s]] for i, s in enumerate(res.loc[df.index, 'CTA']))
    assert obtenido == pytest.approx(ghp_milp_reference(coste), rel=1e-9)


def test_assignment_solver_matches_brute_force():
    df = _toy_flights(n=4, seed=3)
    slots = np.array([360, 363, 366, 369, 372, 375], dtype=float)
    coste = ghp.matriz_costes(df, slots, 'cost')
    mejor = min(sum(coste[f, t] for f, t in enumerate(p))
                for p in itertools.permutations(range(len(slots)), len(df)))
    res = ghp.resolver_ghp(df, slots, 'cost')
    idx = {s: k for k, s in enumerate(slots)}
    assert sum(coste[i, idx[s]] for i, s in enumerate(res.loc[df.index, 'CTA'])) == pytest.approx(mejor)


def test_no_flight_lands_before_its_eta_and_slots_are_unique():
    df = _toy_flights(n=8, seed=5)
    slots = np.arange(360, 420, 3, dtype=float)
    for objetivo in ghp.OBJETIVOS:
        res = ghp.resolver_ghp(df, slots, objetivo)
        assert (res['CTA'] >= res['minutes_eta']).all()
        assert res['CTA'].is_unique


def test_exempt_flights_are_served_first_in_eta_order():
    df = _toy_flights(n=3, seed=1)
    df.loc[0, 'flight_status'] = FS_EXEMPT_INTERNATIONAL
    slots = np.array([360, 363, 366, 369, 372, 375, 378, 381], dtype=float)
    res = ghp.resolver_ghp(df, slots, 'cost').set_index('ARCID')
    eta0 = df.loc[0, 'minutes_eta']
    assert res.loc['F0', 'CTA'] == slots[slots >= eta0][0]
    assert res.loc['F0', 'air_delay'] == res.loc['F0', 'total_delay']
    assert res.loc['F0', 'ground_delay'] == 0


def test_infeasible_instance_raises():
    df = _toy_flights(n=3)
    with pytest.raises(RuntimeError):
        ghp.resolver_ghp(df, np.array([360.0, 363.0]), 'validation')


# -----------------------------------------------------------------------------
# Cost model
# -----------------------------------------------------------------------------

@pytest.mark.parametrize('fase', ['at_gate', 'en_route'])
def test_cost_of_delay_never_decreases_with_delay(fase):
    d = np.linspace(0, 400, 4001)
    for tipo in cost_model._PAX_BASE_TABLE:
        c = cost_model.coste_tabla_vec(tipo, d, fase)
        assert (np.diff(c) >= -1e-9).all(), tipo
        assert c[0] == 0


def test_cost_tables_match_cook_and_tanner_reference_values():
    # Spot checks against Cook & Tanner (2015), Tables 22, 26 and 28.
    assert cost_model._coste_tabla('A320', 5, 'at_gate') == 90       # primary, at gate
    assert cost_model._coste_tabla('A320', 60, 'at_gate') == 8350    # full, at gate
    assert cost_model._coste_tabla('B744', 300, 'en_route') == 343600
    # beyond 300 min the last segment is extended
    assert cost_model._coste_tabla('A320', 360, 'at_gate') == pytest.approx(105380 + (105380 - 78890))


def test_passenger_scaling_uses_cook_base_load():
    # A320 with 155 seats -> int(155 * 0.837) = 129 pax vs 130 in Cook's base scenario
    assert cost_model.factor_pasajeros('A320', 155) == pytest.approx(129 / 130)


# -----------------------------------------------------------------------------
# Full WP3 run on the synthetic day
# -----------------------------------------------------------------------------

@pytest.fixture(scope='module')
def wp3_run():
    df = prep.preparar_vuelos(config.SAMPLE_TRAFFIC, config.FLEET_TABLE)
    res_gdp = gdp.ejecutar_nucleo_gdp(df, PARAMS, run_compression=False)
    slots = list(res_gdp['slots']['slot_start_min'])
    return res_gdp['vuelos_asignados'], ghp.ejecutar_ghp_completo(res_gdp['vuelos_asignados'], slots, PARAMS)


def test_validation_task_reproduces_rbs_total_delay(wp3_run):
    rbs, res = wp3_run
    assert res['GHP_Validation']['total_delay'].sum() == pytest.approx(rbs['total_delay'].sum())


def test_every_scenario_keeps_total_delay_and_air_delay_feasible(wp3_run):
    rbs, res = wp3_run
    for clave in ghp.ESCENARIOS.values():
        assert res[clave]['total_delay'].sum() == pytest.approx(rbs['total_delay'].sum())
        assert len(res[clave]) == len(rbs)
    assert res['max_air_delay_ok']


def test_cost_objective_never_costs_more_than_rbs(wp3_run):
    rbs, res = wp3_run
    h = PARAMS['H_START']
    coste_rbs = evaluar_escenario(rbs, 'GDP', h)['Coste_Cook_Total_EUR']
    coste_ghp = evaluar_escenario(res['GHP_Cost'], 'GHP', h)['Coste_Cook_Total_EUR']
    assert coste_ghp <= coste_rbs + 1e-6


def test_emissions_objective_never_emits_more_than_rbs(wp3_run):
    rbs, res = wp3_run
    h = PARAMS['H_START']
    co2_rbs = evaluar_escenario(rbs, 'GDP', h)['CO2_Total_Retraso_kg']
    co2_ghp = evaluar_escenario(res['GHP_Emissions'], 'GHP', h)['CO2_Total_Retraso_kg']
    assert co2_ghp <= co2_rbs + 1e-6


def test_ground_delay_cap_is_respected(wp3_run):
    _, res = wp3_run
    for clave in ghp.ESCENARIOS.values():
        assert res[clave]['ground_delay'].max() <= ghp.MAX_GROUND_DELAY_MIN + 1e-9


def test_cap_below_the_rbs_maximum_is_infeasible():
    # Serving flights in ETA order minimises the maximum delay on identical
    # slots, so no assignment can keep every delay below the RBS maximum.
    df = prep.preparar_vuelos(config.SAMPLE_TRAFFIC, config.FLEET_TABLE)
    res_gdp = gdp.ejecutar_nucleo_gdp(df, PARAMS, run_compression=False)
    rbs = res_gdp['vuelos_asignados']
    slots = list(res_gdp['slots']['slot_start_min'])
    with pytest.raises(RuntimeError, match='MAX_GROUND_DELAY_MIN'):
        ghp.resolver_ghp(rbs, slots, 'cost', max_ground_delay=rbs['ground_delay'].max() - 1)
