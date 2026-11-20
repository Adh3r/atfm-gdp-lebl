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
from lib_ghp_solver import ejecutar_ghp_completo  # noqa: E402

PARAMS = CFG.to_params_dict()


def test_door_to_door_times():
    # Madrid by HSR: 40 + 3.1 h + 20 = 246 min (the value quoted in the report).
    assert inter.calcular_tiempo_d2d(483, 'LEMD', es_avion=False) == pytest.approx(246)
    # Air: 90 + in-flight time at 440 kt + 60.
    esperado = 90 + 483 / (440 * 1.852) * 60 + 60
    assert inter.calcular_tiempo_d2d(483, 'LEMD', es_avion=True) == pytest.approx(esperado)


def test_rail_emissions_use_route_factor_or_generic_fallback():
    assert inter.calcular_emision_tren(483, 'LEMD', 100) == pytest.approx(1900)
    assert inter.calcular_emision_tren(300, 'LEXX', 100) == pytest.approx(300 * 100 * 15 / 1000)


@pytest.fixture(scope='module')
def wp4(tmp_path_factory):
    df = prep.preparar_vuelos(config.SAMPLE_TRAFFIC, config.FLEET_TABLE)
    base = gdp.ejecutar_nucleo_gdp(df, PARAMS, run_compression=False)
    base_ghp = ejecutar_ghp_completo(base['vuelos_asignados'], list(base['slots']['slot_start_min']), PARAMS)
    out = inter.ejecutar_analisis_intermodal(df, base, base_ghp, PARAMS, str(tmp_path_factory.mktemp('wp4')))
    return df, base, out


def test_only_flights_inside_the_regulation_are_replaced(wp4):
    _, base, out = wp4
    afectados = base['vuelos_asignados']
    reemplazados = out['df_modal']
    assert len(reemplazados) > 0
    assert set(reemplazados['ARCID']) <= set(afectados['ARCID'])
    assert set(reemplazados['ADEP']) <= set(inter.DESTINOS_HSR_DESDE_LEBL)
    assert (reemplazados['distancia_km'] <= inter.DISTANCIA_MAX_INTERMODAL_KM).all()


def test_reduced_demand_relieves_the_regulation(wp4):
    _, base, out = wp4
    red = out['resultados_reducido']['resultados_gdp']
    assert red['h_noreg'] <= base['h_noreg']
    assert red['vuelos_asignados']['total_delay'].sum() < base['vuelos_asignados']['total_delay'].sum()


def test_system_co2_accounting_is_consistent(wp4):
    df, _, out = wp4
    tabla = out['df_comparativa'].set_index('Metric')
    fila = tabla.loc['System CO2: flights + delay + rail (kg)']
    k = out['kpis']
    base = df['co2_kg_vuelo'].sum() + k['GDP (RBS)']['CO2_Total_Retraso_kg']
    inter_ = (df['co2_kg_vuelo'].sum() - out['co2']['vuelos_sustituidos']
              + k['Intermodal GDP (RBS)']['CO2_Total_Retraso_kg'] + out['co2']['tren'])
    assert fila['GDP (RBS)'] == pytest.approx(base, abs=0.1)
    assert fila['Intermodal GDP (RBS)'] == pytest.approx(inter_, abs=0.1)
