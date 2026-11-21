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
from lib_metrics import evaluate_scenario  # noqa: E402

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
    a_flights = np.zeros((n_f, n_f * n_t))  # each flight exactly one slot
    for f in range(n_f):
        a_flights[f, f * n_t : (f + 1) * n_t] = 1
    a_slots = np.zeros((n_t, n_f * n_t))  # each slot at most one flight
    for t in range(n_t):
        a_slots[t, t::n_t] = 1
    res = milp(
        c=c,
        constraints=[LinearConstraint(a_flights, 1, 1), LinearConstraint(a_slots, 0, 1)],
        integrality=np.ones_like(c),
        bounds=Bounds(0, 1),
    )
    assert res.success
    return float(res.fun)


def _toy_flights(n=6, seed=0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(
        {
            'ARCID': [f'F{i}' for i in range(n)],
            'ATYP': rng.choice(['A320', 'B738', 'E190', 'A332'], n),
            'recat': rng.choice(['D', 'E', 'B'], n),
            'size_seats_avg': rng.choice([100, 180, 280], n),
            'minutes_eta': np.sort(rng.integers(360, 380, n)).astype(float),
            'minutes_etd': 300.0,
            'flight_status': FS_CONTROLLED,
            'airline': 'TST',
        }
    )
    return df


@pytest.mark.parametrize('objective', ghp.OBJECTIVES)
def test_assignment_solver_matches_milp_reference(objective):
    df = _toy_flights()
    slots = np.arange(360, 360 + 3 * 10, 3, dtype=float)  # 10 slots, 3 min apart
    cost = ghp.build_cost_matrix(df, slots, objective)
    res = ghp.solve_ghp(df, slots, objective)
    idx = {s: k for k, s in enumerate(slots)}
    obtenido = sum(cost[i, idx[s]] for i, s in enumerate(res.loc[df.index, 'CTA']))
    assert obtenido == pytest.approx(ghp_milp_reference(cost), rel=1e-9)


def test_assignment_solver_matches_brute_force():
    df = _toy_flights(n=4, seed=3)
    slots = np.array([360, 363, 366, 369, 372, 375], dtype=float)
    cost = ghp.build_cost_matrix(df, slots, 'cost')
    best = min(
        sum(cost[f, t] for f, t in enumerate(p)) for p in itertools.permutations(range(len(slots)), len(df))
    )
    res = ghp.solve_ghp(df, slots, 'cost')
    idx = {s: k for k, s in enumerate(slots)}
    assert sum(cost[i, idx[s]] for i, s in enumerate(res.loc[df.index, 'CTA'])) == pytest.approx(best)


def test_no_flight_lands_before_its_eta_and_slots_are_unique():
    df = _toy_flights(n=8, seed=5)
    slots = np.arange(360, 420, 3, dtype=float)
    for objective in ghp.OBJECTIVES:
        res = ghp.solve_ghp(df, slots, objective)
        assert (res['CTA'] >= res['minutes_eta']).all()
        assert res['CTA'].is_unique


def test_exempt_flights_are_served_first_in_eta_order():
    df = _toy_flights(n=3, seed=1)
    df.loc[0, 'flight_status'] = FS_EXEMPT_INTERNATIONAL
    slots = np.array([360, 363, 366, 369, 372, 375, 378, 381], dtype=float)
    res = ghp.solve_ghp(df, slots, 'cost').set_index('ARCID')
    eta0 = df.loc[0, 'minutes_eta']
    assert res.loc['F0', 'CTA'] == slots[slots >= eta0][0]
    assert res.loc['F0', 'air_delay'] == res.loc['F0', 'total_delay']
    assert res.loc['F0', 'ground_delay'] == 0


def test_infeasible_instance_raises():
    df = _toy_flights(n=3)
    with pytest.raises(RuntimeError):
        ghp.solve_ghp(df, np.array([360.0, 363.0]), 'validation')


# -----------------------------------------------------------------------------
# Cost model
# -----------------------------------------------------------------------------


@pytest.mark.parametrize('base', cost_model.COST_BASES)
@pytest.mark.parametrize('buffer', [np.nan, 0.0, 40.0])
def test_cost_of_delay_never_decreases_with_delay(base, buffer):
    d = np.linspace(0, 400, 4001)
    for cook_type in cost_model._PAX_BASE_TABLE:
        for airborne in (False, True):
            g, a = (np.zeros_like(d), d) if airborne else (d, 0.0)
            c = cost_model.flight_cost(cook_type, 1.0, g, a, buffer, base)
            assert (np.diff(c) >= -1e-9).all(), (cook_type, base)
            assert c[0] == 0


def test_cost_tables_match_cook_and_tanner_reference_values():
    # Spot checks against Cook & Tanner (2015), Tables 22, 24, 26 and 28.
    prim = cost_model.primary_cost
    assert prim('A320', [5], 'at_gate')[0] == 90  # Table 22
    assert prim('A320', [60], 'en_route')[0] == 6650  # Table 24
    full = cost_model.flight_cost
    k = cost_model.PRICE_FACTOR_2014_TO_2022  # EUR 2014 -> 2022
    assert full('A320', 1.0, [60.0], base='full')[0] == pytest.approx(8350 * k)  # Table 26
    assert full('B744', 1.0, 0.0 * np.ones(1), [300.0], base='full')[0] == pytest.approx(
        343600 * k
    )  # Table 28
    # beyond 300 min the last segment is extended
    assert prim('A320', [360], 'at_gate')[0] == pytest.approx(67820 + (67820 - 46430))


def test_reactionary_cost_is_the_same_at_gate_and_en_route():
    # Cook & Tanner cost all reactionary delay at the gate, so full - primary
    # must coincide in the at-gate and en-route tables (up to rounding).
    for cook_type in cost_model._PAX_BASE_TABLE:
        g = np.subtract(
            cost_model._COOK_TACTICAL[('at_gate', 'full')][cook_type],
            cost_model._COOK_TACTICAL[('at_gate', 'primary')][cook_type],
        )
        e = np.subtract(
            cost_model._COOK_TACTICAL[('en_route', 'full')][cook_type],
            cost_model._COOK_TACTICAL[('en_route', 'primary')][cook_type],
        )
        assert np.abs(g - e).max() <= 10, cook_type


def test_rotation_cost_sits_between_primary_and_full():
    d = np.linspace(0, 300, 301)
    for buffer in (0.0, 30.0, 90.0, np.nan):
        p = cost_model.flight_cost('A320', 1.0, d, base='primary')
        r = cost_model.flight_cost('A320', 1.0, d, 0.0, buffer, 'rotation')
        f = cost_model.flight_cost('A320', 1.0, d, base='full')
        assert (p <= r + 1e-9).all() and (r <= f + 1e-9).all()
    # no buffer: every minute propagates, as in Cook's network average
    assert np.allclose(
        cost_model.flight_cost('A320', 1.0, d, 0.0, 0.0, 'rotation'),
        cost_model.flight_cost('A320', 1.0, d, base='full'),
    )
    # night stop (no later rotation): primary only
    assert np.allclose(
        cost_model.flight_cost('A320', 1.0, d, 0.0, np.nan, 'rotation'),
        cost_model.flight_cost('A320', 1.0, d, base='primary'),
    )


def test_delay_absorbed_by_the_turnaround_does_not_propagate():
    assert cost_model.propagated_delay([20.0, 50.0, 80.0], 50.0).tolist() == [0.0, 0.0, 30.0]
    assert cost_model.propagated_delay([80.0], np.nan).tolist() == [0.0]


def test_turnaround_buffer_uses_minimum_turnaround_by_category():
    df = pd.DataFrame({'recat': ['D', 'B', 'D', 'D'], 'scheduled_turnaround_min': [60.0, 60.0, 20.0, np.nan]})
    h = cost_model.turnaround_buffer(df)
    assert h.iloc[0] == 60 - cost_model.MIN_TURNAROUND_MIN['narrow']
    assert h.iloc[1] == 0  # wide-body: 60 min < 80 min minimum
    assert h.iloc[2] == 0  # scheduled below the minimum: no buffer
    assert np.isnan(h.iloc[3])


def test_turnaround_pairs_arrival_with_next_departure_of_same_aircraft():
    arrivals = pd.DataFrame(
        {'RM': ['ECAAA', 'ECAAA', 'ECBBB', 'ECCCC'], 'minutes_eta': [400.0, 600.0, 450.0, 500.0]}
    )
    departures_df = pd.DataFrame({'RM': ['ECAAA', 'ECAAA', 'ECBBB'], 'ETD': ['07:20', '11:00', '06:00']})
    tat = prep.scheduled_turnaround(arrivals, departures_df)
    assert tat.iloc[0] == 40  # 06:40 arrival, 07:20 departure
    assert tat.iloc[1] == 60  # 10:00 arrival, 11:00 departure
    assert np.isnan(tat.iloc[2])  # ECBBB departed before it arrived: night stop
    assert np.isnan(tat.iloc[3])  # no departure at all


def test_turnaround_skips_pairs_with_a_missing_leg():
    # The aircraft arrives twice before the departure: the first arrival is
    # not a turnaround of that departure.
    arrivals = pd.DataFrame({'RM': ['ECAAA', 'ECAAA'], 'minutes_eta': [400.0, 500.0]})
    departures_df = pd.DataFrame({'RM': ['ECAAA'], 'ETD': ['09:00']})
    tat = prep.scheduled_turnaround(arrivals, departures_df)
    assert np.isnan(tat.iloc[0]) and tat.iloc[1] == 40


def test_cost_objective_holds_first_the_flights_with_loose_turnarounds():
    # Two identical A320s compete for slots at 0 and 60 min of delay. The one
    # with a 90-min buffer absorbs the hour; the tight one keeps the early slot.
    df = pd.DataFrame(
        {
            'ARCID': ['TIGHT', 'LOOSE'],
            'ATYP': 'A320',
            'recat': 'D',
            'size_seats_avg': 180,
            'minutes_eta': [400.0, 400.0],
            'minutes_etd': 300.0,
            'flight_status': FS_CONTROLLED,
            'airline': 'TST',
            'scheduled_turnaround_min': [35.0, 125.0],
        }
    )
    res = ghp.solve_ghp(df, np.array([400.0, 460.0]), 'cost').set_index('ARCID')
    assert res.loc['TIGHT', 'total_delay'] == 0
    assert res.loc['LOOSE', 'total_delay'] == 60


def test_passenger_scaling_uses_cook_base_load():
    # A320 with 155 seats -> int(155 * 0.879) = 136 pax vs 130 in Cook's base scenario
    assert cost_model.passenger_factor('A320', 155) == pytest.approx(136 / 130)


def test_only_the_passenger_part_of_the_primary_cost_scales_with_passengers():
    # Doubling the passengers adds exactly one more passenger component
    # (Cook & Tanner passenger-cost Table 12); crew, fuel and maintenance stay.
    k = cost_model.PRICE_FACTOR_2014_TO_2022
    one = cost_model.flight_cost('A320', 1.0, [60.0], base='primary')[0]
    two = cost_model.flight_cost('A320', 2.0, [60.0], base='primary')[0]
    assert one == pytest.approx(4590 * k)  # Table 22
    assert two - one == pytest.approx(4030 * k)  # Table 12, A320 at 60 min
    assert two < 2 * one


# -----------------------------------------------------------------------------
# Full WP3 run on the synthetic day
# -----------------------------------------------------------------------------


@pytest.fixture(scope='module')
def wp3_run():
    df = prep.prepare_flights(config.SAMPLE_TRAFFIC, config.FLEET_TABLE)
    res_gdp = gdp.run_gdp(df, PARAMS, run_compression=False)
    slots = list(res_gdp['slots']['slot_start_min'])
    return res_gdp['allocation'], ghp.run_ghp(res_gdp['allocation'], slots, PARAMS)


def test_validation_task_reproduces_rbs_total_delay(wp3_run):
    rbs, res = wp3_run
    assert res['GHP_Validation']['total_delay'].sum() == pytest.approx(rbs['total_delay'].sum())


def test_every_scenario_keeps_total_delay_and_air_delay_feasible(wp3_run):
    rbs, res = wp3_run
    for key in ghp.SCENARIOS.values():
        assert res[key]['total_delay'].sum() == pytest.approx(rbs['total_delay'].sum())
        assert len(res[key]) == len(rbs)
    assert res['max_air_delay_ok']


def test_cost_objective_never_costs_more_than_rbs(wp3_run):
    rbs, res = wp3_run
    h = PARAMS['H_START']
    cost_rbs = evaluate_scenario(rbs, 'GDP', h)['delay_cost_total_eur']
    cost_ghp = evaluate_scenario(res['GHP_Cost'], 'GHP', h)['delay_cost_total_eur']
    assert cost_ghp <= cost_rbs + 1e-6


def test_emissions_objective_never_emits_more_than_rbs(wp3_run):
    rbs, res = wp3_run
    h = PARAMS['H_START']
    co2_rbs = evaluate_scenario(rbs, 'GDP', h)['delay_co2_kg']
    co2_ghp = evaluate_scenario(res['GHP_Emissions'], 'GHP', h)['delay_co2_kg']
    assert co2_ghp <= co2_rbs + 1e-6


def test_ground_delay_cap_is_respected(wp3_run):
    _, res = wp3_run
    for key in ghp.SCENARIOS.values():
        assert res[key]['ground_delay'].max() <= ghp.MAX_GROUND_DELAY_MIN + 1e-9


def test_cap_below_the_rbs_maximum_is_infeasible():
    # Serving flights in ETA order minimises the maximum delay on identical
    # slots, so no assignment can keep every delay below the RBS maximum.
    df = prep.prepare_flights(config.SAMPLE_TRAFFIC, config.FLEET_TABLE)
    res_gdp = gdp.run_gdp(df, PARAMS, run_compression=False)
    rbs = res_gdp['allocation']
    slots = list(res_gdp['slots']['slot_start_min'])
    with pytest.raises(RuntimeError, match='MAX_GROUND_DELAY_MIN'):
        ghp.solve_ghp(rbs, slots, 'cost', max_ground_delay=rbs['ground_delay'].max() - 1)
