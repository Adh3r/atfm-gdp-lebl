# =============================================================================
# src/lib_metrics.py
# Unified metrics engine: the same KPIs, computed the same way, for every
# scenario of WP2-WP5 (GDP, GHP, with and without rail substitution).
# =============================================================================

import pandas as pd

from config import FS_CONTROLLED
from cost_model import OTP_THRESHOLD_MIN, flight_costs, ground_co2_kg_per_min
from lib_gdp_core import unrecoverable_delay


def _rsd(series: pd.Series) -> float:
    """Relative standard deviation (%) of a series; 0 when its mean is 0."""
    mean_value = series.mean()
    return float(series.std() / mean_value * 100) if mean_value > 0 else 0.0


def evaluate_scenario(df_res: pd.DataFrame, scenario_name: str, h_start_min: int) -> dict:
    """
    KPIs of one scenario: operational, environmental (Montlaur et al. 2025 for
    airborne burn, APU fuel at the gate for ground delay), economic (Cook & Tanner 2015, with
    the reactionary delay of each aircraft's next rotation) and fairness.

    Args:
        df_res:           affected flights with total/air/ground delay.
        scenario_name: scenario label.
        h_start_min:      Hstart, for the unrecoverable delay.
    """
    if df_res.empty:
        return {}

    df = df_res.copy()
    for col in ('total_delay', 'air_delay', 'ground_delay'):
        if col not in df.columns:
            df[col] = 0.0

    # -------------------------------------------------------------------------
    # 1. Operational KPIs
    # -------------------------------------------------------------------------
    n_flights = len(df)
    # Ground delay is taken by controlled flights and air delay by exempt
    # ones, so their means and RSDs are computed over those flights (WP2:
    # "mean air/ground delay per flight, relative air/ground delay standard
    # deviation"); averaging in the zeros of the other group would distort them.
    if 'flight_status' in df.columns:
        controlled_mask = df['flight_status'] == FS_CONTROLLED
    else:
        controlled_mask = df['ground_delay'] > 0
    ground_ctrl = df.loc[controlled_mask, 'ground_delay']
    air_exempt = df.loc[~controlled_mask, 'air_delay']
    n_delayed = int((df['total_delay'] > 0).sum())
    otp_pct = (df['total_delay'] < OTP_THRESHOLD_MIN).mean() * 100
    lost_total = unrecoverable_delay(df, h_start_min).sum()

    # -------------------------------------------------------------------------
    # 2. Environmental KPIs (CO2 of the delay)
    # -------------------------------------------------------------------------
    block_time = df['block_time_min'].clip(lower=1)
    if 'flight_co2_kg' in df.columns:
        air_delay_co2 = (df['flight_co2_kg'] * (df['air_delay'] / block_time)).sum()
    else:
        air_delay_co2 = 0.0
    ground_co2_rate = ground_co2_kg_per_min(df.get('size_seats_avg', pd.Series(float('nan'), index=df.index)))
    ground_delay_co2 = (ground_co2_rate * df['ground_delay']).sum()

    # -------------------------------------------------------------------------
    # 3. Economic KPIs: the same cost function as the GHP cost objective
    #    (WP3 Task 5: GDP and GHP costed with the same coefficients). Ground
    #    delay at the gate, air delay en route, plus the reactionary cost of
    #    the delay passed on to the next rotation.
    # -------------------------------------------------------------------------
    costs = flight_costs(df)
    cost_total = costs['delay_cost_eur'].sum()
    cost_mean = cost_total / n_delayed if n_delayed > 0 else 0.0

    # -------------------------------------------------------------------------
    # 4. Fairness. Airline level (WP3 Task 4): the 4 airlines with most
    #    affected flights; for each, mean delay and RSD of its flights. The
    #    fairness index is the RSD of the four mean delays (0 % = every airline
    #    gets the same mean delay).
    # -------------------------------------------------------------------------
    top_airlines = {}
    airline_rsd = 0.0
    if 'airline' in df.columns:
        top_4 = df['airline'].value_counts().head(4).index
        means = df[df['airline'].isin(top_4)].groupby('airline')['total_delay'].mean()
        if len(means) > 1 and means.mean() > 0:
            airline_rsd = means.std(ddof=0) / means.mean() * 100
        for idx, al in enumerate(top_4, start=1):
            df_al = df[df['airline'] == al]
            top_airlines[f'top{idx}_airline'] = al
            top_airlines[f'top{idx}_mean_delay_min'] = round(df_al['total_delay'].mean(), 1)
            top_airlines[f'top{idx}_rsd_pct'] = round(_rsd(df_al['total_delay'].fillna(0)), 1)

    kpis = {
        'scenario': scenario_name,
        'flights': n_flights,
        'delayed_flights': n_delayed,
        'otp_pct': round(otp_pct, 2),
        'total_delay_min': round(df['total_delay'].sum(), 2),
        'max_delay_min': round(df['total_delay'].max(), 2),
        'mean_delay_min': round(df['total_delay'].mean(), 2),
        'air_delay_min': round(df['air_delay'].sum(), 2),
        'max_air_delay_min': round(df['air_delay'].max(), 2),
        'mean_air_delay_min': round(air_exempt.mean(), 2) if len(air_exempt) else 0.0,
        'mean_ground_delay_min': round(ground_ctrl.mean(), 2) if len(ground_ctrl) else 0.0,
        'ground_delay_min': round(df['ground_delay'].sum(), 2),
        'max_ground_delay_min': round(df['ground_delay'].max(), 2),
        'unrecoverable_delay_min': round(lost_total, 2),
        'reactionary_delay_min': round(costs['propagated_delay_min'].sum(), 2),
        'propagating_flights': int((costs['propagated_delay_min'] > 0).sum()),
        'air_delay_co2_kg': round(air_delay_co2, 2),
        'ground_delay_co2_kg': round(ground_delay_co2, 2),
        'delay_co2_kg': round(air_delay_co2 + ground_delay_co2, 2),
        'delay_cost_total_eur': round(cost_total, 2),
        'primary_cost_total_eur': round(costs['primary_cost_eur'].sum(), 2),
        'reactionary_cost_total_eur': round(costs['reactionary_cost_eur'].sum(), 2),
        'delay_cost_mean_eur': round(cost_mean, 2),
        'delay_cost_max_eur': round(costs['delay_cost_eur'].max(), 2),
        'rsd_air_pct': round(_rsd(air_exempt), 2),
        'rsd_ground_pct': round(_rsd(ground_ctrl), 2),
        'rsd_total_pct': round(_rsd(df['total_delay']), 2),
        'airline_fairness_rsd_pct': round(airline_rsd, 2),
    }
    kpis.update(top_airlines)
    return kpis


