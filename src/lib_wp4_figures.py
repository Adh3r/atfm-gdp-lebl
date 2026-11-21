# =============================================================================
# src/lib_wp4_figures.py
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


def plot_d2d(df_modal: pd.DataFrame, out_dir: str) -> str:
    paths = (df_modal.groupby('ADEP')
             .agg(n=('ARCID', 'size'),
                  air=('d2d_air_nominal_min', 'mean'),
                  delay=('atfm_delay_min', 'mean'),
                  rail=('d2d_rail_min', 'mean'))
             .sort_values('air'))
    x = np.arange(len(paths))
    w = 0.38
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(x - w / 2, paths['air'], w, label='Air D2D, nominal', color='#4A90E2')
    ax.bar(x - w / 2, paths['delay'], w, bottom=paths['air'],
           label='Mean regulation delay', color='#E74C3C', hatch='//')
    ax.bar(x + w / 2, paths['rail'], w, label='HSR D2D', color='#2ECC71')
    ax.set_xticks(x)
    ax.set_xticklabels([f"{a}\n({n} flt)" for a, n in zip(paths.index, paths['n'])])
    ax.set_ylabel('Door-to-door time (min)')
    ax.set_title('Air vs high-speed rail, flights replaced in the regulation')
    ax.set_ylim(0, max((paths['air'] + paths['delay']).max(), paths['rail'].max()) * 1.3)
    ax.legend(loc='upper left', ncol=3, fontsize=9)
    fig.tight_layout()
    path = os.path.join(out_dir, 'fig1_d2d_by_route.svg')
    fig.savefig(path, format='svg')
    plt.close(fig)
    return path


def plot_co2_saving(kpis: dict, co2: dict, out_dir: str) -> str:
    pairs = [('GDP (RBS)', 'Intermodal GDP (RBS)'),
             ('GHP emissions', 'Intermodal GHP emissions'),
             ('GHP cost', 'Intermodal GHP cost')]
    direct = (co2['replaced_flights'] - co2['rail']) / 1000
    network = [(kpis[b]['delay_co2_kg'] - kpis[i]['delay_co2_kg']) / 1000 for b, i in pairs]
    x = np.arange(len(pairs))
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(x, [direct] * len(pairs), 0.55, label='Direct: replaced flights − rail', color='#27AE60')
    ax.bar(x, network, 0.55, bottom=[direct] * len(pairs),
           label='Network: less delay CO₂', color='#F39C12')
    for k, r in enumerate(network):
        ax.text(k, direct + r, f'{direct + r:.1f} t', ha='center', va='bottom')
    ax.set_xticks(x)
    ax.set_xticklabels([b for b, _ in pairs])
    ax.set_ylabel('CO₂ saved (t)')
    ax.set_title('CO₂ saved by replacing flights with high-speed rail')
    ax.set_ylim(0, (direct + max(network)) * 1.3)
    ax.legend(loc='upper center', ncol=2, fontsize=9)
    fig.tight_layout()
    path = os.path.join(out_dir, 'fig2_co2_savings.svg')
    fig.savefig(path, format='svg')
    plt.close(fig)
    return path


def plot_queues(timeline_base, timeline_reduced, h_noreg_base, h_noreg_reduced, n_replaced, out_dir) -> str:
    fig, ax = plt.subplots(figsize=(9, 4.8))
    hours = timeline_base['minute'] / 60
    ax.plot(hours, timeline_base['queue_size'], color='#E74C3C', linewidth=2, label='Baseline demand')
    ax.plot(timeline_reduced['minute'] / 60, timeline_reduced['queue_size'], color='#3498DB', linewidth=2,
            label=f'Without the {n_replaced} rail-replaced flights')
    for h, c in ((h_noreg_base, '#E74C3C'), (h_noreg_reduced, '#3498DB')):
        ax.axvline(h / 60, color=c, linestyle='--', linewidth=1)
    ax.annotate(f'HNoReg {h_noreg_reduced // 60:02d}:{h_noreg_reduced % 60:02d}\n'
                f'({h_noreg_base - h_noreg_reduced} min earlier)',
                xy=(h_noreg_reduced / 60, 1), xytext=(h_noreg_reduced / 60 + 0.6, timeline_base['queue_size'].max() * 0.45),
                arrowprops=dict(arrowstyle='->'), fontsize=10)
    ax.set_xlim(4, 22)
    ax.set_xlabel('Time (UTC hour)')
    ax.set_ylabel('Aircraft in queue (Newell)')
    ax.set_title('Arrival queue: baseline vs rail substitution')
    ax.legend(loc='upper right')
    fig.tight_layout()
    path = os.path.join(out_dir, 'fig3_queue_newell.svg')
    fig.savefig(path, format='svg')
    plt.close(fig)
    return path


def plot_wp4_figures(base_results: dict, wp4_results: dict, out_dir: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    reduced_gdp = wp4_results['reduced']['gdp_results']
    paths = []
    if len(wp4_results['df_modal']):
        paths.append(plot_d2d(wp4_results['df_modal'], out_dir))
    paths.append(plot_co2_saving(wp4_results['kpis'], wp4_results['co2'], out_dir))
    paths.append(plot_queues(base_results['timeline'], reduced_gdp['timeline'],
                               base_results['h_noreg'], reduced_gdp['h_noreg'],
                               len(wp4_results['df_modal']), out_dir))
    return paths
