# =============================================================================
# src/lib_visualization.py
# WP2 figures (SVG). Plotting only; no model logic lives here.
#
#   plot_newell()              Newell cumulative demand / capacity curves
#   plot_balance_capacidad()   hourly demand vs. traffic served vs. capacity
#   plot_impacto_economico()   cost of delay: do-nothing vs. GDP
#   plot_equidad_aerolineas()  mean delay per airline under RBS (top 10)
#   generar_graficos_fase2()   draws the four figures above
#   generar_heatmap()          one sensitivity heatmap (R x file time)
# =============================================================================

import os

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib.colors import LogNorm

# Same function as the Excel workbook, so the numbers always match.
from lib_gdp_core import calcular_kpis_economicos

# =============================================================================
# SENSITIVITY HEATMAP (WP2)
# =============================================================================

def generar_heatmap(
    df_matriz: pd.DataFrame,
    titulo: str,
    unidad: str,
    mejor: str,
    ruta_salida: str,
    fila_optima: int,
    columna_optima: int,
) -> None:
    """
    Heatmap of one KPI over the radius of exemption R (columns) and the file
    time Hstart - Hfile (rows), with the best cell outlined.

    The colour scale is logarithmic (the KPIs span orders of magnitude across
    the grid); green is good, red is bad.

    Args:
        df_matriz:      KPI matrix (rows Hstart - Hfile, columns R).
        titulo:         figure title.
        unidad:         unit for the colour bar ('min', 'kg', 'EUR').
        mejor:          'min' if lower is better, 'max' if higher is better.
        ruta_salida:    SVG path.
        fila_optima:    row index of the best cell in df_matriz.
        columna_optima: column index of the best cell.
    """
    # Largest file-time offset at the top; move the best-cell marker with it.
    df_matriz = df_matriz.sort_index(ascending=False)
    fila_optima = (len(df_matriz) - 1) - fila_optima

    plt.figure(figsize=(12, 7))
    valores = df_matriz.values.flatten()
    limite_inferior = max(valores.min(), 0.1)    # LogNorm needs positive limits
    limite_superior = valores.max()

    paleta = plt.get_cmap("RdYlGn" if mejor == 'max' else "RdYlGn_r")
    formato_numero = ",.0f" if any(x in unidad for x in ["EUR", "kg"]) else ".1f"

    ax = sns.heatmap(
        df_matriz,
        annot=True,
        fmt=formato_numero,
        cmap=paleta,
        norm=LogNorm(vmin=limite_inferior, vmax=limite_superior),
        cbar_kws={'label': unidad},
        linewidths=1.5,
        linecolor='white',
        annot_kws={"size": 8, "weight": "bold"},
    )
    ax.set_xticklabels([f"{int(c)} km"  for c in df_matriz.columns])
    ax.set_yticklabels([f"{int(i)} min" for i in df_matriz.index], rotation=0)
    ax.set_xlabel('Radius of exemption R (km)', fontsize=11, fontweight='bold')
    ax.set_ylabel('File time: Hstart − Hfile (min)', fontsize=11, fontweight='bold')
    ax.set_title(titulo.upper(), fontsize=13, fontweight='bold', pad=20)

    ax.add_patch(plt.Rectangle(
        (columna_optima, fila_optima), 1, 1,
        fill=False, edgecolor='blue', linewidth=4, zorder=15,
    ))
    ax.text(
        columna_optima + 0.5, fila_optima + 0.9, '★ BEST',
        ha='center', va='bottom', fontsize=10, color='blue', fontweight='bold',
        bbox=dict(facecolor='white', alpha=0.9, edgecolor='blue', boxstyle='round,pad=0.2'),
    )

    plt.tight_layout()
    os.makedirs(os.path.dirname(ruta_salida), exist_ok=True)
    plt.savefig(ruta_salida, format='svg', transparent=True, bbox_inches='tight')
    plt.close()

# =============================================================================
# 1. NEWELL CUMULATIVE CURVES
# =============================================================================

