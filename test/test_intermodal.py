"""Tests for the WP4 intermodality study (src/lib_intermodal.py)."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

import config  # noqa: E402
import lib_data_prep as prep  # noqa: E402
import lib_gdp_core as gdp  # noqa: E402
import lib_intermodal as inter  # noqa: E402
from config import CFG  # noqa: E402
from lib_ghp_solver import run_ghp  # noqa: E402

PARAMS = CFG.to_params_dict()


def test_door_to_door_times():
    # Madrid by HSR: 40 + 3.1 h + 20 = 246 min (the value quoted in the report).
    assert inter.door_to_door_min(483, 'LEMD', by_air=False) == pytest.approx(246)
    # Air: 90 + in-flight time at 440 kt + 60.
    expected = 90 + 483 / (440 * 1.852) * 60 + 60
    assert inter.door_to_door_min(483, 'LEMD', by_air=True) == pytest.approx(expected)


def test_rail_emissions_use_route_factor_or_generic_fallback():
    assert inter.rail_co2_kg(483, 'LEMD', 100) == pytest.approx(1900)
    assert inter.rail_co2_kg(300, 'LEXX', 100) == pytest.approx(300 * 100 * 15 / 1000)


@pytest.fixture(scope='module')
def wp4(tmp_path_factory):
    df = prep.prepare_flights(config.SAMPLE_TRAFFIC, config.FLEET_TABLE)
    base = gdp.run_gdp(df, PARAMS, run_compression=False)
    base_ghp = run_ghp(base['allocation'], list(base['slots']['slot_start_min']), PARAMS)
    out = inter.run_intermodal_analysis(df, base, base_ghp, PARAMS, str(tmp_path_factory.mktemp('wp4')))
    return df, base, out


def test_only_flights_inside_the_regulation_are_replaced(wp4):
    _, base, out = wp4
    affected = base['allocation']
    replaced = out['df_modal']
    assert len(replaced) > 0
    assert set(replaced['ARCID']) <= set(affected['ARCID'])
    assert set(replaced['ADEP']) <= set(inter.HSR_ROUTES)
    assert (replaced['distance_km'] <= inter.MAX_RAIL_DISTANCE_KM).all()


def test_reduced_demand_relieves_the_regulation(wp4):
    _, base, out = wp4
    reduced = out['reduced']['gdp_results']
    assert reduced['h_noreg'] <= base['h_noreg']
    assert reduced['allocation']['total_delay'].sum() < base['allocation']['total_delay'].sum()


def test_system_co2_accounting_is_consistent(wp4):
    df, _, out = wp4
    table = out['summary'].set_index('Metric')
    row = table.loc['System CO2: flights + delay + rail (kg)']
    k = out['kpis']
    base = df['flight_co2_kg'].sum() + k['GDP (RBS)']['delay_co2_kg']
    inter_ = (df['flight_co2_kg'].sum() - out['co2']['replaced_flights']
              + k['Intermodal GDP (RBS)']['delay_co2_kg'] + out['co2']['rail'])
    assert row['GDP (RBS)'] == pytest.approx(base, abs=0.1)
    assert row['Intermodal GDP (RBS)'] == pytest.approx(inter_, abs=0.1)
