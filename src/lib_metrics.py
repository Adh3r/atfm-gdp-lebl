import pandas as pd

from cost_model import OTP_THRESHOLD_MIN, co2_tierra_kg_por_min, coste_retraso_eur
from lib_gdp_core import calcular_retraso_irrecuperable


def evaluar_escenario(df_res: pd.DataFrame, nombre_escenario: str, h_start_min: int) -> dict:
    """
    Motor Unificado de Métricas (Unified Metrics Engine).
    Calcula KPIs Operacionales, Ambientales (Montlaur et al. 2025 + FEGP), Económicos (Cook & Tanner 2015) 
    y de Equidad (RSD) de forma estandarizada para cualquier escenario.
    """
    if df_res.empty:
        return {}

    df = df_res.copy()
    # Asegurar columnas
    for col in ('total_delay', 'air_delay', 'ground_delay'):
        if col not in df.columns:
            df[col] = 0.0

    # ---------------------------------------------------------
    # 1. KPIs OPERACIONALES BÁSICOS
    # ---------------------------------------------------------
    n_flights = len(df)
    n_delayed = (df['total_delay'] > 0).sum()
    
    total_delay  = df['total_delay'].sum()
    air_delay    = df['air_delay'].sum()
    ground_delay = df['ground_delay'].sum()

    # Promedios y Máximos
    avg_total_delay  = df['total_delay'].mean() if n_flights > 0 else 0
    max_total_delay  = df['total_delay'].max() if n_flights > 0 else 0
    max_air_delay    = df['air_delay'].max() if n_flights > 0 else 0
    max_ground_delay = df['ground_delay'].max() if n_flights > 0 else 0

    # OTP (On-Time Performance): % Vuelos con retraso < 15 min
    vuelos_otp = (df['total_delay'] < OTP_THRESHOLD_MIN).sum()
    otp_pct = (vuelos_otp / n_flights) * 100 if n_flights > 0 else 0

    # Retraso Irrecuperable (Cancelación en H_START)
    retraso_irrecuperable = calcular_retraso_irrecuperable(df, h_start_min).sum()

    # ---------------------------------------------------------
    # 2. KPIs AMBIENTALES (CO2)
    # ---------------------------------------------------------
    duracion_vuelo = df['duracion_vuelo_min'].clip(lower=1)
    if 'co2_kg_vuelo' in df.columns:
        co2_aire_delay = (df['co2_kg_vuelo'] * (df['air_delay'] / duracion_vuelo)).sum()
    else:
        co2_aire_delay = 0.0

    co2_rate_tierra = df.get('recat', pd.Series('D', index=df.index)).apply(co2_tierra_kg_por_min)
    co2_tierra_delay = (co2_rate_tierra * df['ground_delay']).sum()

    # ---------------------------------------------------------
    # 3. KPIs ECONÓMICOS (Cook & Tanner 2015)
    # ---------------------------------------------------------
    # Same cost function as the GHP cost objective (WP3 Task 5: the GDP is
    # costed with the same coefficients). Ground delay is costed at the gate,
    # air delay en route (Cook & Tanner tactical tables).
    costes_individuales = []
    for _, row in df.iterrows():
        aire = float(row.get('air_delay', 0) or 0)
        tierra = float(row.get('ground_delay', 0) or 0)
        coste = 0.0
        if tierra > 0:
            coste += coste_retraso_eur(row.get('ATYP', ''), row.get('recat', 'D'),
                                       row.get('size_seats_avg', 180), tierra, 'at_gate')
        if aire > 0:
            coste += coste_retraso_eur(row.get('ATYP', ''), row.get('recat', 'D'),
                                       row.get('size_seats_avg', 180), aire, 'en_route')
        costes_individuales.append(coste)

    df['coste_retraso_eur'] = costes_individuales
    coste_total = sum(costes_individuales)
    coste_maximo = max(costes_individuales) if costes_individuales else 0.0
    coste_medio = coste_total / n_delayed if n_delayed > 0 else 0.0

    # ---------------------------------------------------------
    # 4. KPA EQUIDAD Y DISTRIBUCIÓN (RSD Global y Top Aerolíneas)
    # ---------------------------------------------------------
    # RSD Global (Air, Ground, Total)
    rsd_air = (df['air_delay'].std() / df['air_delay'].mean() * 100) if df['air_delay'].mean() > 0 else 0
    rsd_ground = (df['ground_delay'].std() / df['ground_delay'].mean() * 100) if df['ground_delay'].mean() > 0 else 0
    rsd_total = (df['total_delay'].std() / df['total_delay'].mean() * 100) if df['total_delay'].mean() > 0 else 0

    # Airline level (WP3 Task 4): the 4 airlines with most affected flights.
    # For each: mean delay and RSD of its flights' delays. Across them, the
    # RSD of the four mean delays is the fairness index used in the report
    # (0 % = every airline gets the same mean delay).
    top_airlines_data = {}
    rsd_aerolineas = 0.0
    if 'airline' in df.columns:
        top_4 = df['airline'].value_counts().head(4).index
        medias = df[df['airline'].isin(top_4)].groupby('airline')['total_delay'].mean()
        if len(medias) > 1 and medias.mean() > 0:
            rsd_aerolineas = medias.std(ddof=0) / medias.mean() * 100
        for idx, al in enumerate(top_4, start=1):
            df_al = df[df['airline'] == al]
            avg_al = df_al['total_delay'].mean()
            std_al = df_al['total_delay'].std()
            rsd_al = (std_al / avg_al * 100) if (avg_al > 0 and pd.notna(std_al)) else 0
            
            top_airlines_data[f'Top{idx}_Aerolinea'] = al
            top_airlines_data[f'Top{idx}_Retraso_Medio_min'] = round(avg_al, 1)
            top_airlines_data[f'Top{idx}_RSD_%'] = round(rsd_al, 1)

    # ---------------------------------------------------------
    # DICCIONARIO UNIFICADO DE SALIDA
    # ---------------------------------------------------------
    kpis = {
        'Escenario':             nombre_escenario,
        'Vuelos_Totales':        n_flights,
        'Vuelos_Retrasados':     int(n_delayed),
        'OTP_%_Menor_15min':     round(otp_pct, 2),
        'Retraso_Total_min':     round(total_delay, 2),
        'Retraso_Max_min':       round(max_total_delay, 2),
        'Retraso_Medio_min':     round(avg_total_delay, 2),
        'Retraso_Aire_min':      round(air_delay, 2),
        'Retraso_Aire_Max_min':  round(max_air_delay, 2),
        'Retraso_Tierra_min':    round(ground_delay, 2),
        'Retraso_Tierra_Max_min':round(max_ground_delay, 2),
        'Retraso_Irrecup_min':   round(retraso_irrecuperable, 2),
        'CO2_Extra_Aire_kg':     round(co2_aire_delay, 2),
        'CO2_Extra_Tierra_kg':   round(co2_tierra_delay, 2),
        'CO2_Total_Retraso_kg':  round(co2_aire_delay + co2_tierra_delay, 2),
        'Coste_Cook_Total_EUR':  round(coste_total, 2),
        'Coste_Cook_Medio_EUR':  round(coste_medio, 2),
        'Coste_Cook_Max_EUR':    round(coste_maximo, 2),
        'RSD_Aire_%':            round(rsd_air, 2),
        'RSD_Tierra_%':          round(rsd_ground, 2),
        'RSD_Total_%':           round(rsd_total, 2),
        'RSD_Aerolineas_%':      round(rsd_aerolineas, 2),
    }
    
    # Añadimos los datos de las aerolíneas dinámicamente si existen
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
    ('Delay CO2 (kg)',                   'CO2_Total_Retraso_kg'),
    ('Cost of delay (EUR)',              'Coste_Cook_Total_EUR'),
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