def plot_newell(
    timeline: pd.DataFrame,
    params: dict,
    h_noreg: int,
    ruta_salida: str,
) -> None:
    """
    Newell cumulative curves: cumulative demand (by ETA) and cumulative
    capacity (AAR, PAAR during the capacity drop). The area between them is
    the total delay; the vertical lines mark Hstart, Hend and HNoReg.

    Args:
        timeline:    Newell timeline ('minuto', 'demand_accum', 'capacity_accum').
        params:      GDP parameters with H_START and H_END.
        h_noreg:     HNoReg (minute the queue dissolves).
        ruta_salida: SVG path.
    """
    plt.figure(figsize=(12, 6))
    horas = timeline['minuto'] / 60

    plt.plot(horas, timeline['demand_accum'],
             label='Cumulative demand (ETA)', color='cornflowerblue', linewidth=2)
    plt.plot(horas, timeline['capacity_accum'],
             '--', label='Cumulative capacity (AAR / PAAR)', color='hotpink', linewidth=2)
    plt.fill_between(horas, timeline['demand_accum'], timeline['capacity_accum'],
                     color='plum', alpha=0.5, label='Queue (delay)')

    plt.axvline(x=params['H_START'] / 60, color='palevioletred',    linestyle=':',  label='Hstart (reduced capacity starts)')
    plt.axvline(x=params['H_END']   / 60, color='mediumaquamarine', linestyle=':',  label='Hend (reduced capacity ends)')
    plt.axvline(x=h_noreg           / 60, color='midnightblue',     linestyle='-.', label='HNoReg (queue dissolved)')

    plt.title("Newell cumulative curves", fontsize=20, fontweight='bold')
    plt.xlabel("UTC time (hours)", fontsize=18)
    plt.ylabel("Cumulative number of aircraft", fontsize=18)
    plt.legend()
    plt.grid(True, alpha=0.4)
    plt.tight_layout()
    plt.savefig(ruta_salida, format='svg', dpi=300, bbox_inches='tight')
    plt.close()

# =============================================================================
# 2. HOURLY CAPACITY BALANCE
# =============================================================================

