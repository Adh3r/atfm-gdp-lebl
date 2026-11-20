# =============================================================================
# src/main.py
# Entry point: runs the whole project (WP1 -> WP5) on the selected traffic day.
#   python src/main.py
# =============================================================================

import os
import time

import pandas as pd

import config
import lib_data_prep     as prep
import lib_excel_export  as excel
import lib_gdp_core      as gdp
import lib_intermodal    as inter
import lib_sensitivity   as sens
import lib_visualization as vis
from config import CFG, FS_CONTROLLED
from lib_gdp_core import calcular_retraso_minimo_newell
from lib_ghp_solver import MAX_AIR_DELAY_MIN, ejecutar_ghp_completo, sensibilidad_coste
from lib_metrics import evaluar_escenario, resumen_escenarios

LINEA = "=" * 70


def _titulo(texto: str) -> None:
    print("\n" + LINEA + "\n" + texto + "\n" + LINEA)


def ejecutar_proyecto_completo() -> None:
    start_time = time.time()
    _titulo("ATFM simulator - Barcelona-El Prat (LEBL)")

    BASE_DIR = config.BASE_DIR
    PATH_VUELOS_RAW   = config.traffic_file()
    PATH_OUTPUT_CSV   = os.path.join(BASE_DIR, 'data/processed/vuelos_finales_gdp.csv')
    PATH_OUTPUT_EXCEL = os.path.join(BASE_DIR, 'data/processed/auditoria_completa.xlsx')
    PROCESSED = os.path.join(BASE_DIR, 'data', 'processed')
    FIGURES = os.path.join(BASE_DIR, 'output', 'figures')
    os.makedirs(PROCESSED, exist_ok=True)

    rutas_graficos = {
        'cum': os.path.join(FIGURES, '1_diagrama_newell.png'),
        'bal': os.path.join(FIGURES, '2_balance_capacidad.png'),
    }

    # -------------------------------------------------------------------------
    # WP1: data preparation
    # -------------------------------------------------------------------------
    print("\n[WP1] Data preparation")
    print(f"   Traffic: {os.path.relpath(PATH_VUELOS_RAW, BASE_DIR)}")
    if os.path.abspath(PATH_VUELOS_RAW) == os.path.abspath(config.SAMPLE_TRAFFIC):
        print("   NOTE: running on the SYNTHETIC sample day (the real traffic file is not redistributed).")
    params = CFG.to_params_dict()
    h_start_min = params['H_START']
    df_vuelos = prep.preparar_vuelos(PATH_VUELOS_RAW, config.FLEET_TABLE)
    con_rotacion = df_vuelos['tat_programado_min'].notna().sum()
    print(f"   {len(df_vuelos)} arrivals; {con_rotacion} with a later departure of the same "
          "aircraft (turnaround known)")

    # -------------------------------------------------------------------------
    # WP2: GDP with Ration-By-Schedule
    # -------------------------------------------------------------------------
    _titulo("[WP2] Ground Delay Program (Ration-By-Schedule)")
    resultados = gdp.ejecutar_nucleo_gdp(df_vuelos, params)
    df_final = resultados['vuelos_asignados']
    df_slots = resultados['slots']
    slots_list = list(df_slots['slot_start_min'])
    controlados = df_final[df_final['flight_status'] == FS_CONTROLLED]
    h_noreg_base = resultados['h_noreg']
    print(f"   HNoReg: {h_noreg_base // 60:02d}:{h_noreg_base % 60:02d} UTC")
    print(f"   Affected flights: {len(df_final)} ({len(controlados)} controlled, "
          f"{len(df_final) - len(controlados)} exempt)")
    print(f"   Mean delay: {df_final['total_delay'].mean():.1f} min/flight")

    print("\n[WP2] Sensitivity analysis (radius of exemption x file time)")
    sens.ejecutar_analisis_sensibilidad(df_vuelos, params, BASE_DIR)

    # -------------------------------------------------------------------------
    # WP3: Ground Holding Problem
    # -------------------------------------------------------------------------
    _titulo("[WP3] Ground Holding Problem (emissions and cost objectives)")
    resultados_ghp = ejecutar_ghp_completo(df_final, slots_list, params)

    # Task 1 validation: with r_f = 1 the GHP must reproduce the RBS total delay.
    total_rbs = df_final['total_delay'].sum()
    total_val = resultados_ghp['GHP_Validation']['total_delay'].sum()
    estado = "OK" if abs(total_rbs - total_val) < 1e-6 else "MISMATCH"
    print(f"   Validation (r_f = 1): GHP {total_val:.1f} min vs RBS {total_rbs:.1f} min -> {estado}")
    print(f"   Max air delay <= {MAX_AIR_DELAY_MIN} min in every scenario: "
          f"{'yes' if resultados_ghp['max_air_delay_ok'] else 'NO'}")

    escenarios_wp3 = {
        'GDP (RBS)':     df_final,
        'GHP emissions': resultados_ghp['GHP_Emissions'],
        'GHP cost':      resultados_ghp['GHP_Cost'],
    }
    kpis_wp3 = {n: evaluar_escenario(d, n, h_start_min) for n, d in escenarios_wp3.items()}
    escenarios_dict = dict(escenarios_wp3)
    lista_kpis = list(kpis_wp3.values())

    comparativa_wp3 = resumen_escenarios(kpis_wp3, extras={
        'HNoReg (min UTC)': {n: h_noreg_base for n in escenarios_wp3},
    })
    print("\n   WP3 summary (affected flights, same regulation and slots):")
    print(comparativa_wp3.to_string(index=False))

    # Task 3 validation: vary the cost parameters r_f and see their impact.
    df_sens_coste = sensibilidad_coste(df_final, slots_list)
    print("\n   WP3 cost-model sensitivity (each GHP solution evaluated under every model):")
    print(df_sens_coste.to_string(index=False))

    pd.DataFrame(lista_kpis).to_csv(os.path.join(PROCESSED, 'wp3_dashboard_crudo.csv'), index=False)
    comparativa_wp3.to_csv(os.path.join(PROCESSED, 'wp3_resumen_ejecutivo.csv'), index=False)
    df_sens_coste.to_csv(os.path.join(PROCESSED, 'wp3_sensibilidad_coste.csv'), index=False)

    from lib_ghp_solver import generar_graficas_wp3
    generar_graficas_wp3(
        {'GDP': df_final, 'GHP_Emissions': resultados_ghp['GHP_Emissions'],
         'GHP_Cost': resultados_ghp['GHP_Cost']},
        os.path.join(FIGURES, 'wp3'),
    )

    # -------------------------------------------------------------------------
    # WP4: intermodality (high-speed rail)
    # -------------------------------------------------------------------------
    resultados_wp4 = inter.ejecutar_analisis_intermodal(
        df_vuelos, resultados, resultados_ghp, params, BASE_DIR)
    df_tabla_intermodal = resultados_wp4['df_comparativa']
    for nombre, df_esc in resultados_wp4['escenarios_reducidos'].items():
        escenarios_dict[nombre] = df_esc
        lista_kpis.append(resultados_wp4['kpis'][nombre])
    df_dashboard = pd.DataFrame(lista_kpis)

    from generar_graficos_wp4 import generar_graficas_wp4
    generar_graficas_wp4(resultados, resultados_wp4, os.path.join(FIGURES, 'wp4'))

    # -------------------------------------------------------------------------
    # WP5: final comparison and deliverables
    # -------------------------------------------------------------------------
    _titulo("[WP5] Final comparison and deliverables")
    df_final.to_csv(PATH_OUTPUT_CSV, index=False)

    from lib_comparison import grafica_final, tabla_final
    df_wp5 = tabla_final(df_tabla_intermodal)
    df_wp5.to_csv(os.path.join(PROCESSED, 'wp5_comparacion_final.csv'), index=False)
    grafica_final(resultados_wp4['kpis'], os.path.join(FIGURES, 'wp5'))
    print("\n   WP5 final comparison (KPIs by SESAR KPA):")
    print(df_wp5.to_string(index=False))

    excel.exportar_auditoria_excel(
        df_vuelos_crudo=resultados['vuelos_crudos'],
        escenarios_dict=escenarios_dict,
        df_dashboard=df_dashboard,
        df_slots=df_slots,
        params=resultados['params'],
        h_noreg=h_noreg_base,
        timeline=resultados['timeline'],
        r_min_newell=calcular_retraso_minimo_newell(resultados['timeline']),
        path=PATH_OUTPUT_EXCEL,
        df_res_comprimido=resultados.get('vuelos_comprimidos'),
        df_intermodal=df_tabla_intermodal,
        tablas_resumen={
            'WP3_GHP_vs_GDP': comparativa_wp3,
            'WP3_Cost_sensitivity': df_sens_coste,
            'WP4_Intermodal': df_tabla_intermodal,
            'WP5_Final_by_KPA': df_wp5,
        },
    )

    vis.generar_graficos_fase2(
        timeline=resultados['timeline'], df_vuelos=df_vuelos, df_res=df_final,
        params=params, h_noreg=h_noreg_base, paths=rutas_graficos,
    )

    _titulo("Done")
    print("   Flight list (RBS):        data/processed/vuelos_finales_gdp.csv")
    print("   Audit workbook:           data/processed/auditoria_completa.xlsx")
    print("   WP2 sensitivity grid:     data/processed/sensitivity_grid.csv")
    print("   WP3 summary:              data/processed/wp3_resumen_ejecutivo.csv")
    print("   WP3 cost sensitivity:     data/processed/wp3_sensibilidad_coste.csv")
    print("   WP4 comparison:           data/processed/wp4_comparativa_intermodal.csv")
    print("   WP5 comparison:           data/processed/wp5_comparacion_final.csv")
    print("   Figures:                  output/figures/")
    print(f"\n   Run time: {time.time() - start_time:.1f} s")


if __name__ == "__main__":
    ejecutar_proyecto_completo()
