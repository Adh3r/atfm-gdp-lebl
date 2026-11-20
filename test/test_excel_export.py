"""Tests for the Excel workbook (src/lib_excel_export.py) and the WP5 comparison (src/lib_comparison.py)."""

import os
import sys

import openpyxl
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

import config  # noqa: E402
import lib_data_prep as prep  # noqa: E402
import lib_excel_export as excel  # noqa: E402
import lib_gdp_core as gdp  # noqa: E402
from config import CFG  # noqa: E402
from lib_comparison import KPA_POR_KPI, tabla_final  # noqa: E402
from lib_ghp_solver import ejecutar_ghp_completo  # noqa: E402
from lib_intermodal import ejecutar_analisis_intermodal  # noqa: E402
from lib_metrics import FILAS_RESUMEN, evaluar_escenario, resumen_escenarios  # noqa: E402

PARAMS = CFG.to_params_dict()


@pytest.fixture(scope='module')
def pipeline(tmp_path_factory):
    out = tmp_path_factory.mktemp('run')
    df = prep.preparar_vuelos(config.SAMPLE_TRAFFIC, config.FLEET_TABLE)
    res = gdp.ejecutar_nucleo_gdp(df, PARAMS)                         # with compression
    rbs = res['vuelos_asignados']
    ghp = ejecutar_ghp_completo(rbs, list(res['slots']['slot_start_min']), PARAMS)
    wp4 = ejecutar_analisis_intermodal(df, res, ghp, PARAMS, str(out))
    escenarios = {'GDP (RBS)': rbs, 'GHP emissions': ghp['GHP_Emissions'], 'GHP cost': ghp['GHP_Cost']}
    kpis = {n: evaluar_escenario(d, n, PARAMS['H_START']) for n, d in escenarios.items()}
    wp3 = resumen_escenarios(kpis)
    wp5 = tabla_final(wp4['df_comparativa'])
    path = os.path.join(str(out), 'audit.xlsx')
    excel.exportar_auditoria_excel(
        df_vuelos_crudo=df, escenarios_dict=escenarios, df_dashboard=pd.DataFrame(kpis.values()),
        df_slots=res['slots'], params=PARAMS, h_noreg=res['h_noreg'], timeline=res['timeline'],
        r_min_newell=gdp.calcular_retraso_minimo_newell(res['timeline']), path=path,
        df_res_comprimido=res['vuelos_comprimidos'], df_intermodal=wp4['df_comparativa'],
        tablas_resumen={'WP3_GHP_vs_GDP': wp3, 'WP4_Intermodal': wp4['df_comparativa'], 'WP5_Final_by_KPA': wp5},
    )
    return {'path': path, 'res': res, 'wp5': wp5, 'escenarios': escenarios}


def test_workbook_has_every_sheet(pipeline):
    hojas = openpyxl.load_workbook(pipeline['path'], read_only=True).sheetnames
    for esperado in ['00_Dashboard_KPIs', 'WP3_GHP_vs_GDP', 'WP4_Intermodal', 'WP5_Final_by_KPA',
                     'Audit_GDP (RBS)', 'Audit_GHP emissions', 'Audit_GHP cost',
                     'WP2_Compression', 'Config_Escenario', 'Config_Slots']:
        assert esperado in hojas


def test_audit_sheet_has_one_row_per_affected_flight(pipeline):
    ws = openpyxl.load_workbook(pipeline['path'], read_only=True)['Audit_GDP (RBS)']
    filas = list(ws.iter_rows(values_only=True))
    assert len(filas) - 1 == len(pipeline['escenarios']['GDP (RBS)'])
    assert {'CTA', 'CTD', 'flight_status'} <= set(filas[0])


def test_compression_sheet_matches_the_compression_run(pipeline):
    res = pipeline['res']
    tabla = excel.tabla_compresion(res['vuelos_asignados'], res['vuelos_comprimidos'])
    assert (tabla['status'] == 'Cancelled (penalty)').sum() == 12
    movidos = tabla[tabla['status'] == 'Moved earlier (compression)']
    assert len(movidos) > 0
    assert (movidos['minutes_saved'] > 0).all()                     # the old sheet always reported 0
    assert (movidos['CTA_after'] >= movidos['ETA']).all()


def test_every_wp5_kpi_is_mapped_to_a_kpa(pipeline):
    assert 'Other' not in set(pipeline['wp5']['KPA'])
    assert {etiqueta for etiqueta, _ in FILAS_RESUMEN} <= set(KPA_POR_KPI)
    assert len(pipeline['wp5'].columns) == 2 + 6                      # KPA, Metric + six methods
