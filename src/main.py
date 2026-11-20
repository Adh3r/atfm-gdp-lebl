# =============================================================================
# src/main.py
# PUNTO DE ENTRADA DEL PROYECTO — Ejecuta la simulación completa.
# =============================================================================

import os
import traceback
import pandas as pd
import time

import lib_data_prep     as prep
import lib_gdp_core      as gdp
import lib_excel_export  as excel
import lib_sensitivity   as sens
import lib_visualization as vis
import lib_intermodal    as inter 
from lib_metrics         import evaluar_escenario, resumen_escenarios # <-- NUEVO MOTOR DE MÉTRICAS

import config
from config import CFG, FS_CONTROLLED
from lib_gdp_core import calcular_retraso_minimo_newell
from lib_ghp_solver import MAX_AIR_DELAY_MIN, ejecutar_ghp_completo

# =============================================================================
# ORQUESTADOR PRINCIPAL
# =============================================================================

def ejecutar_proyecto_completo() -> None:

    # 1. Arrancamos el cronómetro
    start_time = time.time()
    print("\n" + "═" * 70)
    print("🚀 INICIANDO SIMULADOR ATM — AEROPUERTO DE BARCELONA (LEBL)")
    print("═" * 70)

    # Rutas
    BASE_DIR = config.BASE_DIR
    PATH_VUELOS_RAW   = config.traffic_file()
    PATH_FLOTA_RAW    = config.FLEET_TABLE
    PATH_OUTPUT_CSV   = os.path.join(BASE_DIR, 'data/processed/vuelos_finales_gdp.csv')
    PATH_OUTPUT_EXCEL = os.path.join(BASE_DIR, 'data/processed/auditoria_completa.xlsx')

    rutas_graficos = {
        'cum': os.path.join(BASE_DIR, 'output/figures/1_diagrama_newell.png'),
        'bal': os.path.join(BASE_DIR, 'output/figures/2_balance_capacidad.png'),
    }

    # -------------------------------------------------------------------------
    # WP1: PREPARACIÓN DE DATOS
    # -------------------------------------------------------------------------
    print("\n[WP1] Preparando datos y escenario...")
    print(f"   • Tráfico: {os.path.relpath(PATH_VUELOS_RAW, BASE_DIR)}")
    if os.path.abspath(PATH_VUELOS_RAW) == os.path.abspath(config.SAMPLE_TRAFFIC):
        print("   ⚠️  Real traffic file not found: running on the SYNTHETIC sample day.")
    params = CFG.to_params_dict()
    h_start_min = params.get('H_START', 0) # Necesario para evaluar retraso irrecuperable
    df_vuelos = prep.preparar_vuelos(PATH_VUELOS_RAW, PATH_FLOTA_RAW)
    print(f"   ✅ Datos cargados y depurados: {len(df_vuelos)} vuelos en la red.")

    # -------------------------------------------------------------------------
    # WP2: SIMULACIÓN GDP BASE
    # -------------------------------------------------------------------------
    print("\n" + "═" * 70)
    print("🛫 [WP2] EJECUTANDO MOTOR DE SIMULACIÓN GDP (Algoritmo RBS)")
    print("═" * 70)
    
    resultados = gdp.ejecutar_nucleo_gdp(df_vuelos, params)

    df_final = resultados['vuelos_asignados']
    df_slots = resultados['slots']
    slots_list = list(df_slots['slot_start_min'])
    candidatos = df_final[df_final['flight_status'] == FS_CONTROLLED]
    
    print(f"   • Vuelos regulados (GDP): {len(candidatos)}")
    print(f"   • Vuelos exentos:         {len(df_final) - len(candidatos)}")
    print(f"   • Retraso medio global:   {df_final['total_delay'].mean():.1f} min/vuelo")

    # Inicializamos el diccionario maestro de escenarios y la lista de KPIs
    escenarios_dict = {'GDP_Basic_Scenario': df_final}
    lista_kpis = [evaluar_escenario(df_final, 'GDP_Basic_Scenario', h_start_min)]

    # -------------------------------------------------------------------------
    # WP2 (EXTRA): ANÁLISIS DE SENSIBILIDAD
    # -------------------------------------------------------------------------
    print("\n" + "═" * 70)
    print("🌡️  [WP2-B] ANÁLISIS DE SENSIBILIDAD (Radio vs H_File)")
    print("═" * 70)
    sens.ejecutar_analisis_sensibilidad(df_vuelos, params, BASE_DIR)
    print("   ✅ Matrices guardadas y Heatmaps generados.")

    # -------------------------------------------------------------------------
    # WP3: GROUND HOLDING PROBLEM (GHP)
    # -------------------------------------------------------------------------
    print("\n" + "═" * 70)
    print("🧠 [WP3] GROUND HOLDING PROBLEM (emissions and cost objectives)")
    print("═" * 70)

    resultados_ghp = ejecutar_ghp_completo(df_final, slots_list, params)

    # Task 1 validation: with r_f = 1 the GHP must reproduce the RBS total delay.
    total_rbs = df_final['total_delay'].sum()
    total_val = resultados_ghp['GHP_Validation']['total_delay'].sum()
    estado = "OK" if abs(total_rbs - total_val) < 1e-6 else "MISMATCH"
    print(f"   • Validation (r_f = 1): GHP {total_val:.1f} min vs RBS {total_rbs:.1f} min -> {estado}")
    print(f"   • Max air delay <= {MAX_AIR_DELAY_MIN} min in every scenario: "
          f"{'yes' if resultados_ghp['max_air_delay_ok'] else 'NO'}")

    escenarios_wp3 = {
        'GDP (RBS)':        df_final,
        'GHP emissions':    resultados_ghp['GHP_Emissions'],
        'GHP cost':         resultados_ghp['GHP_Cost'],
    }
    kpis_wp3 = {n: evaluar_escenario(d, n, h_start_min) for n, d in escenarios_wp3.items()}
    escenarios_dict = dict(escenarios_wp3)
    lista_kpis = list(kpis_wp3.values())
    df_dashboard = pd.DataFrame(lista_kpis)

    h_noreg_base = resultados['h_noreg']
    comparativa_wp3 = resumen_escenarios(kpis_wp3, extras={
        'HNoReg (min UTC)':  {n: h_noreg_base for n in escenarios_wp3},
    })

    print("\n 📊 WP3 SUMMARY (affected flights, same regulation and slots):")
    print(comparativa_wp3.to_string(index=False))

    path_wp3_original = os.path.join(BASE_DIR, 'data/processed/wp3_dashboard_crudo.csv')
    path_wp3_resumen = os.path.join(BASE_DIR, 'data/processed/wp3_resumen_ejecutivo.csv')
    os.makedirs(os.path.dirname(path_wp3_resumen), exist_ok=True)
    df_dashboard.to_csv(path_wp3_original, index=False)
    comparativa_wp3.to_csv(path_wp3_resumen, index=False)

    from lib_ghp_solver import generar_graficas_wp3
    generar_graficas_wp3(
        {'GDP': df_final, 'GHP_Emissions': resultados_ghp['GHP_Emissions'],
         'GHP_Cost': resultados_ghp['GHP_Cost']},
        os.path.join(BASE_DIR, 'output', 'figures', 'wp3'),
    )
    print(f"   ✅ WP3 tables: {os.path.relpath(path_wp3_resumen, BASE_DIR)}; figures: output/figures/wp3/")

    

    # -------------------------------------------------------------------------
    # WP4: ANÁLISIS INTERMODAL
    # -------------------------------------------------------------------------
    df_tabla_intermodal = None
    try:
        # Pasamos resultados_ghp como tercer argumento
        resultados_wp4 = inter.ejecutar_analisis_intermodal(df_vuelos, resultados, resultados_ghp, params, BASE_DIR)
        
        if isinstance(resultados_wp4, dict):
            # 1. Capturamos la tabla comparativa para la Pestaña 8
            df_tabla_intermodal = resultados_wp4['df_comparativa']
            
            # 2. Capturamos los resultados del GHP para las DOS funciones de coste
            df_intermodal_gdp = resultados_wp4['resultados_reducido']['resultados_gdp']['vuelos_asignados']
            df_intermodal_coste = resultados_wp4['resultados_reducido']['resultados_ghp']['GHP_Cost']
            df_intermodal_co2 = resultados_wp4['resultados_reducido']['resultados_ghp']['GHP_Emissions']
            
            # 3. Los inyectamos en el diccionario general de escenarios
            escenarios_dict['Intermodal_GDP'] = df_intermodal_gdp
            escenarios_dict['Intermodal_GHP_Coste'] = df_intermodal_coste
            escenarios_dict['Intermodal_GHP_CO2'] = df_intermodal_co2
            
            # 4. Actualizamos el Dashboard general (WP5)
            lista_kpis.append(evaluar_escenario(df_intermodal_gdp, 'Intermodal_GDP', h_start_min))
            lista_kpis.append(evaluar_escenario(df_intermodal_coste, 'Intermodal_GHP_Coste', h_start_min))
            lista_kpis.append(evaluar_escenario(df_intermodal_co2, 'Intermodal_GHP_CO2', h_start_min))
            df_dashboard = pd.DataFrame(lista_kpis)
            
            print("✅ Datos intermodales procesados y listos para exportar.")
            
    except Exception as e:
        print(f"\n❌ ERROR en WP4. Detalle: {e}")
        traceback.print_exc()

    # -------------------------------------------------------------------------
    # WP5: ENTREGABLES (EXCEL y GRÁFICOS)
    # -------------------------------------------------------------------------
    print("\n" + "═" * 70)
    print("📁 [WP5] GENERANDO ENTREGABLES MAESTROS (AUDITORÍA Y GRÁFICOS)")
    print("═" * 70)

    os.makedirs(os.path.dirname(PATH_OUTPUT_CSV), exist_ok=True)
    df_final.to_csv(PATH_OUTPUT_CSV, index=False)
    print("   ✅ CSV maestro guardado.")

    r_min_newell = calcular_retraso_minimo_newell(resultados['timeline'])
    
    # Llamada refactorizada al exportador Multi-Escenario
    excel.exportar_auditoria_excel(
        df_vuelos_crudo=resultados['vuelos_crudos'],
        escenarios_dict=escenarios_dict,
        df_dashboard=df_dashboard,
        df_slots=df_slots,
        params=resultados['params'],
        h_noreg=resultados['h_noreg'],
        timeline=resultados['timeline'],
        r_min_newell=r_min_newell,
        path=PATH_OUTPUT_EXCEL,
        df_res_comprimido=resultados.get('vuelos_comprimidos'),
        df_intermodal=df_tabla_intermodal
    )

    vis.generar_graficos_fase2(
        timeline=resultados['timeline'], df_vuelos=df_vuelos, df_res=resultados['vuelos_asignados'],
        params=params, h_noreg=resultados['h_noreg'], paths=rutas_graficos,
    )
    print("   ✅ Gráficos de Newell y Capacidad exportados.")

    # -------------------------------------------------------------------------
    # RESUMEN FINAL
    # -------------------------------------------------------------------------
    print("\n" + "═" * 70)
    print("✨ PROCESO COMPLETO FINALIZADO CON ÉXITO ✨")
    print(f"   📄 CSV maestro:          data/processed/vuelos_finales_gdp.csv")
    print(f"   📊 Excel de auditoría:   data/processed/auditoria_completa.xlsx")
    print(f"   📈 Dashboard KPIs:       data/processed/wp3_resumen_ejecutivo.csv")
    print(f"   🚆 Comparativa WP4:      data/processed/wp4_comparativa_intermodal.csv")
    print(f"   🌡️  Matriz sensibilidad: data/processed/sensitivity_grid.csv")
    print("═" * 70 + "\n")
    
    end_time = time.time()
    tiempo_ejecucion = end_time - start_time
    print(f"\n⏱️ TIEMPO DE EJECUCIÓN TOTAL: {tiempo_ejecucion:.4f} segundos")
    print("="*50)

if __name__ == "__main__":
    ejecutar_proyecto_completo()