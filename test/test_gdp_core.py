"""Tests for the WP2 GDP engine (src/lib_gdp_core.py) and compression (src/lib_compression.py)."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

import config  # noqa: E402
import lib_data_prep as prep  # noqa: E402
import lib_gdp_core as gdp  # noqa: E402
from config import (CFG, FS_CONTROLLED, FS_EXEMPT_FLYING, FS_EXEMPT_INTERNATIONAL,  # noqa: E402
                    FS_EXEMPT_RADIUS, FS_NOT_AFFECTED)
from cost_model import co2_tierra_kg_por_min  # noqa: E402
from lib_compression import penalty_and_compression  # noqa: E402

PARAMS = CFG.to_params_dict()


def _flights(rows):
    """rows: (id, eta, etd, is_ecac, distance_km[, status]) -> minimal flight table."""
    cols = ['ARCID', 'minutes_eta', 'minutes_etd', 'is_ecac', 'distancia_km', 'flight_status']
    rows = [r if len(r) == 6 else (*r, FS_CONTROLLED) for r in rows]
    df = pd.DataFrame(rows, columns=cols)
    df['airline'] = df['ARCID'].str[:3]
    return df


def _slots(times):
    return pd.DataFrame({'slot_start_min': list(times), 'occupied': False, 'flight_id': None})


# -----------------------------------------------------------------------------
# Newell cumulative curves
# -----------------------------------------------------------------------------

def test_newell_no_queue_when_demand_below_capacity():
    # one arrival every 10 min all day: below 20/h even during the LVP window
    df = _flights([(f'F{i}', float(t), 0.0, True, 500.0) for i, t in enumerate(range(0, 1440, 10))])
    timeline, h_noreg = gdp.simular_curvas_newell(df, PARAMS)
    assert timeline['queue_size'].max() < 1
    assert h_noreg == PARAMS['H_END'] + 1


def test_newell_capacity_never_exceeds_demand_and_is_not_banked():
    # no arrivals in the first half of the GDP, then a burst of 60 at 10:00
    df = _flights([(f'B{i}', 600.0, 0.0, True, 500.0) for i in range(60)])
    timeline, _ = gdp.simular_curvas_newell(df, PARAMS)
    assert (timeline['capacity_accum'] <= timeline['demand_accum'] + 1e-9).all()
    # From 10:00 the queue drains at 20/h only: 60 flights need about 3 h.
    # If idle capacity from 06:00-10:00 were banked, they would all land at once.
    at_1100 = timeline.loc[timeline['minuto'] == 660].iloc[0]
    assert at_1100['queue_size'] == pytest.approx(60 - 61 / 3, abs=1)


def test_newell_counts_etas_with_seconds():
    df = _flights([('S1', 400.75, 0.0, True, 500.0), ('S2', 401.0, 0.0, True, 500.0)])
    timeline, _ = gdp.simular_curvas_newell(df, PARAMS)
    assert timeline['demand_accum'].iloc[-1] == 2


def test_newell_minimum_delay_is_area_between_curves():
    timeline = pd.DataFrame({'demand_accum': [0, 2, 4, 4], 'capacity_accum': [0, 1, 2, 4]})
    assert gdp.calcular_retraso_minimo_newell(timeline) == 3


# -----------------------------------------------------------------------------
# Exemption rules
# -----------------------------------------------------------------------------

def test_exemption_categories_and_priority():
    t_file = PARAMS['H_START'] - CFG.H_FILE_OFFSET   # 03:30
    df = _flights([
        ('CAN', 500.0, t_file + 30, True, 800.0),     # ECAC, not departed, inside radius
        ('AIR', 500.0, t_file - 30, True, 800.0),     # departed before the GDP was filed
        ('FAR', 500.0, t_file + 30, True, 3500.0),    # outside the 3000 km radius
        ('INT', 500.0, t_file + 30, False, 6000.0),   # non-ECAC
        ('INA', 500.0, t_file - 300, False, 9000.0),  # non-ECAC and airborne -> INTERNATIONAL first
    ])
    status = gdp.etiquetar_vuelos_gdp(df, PARAMS['H_START']).set_index('ARCID')['flight_status']
    assert status.to_dict() == {
        'CAN': FS_CONTROLLED, 'AIR': FS_EXEMPT_FLYING, 'FAR': FS_EXEMPT_RADIUS,
        'INT': FS_EXEMPT_INTERNATIONAL, 'INA': FS_EXEMPT_INTERNATIONAL,
    }


def test_exemption_parameters_are_honoured():
    df = _flights([('X', 500.0, 300.0, True, 1200.0)])
    assert gdp.etiquetar_vuelos_gdp(df, 360, radius_km=1000)['flight_status'].iat[0] == FS_EXEMPT_RADIUS
    assert gdp.etiquetar_vuelos_gdp(df, 360, h_file_offset=30)['flight_status'].iat[0] == FS_EXEMPT_FLYING
    assert gdp.etiquetar_vuelos_gdp(df, 360, h_file_offset=150)['flight_status'].iat[0] == FS_CONTROLLED


def test_labelling_does_not_modify_input():
    df = _flights([('X', 500.0, 300.0, True, 1200.0)]).drop(columns='flight_status')
    before = df.copy()
    gdp.etiquetar_vuelos_gdp(df, 360)
    pd.testing.assert_frame_equal(df, before)


def test_flights_outside_hstart_hnoreg_are_not_affected():
    df = _flights([
        ('EARLY', 359.0, 300.0, True, 500.0),
        ('FIRST', 360.0, 300.0, True, 500.0),
        ('LAST',  999.0, 900.0, True, 500.0),
        ('AFTER', 1000.0, 900.0, False, 6000.0),   # non-ECAC, but outside the regulation
    ])
    status = gdp.etiquetar_vuelos_gdp(df, 360, h_noreg=1000).set_index('ARCID')['flight_status']
    assert status.to_dict() == {'EARLY': FS_NOT_AFFECTED, 'FIRST': FS_CONTROLLED,
                                'LAST': FS_CONTROLLED, 'AFTER': FS_NOT_AFFECTED}


def test_ready_to_depart_margin_extends_exempt_flying():
    h_file = 360 - CFG.H_FILE_OFFSET
    df = _flights([('READY', 500.0, h_file + 20, True, 800.0)])
    assert gdp.etiquetar_vuelos_gdp(df, 360)['flight_status'].iat[0] == FS_CONTROLLED
    assert gdp.etiquetar_vuelos_gdp(df, 360, ready_margin=30)['flight_status'].iat[0] == FS_EXEMPT_FLYING


# -----------------------------------------------------------------------------
# RBS slot allocation and delay split
# -----------------------------------------------------------------------------

def test_rbs_gives_exempt_flights_priority():
    df = _flights([
        ('CAN', 360.0, 0, True, 500, FS_CONTROLLED),
        ('EXE', 361.0, 0, False, 5000, FS_EXEMPT_INTERNATIONAL),
    ])
    res = gdp.asignar_slots_rbs(df, _slots([362, 365]))
    slots = res.set_index('ARCID')['assigned_slot']
    assert slots['EXE'] == 362 and slots['CAN'] == 365


def test_rbs_is_first_scheduled_first_served_and_never_early():
    df = _flights([(f'C{i}', 360.0 + i, 0, True, 500) for i in range(5)])
    res = gdp.asignar_slots_rbs(df, _slots([360, 363, 366, 369, 372]))
    assert res['assigned_slot'].tolist() == [360, 363, 366, 369, 372]
    assert (res['assigned_slot'] >= res['minutes_eta']).all()


def test_rbs_leaves_flight_unassigned_when_no_slot_left():
    df = _flights([('A', 360.0, 0, True, 500), ('B', 361.0, 0, True, 500)])
    res = gdp.asignar_slots_rbs(df, _slots([362]))
    assert res['assigned_slot'].isna().sum() == 1


def test_delay_split_air_for_exempt_ground_for_candidates():
    df = _flights([('CAN', 360.0, 0, True, 500, FS_CONTROLLED), ('EXE', 360.0, 0, False, 5000, FS_EXEMPT_INTERNATIONAL)])
    df['assigned_slot'] = [370.0, 365.0]
    res = gdp.calcular_delays(df).set_index('ARCID')
    assert res.loc['CAN', ['total_delay', 'ground_delay', 'air_delay']].tolist() == [10, 10, 0]
    assert res.loc['EXE', ['total_delay', 'ground_delay', 'air_delay']].tolist() == [5, 0, 5]


# -----------------------------------------------------------------------------
# Unrecoverable delay and KPIs
# -----------------------------------------------------------------------------

def test_unrecoverable_delay_cases():
    h = 360
    df = pd.DataFrame({
        'minutes_etd':  [400.0, 300.0, 330.0, 300.0],
        'ground_delay': [ 50.0,  40.0,  60.0,   0.0],
    }, index=['case1', 'case2', 'case3', 'no_delay'])
    lost = gdp.calcular_retraso_irrecuperable(df, h)
    assert lost.to_dict() == {'case1': 0.0, 'case2': 40.0, 'case3': 30.0, 'no_delay': 0.0}


def test_kpis_use_fegp_for_ground_co2():
    df = pd.DataFrame({
        'total_delay': [30.0, 10.0], 'air_delay': [0.0, 10.0], 'ground_delay': [30.0, 0.0],
        'co2_kg_vuelo': [6000.0, 9000.0], 'duracion_vuelo_min': [60.0, 90.0],
        'recat': ['D', 'B'], 'minutes_etd': [500.0, 500.0],
    })
    k = gdp.calcular_kpis_economicos(df)
    assert k['co2_tierra_delay'] == pytest.approx(30 * co2_tierra_kg_por_min('D'), abs=0.01)
    assert k['co2_aire_delay'] == pytest.approx(10 * 9000 / 90, abs=0.01)
    # do-nothing puts all 40 min in the air at each flight's cruise rate
    assert k['co2_savings'] == pytest.approx(30 * 6000 / 60 - 30 * co2_tierra_kg_por_min('D'), abs=0.01)
    assert k['cost_savings'] == pytest.approx(30 * (config.COST_AIR_MIN - config.COST_GND_MIN))


# -----------------------------------------------------------------------------
# Penalty and compression
# -----------------------------------------------------------------------------

def _compression_case():
    # Four candidates queued behind each other; VLG2 (the most delayed of VLG) is cancelled.
    df = _flights([
        ('VLG1', 360.0, 0, True, 500), ('RYR1', 361.0, 0, True, 500),
        ('VLG2', 362.0, 0, True, 500), ('VLG3', 363.0, 0, True, 500),
        ('RYR2', 364.0, 0, True, 500),
    ])
    df['assigned_slot'] = [360.0, 380.0, 400.0, 420.0, 440.0]
    return gdp.calcular_delays(df)


def test_compression_owner_airline_fills_its_slot_first(capsys):
    df = _compression_case()
    out = penalty_and_compression(df, num_penalizados=1).set_index('ARCID')
    # RYR2 (largest delay) is cancelled: its 440 slot is freed and nobody can use it.
    assert np.isnan(out.loc['RYR2', 'assigned_slot'])
    pd.testing.assert_series_equal(out['assigned_slot'].dropna(), df.set_index('ARCID')['assigned_slot'].drop('RYR2'))

    df2 = _compression_case()
    df2.loc[df2['ARCID'] == 'RYR2', 'total_delay'] = 0     # make VLG3 the one penalised
    out2 = penalty_and_compression(df2, num_penalizados=1).set_index('ARCID')
    assert np.isnan(out2.loc['VLG3', 'assigned_slot'])
    # VLG3's slot (420) belongs to VLG, but no later VLG flight exists, so RYR2 moves up.
    assert out2.loc['RYR2', 'assigned_slot'] == 420


def test_compression_only_moves_flights_earlier_and_keeps_slots_unique(capsys):
    df = _compression_case()
    df.loc[df['ARCID'] == 'RYR1', 'total_delay'] = 999      # cancel RYR1, freeing 380
    out = penalty_and_compression(df, num_penalizados=1).set_index('ARCID')
    before = df.set_index('ARCID')['assigned_slot']
    moved = out['assigned_slot'].dropna()
    assert (moved <= before[moved.index]).all()
    assert moved.is_unique
    assert (moved >= out.loc[moved.index, 'minutes_eta']).all()
    # The freed 380 slot belongs to Ryanair, so RYR2 takes it ahead of the
    # earlier VLG flights; its old 440 slot then has no later taker.
    assert out.loc['RYR2', 'assigned_slot'] == 380
    assert out.loc['VLG2', 'assigned_slot'] == 400


# -----------------------------------------------------------------------------
# Full GDP run on the synthetic day
# -----------------------------------------------------------------------------

@pytest.fixture(scope='module')
def gdp_run():
    df = prep.preparar_vuelos(config.SAMPLE_TRAFFIC, config.FLEET_TABLE)
    return gdp.ejecutar_nucleo_gdp(df, PARAMS, run_compression=False)


def test_full_run_allocation_is_feasible(gdp_run):
    res = gdp_run['vuelos_asignados']
    assert res['assigned_slot'].notna().all()
    assert res['assigned_slot'].is_unique
    assert (res['assigned_slot'] >= res['minutes_eta'] - 1e-9).all()
    np.testing.assert_allclose(res['air_delay'] + res['ground_delay'], res['total_delay'])


def test_full_run_rbs_delay_close_to_newell_bound(gdp_run):
    rbs = gdp_run['vuelos_asignados']['total_delay'].sum()
    newell = gdp.calcular_retraso_minimo_newell(gdp_run['timeline'])
    # Same capacity profile, discrete slots vs. a fluid queue: within a few percent.
    assert rbs == pytest.approx(newell, rel=0.05)


def test_full_run_queue_clears_after_the_regulation(gdp_run):
    assert PARAMS['H_END'] < gdp_run['h_noreg'] < 1440


def test_full_run_only_regulates_flights_between_hstart_and_hnoreg(gdp_run):
    res = gdp_run['vuelos_asignados']
    assert res['minutes_eta'].between(PARAMS['H_START'], gdp_run['h_noreg'], inclusive='left').all()
    assert FS_NOT_AFFECTED not in set(res['flight_status'])