def plot_balance_capacidad(
    timeline: pd.DataFrame,
    df_vuelos: pd.DataFrame,
    params: dict,
    ruta_salida: str,
) -> None:
    """
    Hourly demand (by ETA), traffic actually served (from the capacity curve)
    and the declared capacity (PAAR during the capacity drop, AAR otherwise).

    Args:
        timeline:    Newell timeline.
        df_vuelos:   flights with 'minutes_eta'.
        params:      GDP parameters (H_START, H_END, AAR, PAAR).
        ruta_salida: SVG path.
    """
    plt.figure(figsize=(12, 6))

    hora_eta = (df_vuelos['minutes_eta'] // 60).astype(int)
    demanda_horaria = hora_eta.value_counts().reindex(range(24), fill_value=0).sort_index()

    servido = []
    for hora in range(24):
        inicio = timeline.loc[hora * 60, 'capacity_accum']
        fin = timeline.loc[min((hora + 1) * 60, 1439), 'capacity_accum']
        servido.append(fin - inicio)

    h_ini, h_fin = int(params['H_START'] / 60), int(params['H_END'] / 60)
    capacidad = [params['PAAR'] if h_ini <= h < h_fin else params['AAR'] for h in range(24)]

    horas = range(24)
    plt.bar(horas, demanda_horaria, color='cornflowerblue', alpha=0.5, width=0.8, edgecolor='black', label='Demand (ETA)')
    plt.bar(horas, servido, color='midnightblue', alpha=0.8, width=0.4, edgecolor='black', label='Traffic served')
    plt.step(horas, capacidad, where='mid', color='hotpink', linewidth=3, label='Capacity (AAR / PAAR)')

    plt.title('Low-visibility capacity drop: demand vs. traffic served', fontsize=20, fontweight='bold')
    plt.xlabel('Time of day (UTC hour)', fontsize=18)
    plt.ylabel('Arrivals per hour', fontsize=18)
    plt.xticks(horas)
    plt.legend(loc='upper left')
    plt.grid(True, axis='y', linestyle='--', alpha=0.6)
    plt.tight_layout()
    plt.savefig(ruta_salida, format='svg', transparent=True, bbox_inches='tight')
    plt.close()

# =============================================================================
# 3. COST OF DELAY: DO-NOTHING VS. GDP
# =============================================================================

def plot_impacto_economico(df_res: pd.DataFrame, ruta_salida: str) -> None:
    """
    Cost of delay with the GDP against the do-nothing case (the same delay
    absorbed in airborne holding), both from the Cook & Tanner model of
    cost_model (see calcular_kpis_economicos).

    Args:
        df_res:      RBS result with delays.
        ruta_salida: SVG path.
    """
    kpis = calcular_kpis_economicos(df_res)
    etiquetas = [
        'Do-nothing\n(all delay airborne)',
        'GDP\n(delay moved to the ground)',
    ]
    valores = [kpis['cost_baseline'], kpis['cost_gdp']]

    plt.figure(figsize=(10, 6))
    barras = plt.bar(etiquetas, valores, color=['crimson', 'mediumseagreen'], edgecolor='black', width=0.6)
    plt.title("Cost of delay: GDP vs. do-nothing", fontsize=14, fontweight='bold')
    plt.ylabel("Cost of delay, Cook & Tanner 2015 (EUR 2014)", fontsize=12)

    valor_maximo = max(valores)
    plt.ylim(0, valor_maximo * 1.30)
    for barra in barras:
        altura = barra.get_height()
        plt.text(barra.get_x() + barra.get_width() / 2, altura + valor_maximo * 0.03,
                 f"EUR {int(altura):,}", ha='center', va='bottom', fontweight='bold', fontsize=11)

    plt.grid(axis='y', linestyle='--', alpha=0.5)
    plt.tight_layout()
    plt.savefig(ruta_salida, format='svg', transparent=True, bbox_inches='tight')
    plt.close()

# =============================================================================
# 4. RBS FAIRNESS BY AIRLINE
# =============================================================================

def plot_equidad_aerolineas(df_res: pd.DataFrame, ruta_salida: str) -> None:
    """
    Mean delay per airline under RBS, for the 10 airlines with most affected
    flights, against the overall mean.

    Args:
        df_res:      RBS result with delays.
        ruta_salida: SVG path.
    """
    top_10 = df_res['airline'].value_counts().head(10).index
    medias = (
        df_res[df_res['airline'].isin(top_10)]
        .groupby('airline')['total_delay'].mean()
        .sort_values(ascending=False)
    )

    plt.figure(figsize=(12, 6))
    plt.bar(medias.index, medias.values, color='mediumpurple', edgecolor='black', alpha=0.8)
    media_global = df_res['total_delay'].mean()
    plt.axhline(y=media_global, color='red', linestyle='--', linewidth=2,
                label=f"Mean delay, all affected flights ({media_global:.1f} min)")

    plt.title("RBS fairness: mean delay per airline (top 10 by affected flights)", fontsize=14, fontweight='bold')
    plt.xlabel("Airline (ICAO designator)", fontsize=12)
    plt.ylabel("Mean delay (min)", fontsize=12)
    plt.legend()
    plt.grid(axis='y', linestyle='--', alpha=0.6)
    plt.tight_layout()
    plt.savefig(ruta_salida, format='svg', transparent=True, bbox_inches='tight')
    plt.close()

# =============================================================================
# ORCHESTRATOR
# =============================================================================

def generar_graficos_fase2(
    timeline: pd.DataFrame,
    df_vuelos: pd.DataFrame,
    df_res: pd.DataFrame,
    params: dict,
    h_noreg: int,
    paths: dict,
) -> None:
    """Draw the four WP2 figures (always as SVG, whatever extension paths has)."""
    carpeta = os.path.dirname(paths['cum'])
    os.makedirs(carpeta, exist_ok=True)

    plot_newell(timeline, params, h_noreg, paths['cum'].replace('.png', '.svg'))
    plot_balance_capacidad(timeline, df_vuelos, params, paths['bal'].replace('.png', '.svg'))
    plot_impacto_economico(df_res, os.path.join(carpeta, '3_impacto_economico.svg'))
    plot_equidad_aerolineas(df_res, os.path.join(carpeta, '4_equidad_aerolineas.svg'))
    print("   WP2 figures (SVG) written to output/figures/")


# =============================================================================
# DEBUG MODE: python src/lib_visualization.py
# =============================================================================
if __name__ == "__main__":
    import config
    import lib_data_prep as prep
    import lib_gdp_core as gdp
    from config import CFG

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    carpeta_test = os.path.join(base, 'output', 'figures', 'TEST_VISUALS')
    params = CFG.to_params_dict()

    df_vuelos = prep.preparar_vuelos(config.traffic_file(), config.FLEET_TABLE)
    resultados = gdp.ejecutar_nucleo_gdp(df_vuelos, params)
    generar_graficos_fase2(
        timeline=resultados['timeline'],
        df_vuelos=df_vuelos,
        df_res=resultados['vuelos_asignados'],
        params=params,
        h_noreg=resultados['h_noreg'],
        paths={'cum': os.path.join(carpeta_test, '1_newell.svg'),
               'bal': os.path.join(carpeta_test, '2_balance.svg')},
    )