# Rows of the summary tables shared by WP3, WP4 and WP5.
SUMMARY_ROWS = [
    ('Affected flights', 'flights'),
    ('Delayed flights', 'delayed_flights'),
    ('Total delay (min)', 'total_delay_min'),
    ('Air delay (min)', 'air_delay_min'),
    ('Ground delay (min)', 'ground_delay_min'),
    ('Mean delay (min/flight)', 'mean_delay_min'),
    ('Mean air delay per exempt flight (min)', 'mean_air_delay_min'),
    ('Mean ground delay per controlled flight (min)', 'mean_ground_delay_min'),
    ('Max delay (min)', 'max_delay_min'),
    ('Max air delay (min)', 'max_air_delay_min'),
    ('Max ground delay (min)', 'max_ground_delay_min'),
    ('RSD of air delay, exempt flights (%)', 'rsd_air_pct'),
    ('RSD of ground delay, controlled flights (%)', 'rsd_ground_pct'),
    ('OTP, delay < 15 min (%)', 'otp_pct'),
    ('Unrecoverable delay (min)', 'unrecoverable_delay_min'),
    ('Reactionary delay passed to next rotations (min)', 'reactionary_delay_min'),
    ('Flights whose delay propagates', 'propagating_flights'),
    ('Air delay CO2 (kg)', 'air_delay_co2_kg'),
    ('Ground delay CO2 (kg)', 'ground_delay_co2_kg'),
    ('Delay CO2 (kg)', 'delay_co2_kg'),
    ('Cost of delay (EUR)', 'delay_cost_total_eur'),
    ('  of which reactionary (EUR)', 'reactionary_cost_total_eur'),
    ('Airline fairness: RSD of top-4 mean delays (%)', 'airline_fairness_rsd_pct'),
]


def summary_table(kpis_by_scenario: dict, extras: dict | None = None) -> pd.DataFrame:
    """
    Summary table: one column per scenario, one row per KPI (SUMMARY_ROWS),
    plus optional extra rows {row label: {scenario: value}}.
    """
    table = {'Metric': [r[0] for r in SUMMARY_ROWS]}
    for name, kpis in kpis_by_scenario.items():
        table[name] = [round(kpis[key], 2) for _, key in SUMMARY_ROWS]
    df = pd.DataFrame(table)
    for label, values_list in (extras or {}).items():
        row = {'Metric': label, **{n: values_list.get(n) for n in kpis_by_scenario}}
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    return df
