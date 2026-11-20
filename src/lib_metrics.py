# =============================================================================
# src/lib_metrics.py
# Unified metrics engine: the same KPIs, computed the same way, for every
# scenario of WP2-WP5 (GDP, GHP, with and without rail substitution).
# =============================================================================

import pandas as pd

from cost_model import OTP_THRESHOLD_MIN, co2_tierra_kg_por_min, costes_por_vuelo
from lib_gdp_core import calcular_retraso_irrecuperable


def _rsd(serie: pd.Series) -> float:
    """Relative standard deviation (%) of a series; 0 when its mean is 0."""
    media = serie.mean()
    return float(serie.std() / media * 100) if media > 0 else 0.0


def evaluar_escenario(df_res: pd.DataFrame, nombre_escenario: str, h_start_min: int) -> dict:
    """
    KPIs of one scenario: operational, environmental (Montlaur et al. 2025 for
    airborne burn, FEGP for ground holding), economic (Cook & Tanner 2015, with
    the reactionary delay of each aircraft's next rotation) and fairness.

    Args:
        df_res:           affected flights with total/air/ground delay.
        nombre_escenario: scenario label.
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
    n_delayed = int((df['total_delay'] > 0).sum())
    otp_pct = (df['total_delay'] < OTP_THRESHOLD_MIN).mean() * 100
    retraso_irrecuperable = calcular_retraso_irrecuperable(df, h_start_min).sum()

    # -------------------------------------------------------------------------
    # 2. Environmental KPIs (CO2 of the delay)
    # -------------------------------------------------------------------------
    duracion_vuelo = df['duracion_vuelo_min'].clip(lower=1)
    if 'co2_kg_vuelo' in df.columns:
        co2_aire_delay = (df['co2_kg_vuelo'] * (df['air_delay'] / duracion_vuelo)).sum()
    else:
        co2_aire_delay = 0.0
    co2_rate_tierra = df.get('recat', pd.Series('D', index=df.index)).apply(co2_tierra_kg_por_min)
    co2_tierra_delay = (co2_rate_tierra * df['ground_delay']).sum()

    # -------------------------------------------------------------------------
    # 3. Economic KPIs: the same cost function as the GHP cost objective
    #    (WP3 Task 5: GDP and GHP costed with the same coefficients). Ground
    #    delay at the gate, air delay en route, plus the reactionary cost of
    #    the delay passed on to the next rotation.
    # -------------------------------------------------------------------------
    costes = costes_por_vuelo(df)
    coste_total = costes['coste_retraso_eur'].sum()
    coste_medio = coste_total / n_delayed if n_delayed > 0 else 0.0

    # -------------------------------------------------------------------------
    # 4. Fairness. Airline level (WP3 Task 4): the 4 airlines with most
    #    affected flights; for each, mean delay and RSD of its flights. The
    #    fairness index is the RSD of the four mean delays (0 % = every airline
    #    gets the same mean delay).
    # -------------------------------------------------------------------------
    top_airlines_data = {}
    rsd_aerolineas = 0.0
    if 'airline' in df.columns:
        top_4 = df['airline'].value_counts().head(4).index
        medias = df[df['airline'].isin(top_4)].groupby('airline')['total_delay'].mean()
        if len(medias) > 1 and medias.mean() > 0:
            rsd_aerolineas = medias.std(ddof=0) / medias.mean() * 100
        for idx, al in enumerate(top_4, start=1):
            df_al = df[df['airline'] == al]
            top_airlines_data[f'Top{idx}_Aerolinea'] = al
            top_airlines_data[f'Top{idx}_Retraso_Medio_min'] = round(df_al['total_delay'].mean(), 1)
            top_airlines_data[f'Top{idx}_RSD_%'] = round(_rsd(df_al['total_delay'].fillna(0)), 1)

    kpis = {
        'Escenario':              nombre_escenario,
        'Vuelos_Totales':         n_flights,
        'Vuelos_Retrasados':      n_delayed,
        'OTP_%_Menor_15min':      round(otp_pct, 2),
        'Retraso_Total_min':      round(df['total_delay'].sum(), 2),
        'Retraso_Max_min':        round(df['total_delay'].max(), 2),
        'Retraso_Medio_min':      round(df['total_delay'].mean(), 2),
        'Retraso_Aire_min':       round(df['air_delay'].sum(), 2),
        'Retraso_Aire_Max_min':   round(df['air_delay'].max(), 2),
        'Retraso_Tierra_min':     round(df['ground_delay'].sum(), 2),
        'Retraso_Tierra_Max_min': round(df['ground_delay'].max(), 2),
        'Retraso_Irrecup_min':    round(retraso_irrecuperable, 2),
        'Retraso_Propagado_min':  round(costes['retraso_propagado_min'].sum(), 2),
        'Vuelos_Propagan':        int((costes['retraso_propagado_min'] > 0).sum()),
        'CO2_Extra_Aire_kg':      round(co2_aire_delay, 2),
        'CO2_Extra_Tierra_kg':    round(co2_tierra_delay, 2),
        'CO2_Total_Retraso_kg':   round(co2_aire_delay + co2_tierra_delay, 2),
        'Coste_Cook_Total_EUR':   round(coste_total, 2),
        'Coste_Primario_EUR':     round(costes['coste_primario_eur'].sum(), 2),
        'Coste_Reactivo_EUR':     round(costes['coste_reactivo_eur'].sum(), 2),
        'Coste_Cook_Medio_EUR':   round(coste_medio, 2),
        'Coste_Cook_Max_EUR':     round(costes['coste_retraso_eur'].max(), 2),
        'RSD_Aire_%':             round(_rsd(df['air_delay']), 2),
        'RSD_Tierra_%':           round(_rsd(df['ground_delay']), 2),
        'RSD_Total_%':            round(_rsd(df['total_delay']), 2),
        'RSD_Aerolineas_%':       round(rsd_aerolineas, 2),
    }
    kpis.update(top_airlines_data)
    return kpis


# Rows of the summary tables shared by WP3, WP4 and WP5.
FILAS_RESUMEN = [
    ('Affected flights',                 'Vuelos_Totales'),
    ('Delayed flights',                  'Vuelos_Retrasados'),
    ('Total delay (min)',                'Retraso_Total_min'),
    ('Air delay (min)',                  'Retraso_Aire_min'),
    ('Ground delay (min)',               'Retraso_Tierra_min'),
    ('Mean delay (min/flight)',          'Retraso_Medio_min'),
    ('Max delay (min)',                  'Retraso_Max_min'),
    ('Max air delay (min)',              'Retraso_Aire_Max_min'),
    ('OTP, delay < 15 min (%)',          'OTP_%_Menor_15min'),
    ('Unrecoverable delay (min)',        'Retraso_Irrecup_min'),
    ('Reactionary delay passed to next rotations (min)', 'Retraso_Propagado_min'),
    ('Flights whose delay propagates',   'Vuelos_Propagan'),
    ('Delay CO2 (kg)',                   'CO2_Total_Retraso_kg'),
    ('Cost of delay (EUR)',              'Coste_Cook_Total_EUR'),
    ('  of which reactionary (EUR)',     'Coste_Reactivo_EUR'),
    ('Airline fairness: RSD of top-4 mean delays (%)', 'RSD_Aerolineas_%'),
]


def resumen_escenarios(kpis_por_escenario: dict, extras: dict | None = None) -> pd.DataFrame:
    """
    Summary table: one column per scenario, one row per KPI (FILAS_RESUMEN),
    plus optional extra rows {row label: {scenario: value}}.
    """
    tabla = {'Metric': [r[0] for r in FILAS_RESUMEN]}
    for nombre, kpis in kpis_por_escenario.items():
        tabla[nombre] = [round(kpis[clave], 2) for _, clave in FILAS_RESUMEN]
    df = pd.DataFrame(tabla)
    for etiqueta, valores in (extras or {}).items():
        fila = {'Metric': etiqueta, **{n: valores.get(n) for n in kpis_por_escenario}}
        df = pd.concat([df, pd.DataFrame([fila])], ignore_index=True)
    return df
