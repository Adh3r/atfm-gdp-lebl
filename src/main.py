# =============================================================================
# src/main.py
# Entry point: runs the whole project (WP1 -> WP5) on the selected traffic day.
#   python src/main.py
# =============================================================================

import os
import time

import pandas as pd

import config
import lib_data_prep as prep
import lib_excel_export as excel
import lib_gdp_core as gdp
import lib_intermodal as inter
import lib_sensitivity as sens
import lib_visualization as vis
from config import CFG, FS_CONTROLLED
from cost_model import ground_co2_kg_per_min
from lib_gdp_core import newell_minimum_delay
from lib_ghp_solver import MAX_AIR_DELAY_MIN, cost_model_sensitivity, run_ghp
from lib_metrics import evaluate_scenario, summary_table

RULE = "=" * 70


def _heading(text: str) -> None:
    print("\n" + RULE + "\n" + text + "\n" + RULE)


def main() -> None:
    start_time = time.time()
    _heading("ATFM simulator - Barcelona-El Prat (LEBL)")

    BASE_DIR = config.BASE_DIR
    TRAFFIC_PATH = config.traffic_file()
    OUTPUT_CSV = os.path.join(BASE_DIR, 'data/processed/rbs_allocation.csv')
    OUTPUT_EXCEL = os.path.join(BASE_DIR, 'data/processed/audit_workbook.xlsx')
    PROCESSED = os.path.join(BASE_DIR, 'data', 'processed')
    FIGURES = os.path.join(BASE_DIR, 'output', 'figures')
    os.makedirs(PROCESSED, exist_ok=True)

    wp2_figure_paths = {
        'cum': os.path.join(FIGURES, '1_newell_curves.png'),
        'bal': os.path.join(FIGURES, '2_capacity_balance.png'),
    }

    # -------------------------------------------------------------------------
    # WP1: data preparation
    # -------------------------------------------------------------------------
    print("\n[WP1] Data preparation")
    print(f"   Traffic: {os.path.relpath(TRAFFIC_PATH, BASE_DIR)}")
    if os.path.abspath(TRAFFIC_PATH) == os.path.abspath(config.SAMPLE_TRAFFIC):
        print("   NOTE: running on the SYNTHETIC sample day (the real traffic file is not redistributed).")
    params = CFG.to_params_dict()
    h_start_min = params['H_START']
    df_flights = prep.prepare_flights(TRAFFIC_PATH, config.FLEET_TABLE)
    with_rotation = df_flights['scheduled_turnaround_min'].notna().sum()
    print(
        f"   {len(df_flights)} arrivals; {with_rotation} with a later departure of the same "
        "aircraft (turnaround known)"
    )

    # -------------------------------------------------------------------------
    # WP2: GDP with Ration-By-Schedule
    # -------------------------------------------------------------------------
    _heading("[WP2] Ground Delay Program (Ration-By-Schedule)")
    results = gdp.run_gdp(df_flights, params)
    df_rbs = results['allocation']
    df_slots = results['slots']
    slot_list = list(df_slots['slot_start_min'])
    n_controlled = int((df_rbs['flight_status'] == FS_CONTROLLED).sum())
    h_noreg_base = results['h_noreg']
    newell_min = newell_minimum_delay(results['timeline'])
    print(
        f"   HNoReg: {h_noreg_base // 60:02d}:{h_noreg_base % 60:02d} UTC; "
        f"total delay from the Newell curves: {newell_min:,.0f} min"
    )
    print(
        f"   Affected flights: {len(df_rbs)} ({n_controlled} controlled, {len(df_rbs) - n_controlled} exempt)"
    )

    # WP2 KPIs (statement: total, max and mean air/ground delay, their RSD,
    # air/ground CO2, delayed flights, OTP) and the cancellation exercise.
    wp2_summary = summary_table(
        {'GDP (RBS)': evaluate_scenario(df_rbs, 'GDP (RBS)', h_start_min)},
        extras={
            'HNoReg (min UTC)': {'GDP (RBS)': h_noreg_base},
            'Total delay from the Newell curves (min)': {'GDP (RBS)': round(newell_min, 1)},
            'Ground delay CO2, APU never on / on 50 % (kg)': {
                'GDP (RBS)': '{:,.0f} / {:,.0f}'.format(
                    *(
                        (ground_co2_kg_per_min(df_rbs['size_seats_avg'], sc) * df_rbs['ground_delay']).sum()
                        for sc in ('low', 'high')
                    )
                )
            },
        },
    )
    print(wp2_summary.to_string(index=False))
    wp2_cancellations = pd.DataFrame(
        [(k, round(v, 1) if isinstance(v, float) else v) for k, v in results['compression_summary'].items()],
        columns=['Metric', 'Value'],
    )
    wp2_summary.to_csv(os.path.join(PROCESSED, 'wp2_summary.csv'), index=False)
    wp2_cancellations.to_csv(os.path.join(PROCESSED, 'wp2_cancellations.csv'), index=False)

    print("\n[WP2] Sensitivity analysis (radius of exemption x file time)")
    sens.run_sensitivity(df_flights, params, BASE_DIR)

    # -------------------------------------------------------------------------
    # WP3: Ground Holding Problem
    # -------------------------------------------------------------------------
    _heading("[WP3] Ground Holding Problem (emissions and cost objectives)")
    ghp_results = run_ghp(df_rbs, slot_list, params)

    # Task 1 validation: with r_f = 1 the GHP must reproduce the RBS total delay.
    total_rbs = df_rbs['total_delay'].sum()
    total_validation = ghp_results['GHP_Validation']['total_delay'].sum()
    status = "OK" if abs(total_rbs - total_validation) < 1e-6 else "MISMATCH"
    print(f"   Validation (r_f = 1): GHP {total_validation:.1f} min vs RBS {total_rbs:.1f} min -> {status}")
    print(
        f"   Max air delay <= {MAX_AIR_DELAY_MIN} min in every scenario: "
        f"{'yes' if ghp_results['max_air_delay_ok'] else 'NO'}"
    )

    wp3_scenarios = {
        'GDP (RBS)': df_rbs,
        'GHP emissions': ghp_results['GHP_Emissions'],
        'GHP cost': ghp_results['GHP_Cost'],
    }
    wp3_kpis = {n: evaluate_scenario(d, n, h_start_min) for n, d in wp3_scenarios.items()}
    scenario_tables = dict(wp3_scenarios)
    kpi_rows = list(wp3_kpis.values())

    wp3_summary = summary_table(
        wp3_kpis,
        extras={
            'HNoReg (min UTC)': {n: h_noreg_base for n in wp3_scenarios},
        },
    )
    print("\n   WP3 summary (affected flights, same regulation and slots):")
    print(wp3_summary.to_string(index=False))

    # Task 3 validation: vary the cost parameters r_f and see their impact.
    df_cost_sensitivity = cost_model_sensitivity(df_rbs, slot_list)
    print("\n   WP3 cost-model sensitivity (each GHP solution evaluated under every model):")
    print(df_cost_sensitivity.to_string(index=False))

    pd.DataFrame(kpi_rows).to_csv(os.path.join(PROCESSED, 'wp3_kpis.csv'), index=False)
    wp3_summary.to_csv(os.path.join(PROCESSED, 'wp3_summary.csv'), index=False)
    df_cost_sensitivity.to_csv(os.path.join(PROCESSED, 'wp3_cost_sensitivity.csv'), index=False)

    from lib_ghp_solver import plot_wp3_figures

    plot_wp3_figures(
        {'GDP': df_rbs, 'GHP_Emissions': ghp_results['GHP_Emissions'], 'GHP_Cost': ghp_results['GHP_Cost']},
        os.path.join(FIGURES, 'wp3'),
    )

    # -------------------------------------------------------------------------
    # WP4: intermodality (high-speed rail)
    # -------------------------------------------------------------------------
    wp4_results = inter.run_intermodal_analysis(df_flights, results, ghp_results, params, BASE_DIR)
    df_wp4_summary = wp4_results['summary']
    for name, df_esc in wp4_results['reduced_scenarios'].items():
        scenario_tables[name] = df_esc
        kpi_rows.append(wp4_results['kpis'][name])
    df_dashboard = pd.DataFrame(kpi_rows)

    from lib_wp4_figures import plot_wp4_figures

    plot_wp4_figures(results, wp4_results, os.path.join(FIGURES, 'wp4'))

    # -------------------------------------------------------------------------
    # WP5: final comparison and deliverables
    # -------------------------------------------------------------------------
    _heading("[WP5] Final comparison and deliverables")
    df_rbs.to_csv(OUTPUT_CSV, index=False)

    from lib_comparison import final_table, plot_final

    df_wp5 = final_table(df_wp4_summary)
    df_wp5.to_csv(os.path.join(PROCESSED, 'wp5_final_comparison.csv'), index=False)
    plot_final(wp4_results['kpis'], os.path.join(FIGURES, 'wp5'))
    print("\n   WP5 final comparison (KPIs by SESAR KPA):")
    print(df_wp5.to_string(index=False))

    excel.export_audit_workbook(
        df_flights_raw=results['flights'],
        scenario_tables=scenario_tables,
        df_dashboard=df_dashboard,
        df_slots=df_slots,
        params=results['params'],
        h_noreg=h_noreg_base,
        timeline=results['timeline'],
        newell_min_delay=newell_min,
        path=OUTPUT_EXCEL,
        df_compressed=results.get('compressed_allocation'),
        df_intermodal=df_wp4_summary,
        summary_tables={
            'WP2_GDP_KPIs': wp2_summary,
            'WP2_Cancellation_summary': wp2_cancellations,
            'WP3_GHP_vs_GDP': wp3_summary,
            'WP3_Cost_sensitivity': df_cost_sensitivity,
            'WP4_Intermodal': df_wp4_summary,
            'WP5_Final_by_KPA': df_wp5,
        },
    )

    vis.plot_wp2_figures(
        timeline=results['timeline'],
        df_flights=df_flights,
        df_rbs=df_rbs,
        params=params,
        h_noreg=h_noreg_base,
        paths=wp2_figure_paths,
    )

    _heading("Done")
    print("   Flight list (RBS):        data/processed/rbs_allocation.csv")
    print("   Audit workbook:           data/processed/audit_workbook.xlsx")
    print("   WP2 KPIs:                 data/processed/wp2_summary.csv")
    print("   WP2 cancellations:        data/processed/wp2_cancellations.csv")
    print("   WP2 sensitivity grid:     data/processed/sensitivity_grid.csv")
    print("   WP3 summary:              data/processed/wp3_summary.csv")
    print("   WP3 cost sensitivity:     data/processed/wp3_cost_sensitivity.csv")
    print("   WP4 comparison:           data/processed/wp4_summary.csv")
    print("   WP5 comparison:           data/processed/wp5_final_comparison.csv")
    print("   Figures:                  output/figures/")
    print(f"\n   Run time: {time.time() - start_time:.1f} s")


if __name__ == "__main__":
    main()
