# =============================================================================
# src/lib_comparison.py
# WP5: final comparison of every method (GDP, GHP emissions, GHP cost, each
# with and without rail substitution), with every KPI related to a SESAR
# Key Performance Area (KPA), as the WP5 statement asks.
# =============================================================================

import os

import pandas as pd

# KPI row of the summary tables -> SESAR KPA (PJ19.04 performance framework,
# ICAO KPAs refined by SESAR). Security is not assessed by this model.
KPI_TO_KPA = {
    'Affected flights':                                 'Capacity',
    'HNoReg (min UTC)':                                 'Capacity',
    'Delayed flights':                                  'Operational efficiency',
    'Total delay (min)':                                'Operational efficiency',
    'Mean delay (min/flight)':                          'Operational efficiency',
    'Air delay (min)':                                  'Operational efficiency',
    'Ground delay (min)':                               'Operational efficiency',
    'OTP, delay < 15 min (%)':                          'Predictability',
    'Max delay (min)':                                  'Predictability',
    'Max air delay (min)':                              'Safety (airborne holding exposure)',
    'Mean air delay per exempt flight (min)':           'Operational efficiency',
    'Mean ground delay per controlled flight (min)':    'Operational efficiency',
    'Max ground delay (min)':                           'Predictability',
    'RSD of air delay, exempt flights (%)':             'Predictability',
    'RSD of ground delay, controlled flights (%)':      'Predictability',
    'Air delay CO2 (kg)':                               'Environment',
    'Ground delay CO2 (kg)':                            'Environment',
    'Ground delay CO2, APU never on / on 50 % (kg)':    'Environment',
    'Unrecoverable delay (min)':                        'Flexibility',
    'Reactionary delay passed to next rotations (min)': 'Predictability',
    'Flights whose delay propagates':                   'Predictability',
    '  of which reactionary (EUR)':                     'Cost effectiveness',
    'Delay CO2 (kg)':                                   'Environment',
    'System CO2: flights + delay + rail (kg)':          'Environment',
    'Rail CO2 (kg)':                                    'Environment',
    'Nominal CO2 of all flights flown (kg)':            'Environment',
    'Cost of delay (EUR)':                              'Cost effectiveness',
    'Airline fairness: RSD of top-4 mean delays (%)':   'Access and equity',
    'Flights replaced by rail':                         'Capacity',
}


def final_table(df_wp4: pd.DataFrame) -> pd.DataFrame:
    """WP4 six-scenario table with a KPA column, sorted by KPA."""
    df = df_wp4.copy()
    df.insert(0, 'KPA', df['Metric'].map(KPI_TO_KPA).fillna('Other'))
    order = ['Capacity', 'Operational efficiency', 'Predictability', 'Flexibility',
             'Safety (airborne holding exposure)', 'Environment', 'Cost effectiveness',
             'Access and equity', 'Other']
    df['_o'] = df['KPA'].map({k: i for i, k in enumerate(order)})
    return df.sort_values(['_o'], kind='stable').drop(columns='_o').reset_index(drop=True)


def plot_final(kpis: dict, out_dir: str) -> str:
    """Cost of delay and delay CO2 of the six methods (WP5 minimum content)."""
    import matplotlib.pyplot as plt
    import numpy as np

    os.makedirs(out_dir, exist_ok=True)
    methods = ['GDP (RBS)', 'GHP emissions', 'GHP cost']
    base = [kpis[m] for m in methods]
    inter = [kpis[f'Intermodal {m}'] for m in methods]
    x = np.arange(len(methods))
    w = 0.38
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    for ax, key, scale, unit in ((axes[0], 'delay_cost_total_eur', 1e6, 'Cost of delay (M€)'),
                                      (axes[1], 'delay_co2_kg', 1e3, 'Delay CO₂ (t)')):
        b = [k[key] / scale for k in base]
        i = [k[key] / scale for k in inter]
        ax.bar(x - w / 2, b, w, label='Full demand', color='#4A6FA5')
        ax.bar(x + w / 2, i, w, label='With rail substitution', color='#2ECC71')
        for xs, vals in ((x - w / 2, b), (x + w / 2, i)):
            for xx, v in zip(xs, vals):
                ax.text(xx, v, f'{v:.2f}' if scale == 1e6 else f'{v:.1f}', ha='center', va='bottom', fontsize=9)
        ax.set_xticks(x)
        ax.set_xticklabels(methods)
        ax.set_ylabel(unit)
        ax.set_ylim(0, max(b + i) * 1.15)
        ax.grid(axis='y', alpha=0.3, linestyle='--')
    axes[0].set_title('Cost of delay')
    axes[1].set_title('CO₂ emitted because of the delay')
    axes[0].legend(loc='upper right')
    fig.suptitle('LEBL, 10 Aug 2025, LVP 06:00-13:00 UTC: comparison of ATFM methods')
    fig.tight_layout()
    path = os.path.join(out_dir, 'wp5_cost_and_co2_by_method.svg')
    fig.savefig(path, format='svg')
    plt.close(fig)
    return path
