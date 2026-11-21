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
from lib_comparison import KPI_TO_KPA, final_table  # noqa: E402
from lib_ghp_solver import run_ghp  # noqa: E402
from lib_intermodal import run_intermodal_analysis  # noqa: E402
from lib_metrics import SUMMARY_ROWS, evaluate_scenario, summary_table  # noqa: E402

PARAMS = CFG.to_params_dict()


@pytest.fixture(scope='module')
def pipeline(tmp_path_factory):
    out = tmp_path_factory.mktemp('run')
    df = prep.prepare_flights(config.SAMPLE_TRAFFIC, config.FLEET_TABLE)
    res = gdp.run_gdp(df, PARAMS)                         # with compression
    rbs = res['allocation']
    ghp = run_ghp(rbs, list(res['slots']['slot_start_min']), PARAMS)
    wp4 = run_intermodal_analysis(df, res, ghp, PARAMS, str(out))
    scenarios = {'GDP (RBS)': rbs, 'GHP emissions': ghp['GHP_Emissions'], 'GHP cost': ghp['GHP_Cost']}
    kpis = {n: evaluate_scenario(d, n, PARAMS['H_START']) for n, d in scenarios.items()}
    wp3 = summary_table(kpis)
    wp5 = final_table(wp4['summary'])
    path = os.path.join(str(out), 'audit.xlsx')
    excel.export_audit_workbook(
        df_flights_raw=df, scenario_tables=scenarios, df_dashboard=pd.DataFrame(kpis.values()),
        df_slots=res['slots'], params=PARAMS, h_noreg=res['h_noreg'], timeline=res['timeline'],
        newell_min_delay=gdp.newell_minimum_delay(res['timeline']), path=path,
        df_compressed=res['compressed_allocation'], df_intermodal=wp4['summary'],
        summary_tables={'WP3_GHP_vs_GDP': wp3, 'WP4_Intermodal': wp4['summary'], 'WP5_Final_by_KPA': wp5},
    )
    return {'path': path, 'res': res, 'wp5': wp5, 'scenarios': scenarios}


def test_workbook_has_every_sheet(pipeline):
    sheets = openpyxl.load_workbook(pipeline['path'], read_only=True).sheetnames
    for expected in ['Dashboard', 'WP3_GHP_vs_GDP', 'WP4_Intermodal', 'WP5_Final_by_KPA',
                     'Audit_GDP (RBS)', 'Audit_GHP emissions', 'Audit_GHP cost',
                     'WP2_Cancellations', 'Parameters', 'Slots']:
        assert expected in sheets


def test_audit_sheet_has_one_row_per_affected_flight(pipeline):
    ws = openpyxl.load_workbook(pipeline['path'], read_only=True)['Audit_GDP (RBS)']
    rows = list(ws.iter_rows(values_only=True))
    assert len(rows) - 1 == len(pipeline['scenarios']['GDP (RBS)'])
    assert {'CTA', 'CTD', 'flight_status'} <= set(rows[0])


def test_compression_sheet_matches_the_compression_run(pipeline):
    res = pipeline['res']
    table = excel.compression_table(res['allocation'], res['compressed_allocation'])
    summary = res['compression_summary']
    assert (table['status'] == 'Cancelled').sum() == summary['cancelled_flights'] > 0
    cancelled = table[table['status'] == 'Cancelled']
    assert (cancelled['airline'] == summary['airline']).all()           # one airline cancels
    assert (cancelled['delay_before'] > summary['delay_threshold_min']).all()
    moved = table[table['status'] == 'Moved earlier']
    assert len(moved) > 0
    assert (moved['minutes_saved'] > 0).all()                     # the old sheet always reported 0
    assert (moved['CTA_after'] >= moved['ETA']).all()


def test_every_wp5_kpi_is_mapped_to_a_kpa(pipeline):
    assert 'Other' not in set(pipeline['wp5']['KPA'])
    assert {label for label, _ in SUMMARY_ROWS} <= set(KPI_TO_KPA)
    assert len(pipeline['wp5'].columns) == 2 + 6                      # KPA, Metric + six methods
