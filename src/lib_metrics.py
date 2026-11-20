import pandas as pd

from cost_model import (
    LOAD_FACTOR_EU, OTP_THRESHOLD_MIN, _PAX_BASE_TABLE,
    _interpolar_coste, _mapear_tipo_cook, co2_tierra_kg_por_min,
)
from lib_gdp_core import calcular_retraso_irrecuperable


def evaluar_escenario(df_res: pd.DataFrame, nombre_escenario: str, h_start_min: int) -> dict:
    """
    Motor Unificado de Métricas (Unified Metrics Engine).
    Calcula KPIs Operacionales, Ambientales (Delgado + FEGP), Económicos (Cook 2015) 
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
    costes_individuales = []
    for i, row in df.iterrows():
        delay_real = float(row.get('total_delay', 0) or 0)
        if delay_real <= 0:
            costes_individuales.append(0.0)
            continue

        tipo_icao = str(row.get('ATYP', row.get('f', ''))).upper().strip()
        recat     = str(row.get('recat', 'D')).upper()
        asientos  = max(int(row.get('size_seats_avg', 180) or 180), 1)

        tipo_cook = _mapear_tipo_cook(tipo_icao, recat, asientos)
        usar_hard = delay_real >= OTP_THRESHOLD_MIN
        coste_tabla = _interpolar_coste(tipo_cook, delay_real, usar_hard)

        pax_base   = _PAX_BASE_TABLE.get(tipo_cook, 130)
        pax_reales = max(int(asientos * LOAD_FACTOR_EU), 1)
        factor_pax = pax_reales / pax_base

        costes_individuales.append(coste_tabla * factor_pax)
    
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

    # Extracción Top 4 Aerolíneas
    top_airlines_data = {}
    if 'airline' in df.columns:  # Asegura que usas la columna correcta del indicativo (Opr o ARCID[:3])
        top_4 = df['airline'].value_counts().head(4).index
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
    }
    
    # Añadimos los datos de las aerolíneas dinámicamente si existen
    kpis.update(top_airlines_data)

    return kpis