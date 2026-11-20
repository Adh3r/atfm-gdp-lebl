# =============================================================================
# src/generar_graficos_wp4.py
# WP4 figures (SVG), drawn from the simulation results passed in by main.py:
#   1. door-to-door time by route: air (nominal and with its GDP delay) vs HSR
#   2. CO2 change of the substitution: direct (flights - rail) and network
#      (delay CO2 of the reduced demand) for GDP and both GHP objectives
#   3. queue length over time from the Newell curves, baseline vs reduced demand
# =============================================================================

import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams.update({
    'font.size': 11,
    'axes.titlesize': 13,
    'axes.grid': True,
    'grid.alpha': 0.3,
    'grid.linestyle': '--',
})


def grafica_d2d(df_modal: pd.DataFrame, out_dir: str) -> str:
    rutas = (df_modal.groupby('ADEP')
             .agg(n=('ARCID', 'size'),
                  aire=('tiempo_d2d_avion_NOMINAL_min', 'mean'),
                  retraso=('retraso_ATFM_min', 'mean'),
                  tren=('tiempo_d2d_tren_min', 'mean'))
             .sort_values('aire'))
    x = np.arange(len(rutas))
    w = 0.38
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(x - w / 2, rutas['aire'], w, label='Air D2D, nominal', color='#4A90E2')
    ax.bar(x - w / 2, rutas['retraso'], w, bottom=rutas['aire'],
           label='Mean regulation delay', color='#E74C3C', hatch='//')
    ax.bar(x + w / 2, rutas['tren'], w, label='HSR D2D', color='#2ECC71')
    ax.set_xticks(x)
    ax.set_xticklabels([f"{a}\n({n} flt)" for a, n in zip(rutas.index, rutas['n'])])
    ax.set_ylabel('Door-to-door time (min)')
    ax.set_title('Air vs high-speed rail, flights replaced in the regulation')
    ax.set_ylim(0, max((rutas['aire'] + rutas['retraso']).max(), rutas['tren'].max()) * 1.3)
    ax.legend(loc='upper left', ncol=3, fontsize=9)
    fig.tight_layout()
    ruta = os.path.join(out_dir, 'fig1_d2d_by_route.svg')
    fig.savefig(ruta, format='svg')
    plt.close(fig)
    return ruta


def grafica_co2(kpis: dict, co2: dict, out_dir: str) -> str:
    pares = [('GDP (RBS)', 'Intermodal GDP (RBS)'),
             ('GHP emissions', 'Intermodal GHP emissions'),
             ('GHP cost', 'Intermodal GHP cost')]
    directo = (co2['vuelos_sustituidos'] - co2['tren']) / 1000
    red = [(kpis[b]['CO2_Total_Retraso_kg'] - kpis[i]['CO2_Total_Retraso_kg']) / 1000 for b, i in pares]
    x = np.arange(len(pares))
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(x, [directo] * len(pares), 0.55, label='Direct: replaced flights − rail', color='#27AE60')
    ax.bar(x, red, 0.55, bottom=[directo] * len(pares),
           label='Network: less delay CO₂', color='#F39C12')
    for k, r in enumerate(red):
        ax.text(k, directo + r, f'{directo + r:.1f} t', ha='center', va='bottom')
    ax.set_xticks(x)
    ax.set_xticklabels([b for b, _ in pares])
    ax.set_ylabel('CO₂ saved (t)')
    ax.set_title('CO₂ saved by replacing flights with high-speed rail')
    ax.set_ylim(0, (directo + max(red)) * 1.3)
    ax.legend(loc='upper center', ncol=2, fontsize=9)
    fig.tight_layout()
    ruta = os.path.join(out_dir, 'fig2_co2_savings.svg')
    fig.savefig(ruta, format='svg')
    plt.close(fig)
    return ruta


def grafica_colas(timeline_base, timeline_red, h_noreg_base, h_noreg_red, n_sustituidos, out_dir) -> str:
    fig, ax = plt.subplots(figsize=(9, 4.8))
    horas = timeline_base['minuto'] / 60
    ax.plot(horas, timeline_base['queue_size'], color='#E74C3C', linewidth=2, label='Baseline demand')
    ax.plot(timeline_red['minuto'] / 60, timeline_red['queue_size'], color='#3498DB', linewidth=2,
            label=f'Without the {n_sustituidos} rail-replaced flights')
    for h, c in ((h_noreg_base, '#E74C3C'), (h_noreg_red, '#3498DB')):
        ax.axvline(h / 60, color=c, linestyle='--', linewidth=1)
    ax.annotate(f'HNoReg {h_noreg_red // 60:02d}:{h_noreg_red % 60:02d}\n'
                f'({h_noreg_base - h_noreg_red} min earlier)',
                xy=(h_noreg_red / 60, 1), xytext=(h_noreg_red / 60 + 0.6, timeline_base['queue_size'].max() * 0.45),
                arrowprops=dict(arrowstyle='->'), fontsize=10)
    ax.set_xlim(4, 22)
    ax.set_xlabel('Time (UTC hour)')
    ax.set_ylabel('Aircraft in queue (Newell)')
    ax.set_title('Arrival queue: baseline vs rail substitution')
    ax.legend(loc='upper right')
    fig.tight_layout()
    ruta = os.path.join(out_dir, 'fig3_queue_newell.svg')
    fig.savefig(ruta, format='svg')
    plt.close(fig)
    return ruta


def generar_graficas_wp4(resultados_base: dict, resultados_wp4: dict, out_dir: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    red = resultados_wp4['resultados_reducido']['resultados_gdp']
    rutas = []
    if len(resultados_wp4['df_modal']):
        rutas.append(grafica_d2d(resultados_wp4['df_modal'], out_dir))
    rutas.append(grafica_co2(resultados_wp4['kpis'], resultados_wp4['co2'], out_dir))
    rutas.append(grafica_colas(resultados_base['timeline'], red['timeline'],
                               resultados_base['h_noreg'], red['h_noreg'],
                               len(resultados_wp4['df_modal']), out_dir))
    return rutas
