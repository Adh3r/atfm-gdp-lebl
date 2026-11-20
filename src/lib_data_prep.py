# =============================================================================
# src/lib_data_prep.py
# WP1 - Data preparation.
# Maps the raw traffic CSV to the clean table the GDP engine needs.
#
# Steps:
#   - Load the flight list, the fleet table and the airport coordinates
#   - Keep arrivals to LEBL
#   - Join aircraft metadata (RECAT category, seats)
#   - Convert times (HH:MM[:SS] -> minutes from midnight UTC)
#   - Tag ECAC origins and extract the airline designator
#   - Great-circle distance from the origin airport, and baseline CO2
#   - Scheduled turnaround of each arrival: ground time until the next
#     departure of the same aircraft (registration mark, RM) from LEBL
# =============================================================================

import os
import re

import numpy as np
import pandas as pd

import config
from config import ECAC_PREFIXES
from emissions_fuel_model import compute_co2_ask

AIRPORT = 'LEBL'
EARTH_RADIUS_KM = 6371.0

# ICAO airline flights use a 3-letter operator designator followed by the
# flight number (VLG1234, RYR404A). Anything else, such as a registration used
# as callsign (N818CA), is general aviation.
AIRLINE_CALLSIGN = re.compile(r'^[A-Z]{3}\d')
GENERAL_AVIATION = 'GA'

# =============================================================================
# TIME CONVERSION
# =============================================================================

def parse_time_to_minutes(time_str) -> float:
    """
    Parse 'HH:MM:SS', 'HH:MM' or a plain number of minutes into minutes
    from midnight.

    Returns NaN when the value is missing or cannot be parsed, so bad rows are
    caught by preparar_vuelos() instead of silently becoming 00:00.
    """
    if pd.isna(time_str):
        return np.nan
    try:
        parts = [int(p) for p in str(time_str).strip().split(':')]
    except ValueError:
        return np.nan

    if len(parts) == 3:
        return parts[0] * 60 + parts[1] + parts[2] / 60
    if len(parts) == 2:
        return float(parts[0] * 60 + parts[1])
    if len(parts) == 1:
        return float(parts[0])
    return np.nan

# =============================================================================
# GEOMETRY
# =============================================================================

def great_circle_km(lat1, lon1, lat2, lon2):
    """Haversine great-circle distance in km (works on scalars or arrays)."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def load_airports(path_aeropuertos: str) -> pd.DataFrame:
    """Load the airport table (icao; name; lat_deg; lon_deg) indexed by ICAO."""
    return pd.read_csv(path_aeropuertos, sep=';').set_index('icao')

# =============================================================================
# MAIN FUNCTION: LOAD FLIGHTS
# =============================================================================

def preparar_vuelos(
    path_vuelos: str,
    path_flota: str,
    path_aeropuertos: str | None = None,
) -> pd.DataFrame:
    """
    Args:
        path_vuelos:      daily flight list CSV (see config.traffic_file()).
        path_flota:       fleet table CSV (fleet_cat_seat.csv).
        path_aeropuertos: airport coordinates CSV. Defaults to
                          config.AIRPORT_TABLE.

    Returns:
        Arrivals to LEBL sorted by ETA, with minutes_eta/etd/tt, is_ecac,
        airline, distancia_km (great circle), co2_kg_vuelo,
        duracion_vuelo_min and tat_programado_min (scheduled turnaround,
        NaN when the aircraft does not depart again from LEBL that day).

    Raises:
        ValueError: if a time cannot be parsed or an origin airport has no
                    coordinates.
    """
    print("   -> Reading traffic and computing great-circle distances...")
    if path_aeropuertos is None:
        path_aeropuertos = config.AIRPORT_TABLE

    # -------------------------------------------------------------------------
    # 1. Read the inputs
    # -------------------------------------------------------------------------
    df_vuelos = pd.read_csv(path_vuelos, sep=';')
    df_flota = pd.read_csv(path_flota, sep=';')
    aeropuertos = load_airports(path_aeropuertos)

    # The fleet CSV carries an unnamed index column and hundreds of blank rows.
    df_flota.columns = df_flota.columns.str.strip()
    df_flota = (
        df_flota.rename(columns={'f': 'ATYP'})
        .dropna(subset=['ATYP'])[['ATYP', 'recat', 'size_seats_avg']]
        .drop_duplicates()
    )
    if df_flota['ATYP'].duplicated().any():
        dup = sorted(df_flota.loc[df_flota['ATYP'].duplicated(), 'ATYP'])
        raise ValueError(f"Conflicting fleet entries for aircraft types: {dup}")

    # -------------------------------------------------------------------------
    # 2. Arrivals to LEBL only (LEBL->LEBL local circuits are dropped). The
    #    departures are kept aside to compute the turnarounds (step 8).
    # -------------------------------------------------------------------------
    salidas = df_vuelos[
        (df_vuelos['ADEP'] == AIRPORT) & (df_vuelos['ADES'] != AIRPORT)
    ][['RM', 'ETD']].copy()
    df_vuelos = df_vuelos[
        (df_vuelos['ADES'] == AIRPORT) & (df_vuelos['ADEP'] != AIRPORT)
    ].copy()

    # -------------------------------------------------------------------------
    # 3. Fleet data: RECAT category and average seats per aircraft type
    # -------------------------------------------------------------------------
    df_vuelos = pd.merge(df_vuelos, df_flota, on='ATYP', how='left')

    # -------------------------------------------------------------------------
    # 4. Times in minutes from midnight UTC
    # -------------------------------------------------------------------------
    for col, raw in (('minutes_eta', 'ETA'), ('minutes_etd', 'ETD'), ('minutes_tt', 'TT')):
        df_vuelos[col] = df_vuelos[raw].apply(parse_time_to_minutes)
        bad = df_vuelos[col].isna()
        if bad.any():
            ids = df_vuelos.loc[bad, 'ARCID'].tolist()
            raise ValueError(f"Unparseable {raw} for flights {ids}")

    # -------------------------------------------------------------------------
    # 5. ECAC origin and airline designator
    # -------------------------------------------------------------------------
    df_vuelos['is_ecac'] = df_vuelos['ADEP'].astype(str).str.startswith(ECAC_PREFIXES)
    es_aerolinea = df_vuelos['ARCID'].str.match(AIRLINE_CALLSIGN)
    df_vuelos['airline'] = np.where(es_aerolinea, df_vuelos['ARCID'].str[:3], GENERAL_AVIATION)

    # -------------------------------------------------------------------------
    # 6. Great-circle distance from the origin airport to LEBL
    #    Used for the GDP coverage radius, the rail-substitution filter and
    #    the CO2 model.
    # -------------------------------------------------------------------------
    sin_coordenadas = sorted(set(df_vuelos['ADEP']) - set(aeropuertos.index))
    if sin_coordenadas or AIRPORT not in aeropuertos.index:
        raise ValueError(
            f"Missing coordinates for {sin_coordenadas or [AIRPORT]} in {path_aeropuertos}; "
            "regenerate it with tools/build_airport_table.py"
        )
    origen = aeropuertos.loc[df_vuelos['ADEP']]
    destino = aeropuertos.loc[AIRPORT]
    df_vuelos['distancia_km'] = great_circle_km(
        origen['lat_deg'].to_numpy(), origen['lon_deg'].to_numpy(),
        destino['lat_deg'], destino['lon_deg'],
    )

    # Block time ETD -> ETA (an arrival after midnight adds 24 h).
    duracion = df_vuelos['minutes_eta'] - df_vuelos['minutes_etd']
    df_vuelos['duracion_vuelo_min'] = np.where(duracion <= 0, duracion + 1440, duracion).clip(min=1)

    # -------------------------------------------------------------------------
    # 7. Baseline CO2 per flight (Montlaur, Trapote-Barreira & Delgado, 2025)
    #    CO2 [kg] = gCO2/ASK * distance * seats / 1000
    # -------------------------------------------------------------------------
    def _calcular_co2_vuelo(fila) -> float:
        distancia = fila['distancia_km']
        asientos = fila['size_seats_avg']
        if distancia <= 0 or pd.isna(asientos) or asientos <= 0:
            return 0.0
        # force=True extrapolates outside the model's fitted seat/distance range
        co2_por_ask = compute_co2_ask(distancia, int(asientos), force=True)
        return round(co2_por_ask * distancia * asientos / 1000, 2)

    df_vuelos['co2_kg_vuelo'] = df_vuelos.apply(_calcular_co2_vuelo, axis=1)

    # -------------------------------------------------------------------------
    # 8. Scheduled turnaround (WP3: reactionary delay)
    # -------------------------------------------------------------------------
    df_vuelos['tat_programado_min'] = turnaround_programado(df_vuelos, salidas)

    # The GDP engine expects chronological arrival order.
    return df_vuelos.sort_values('minutes_eta', kind='stable').reset_index(drop=True)


def turnaround_programado(llegadas: pd.DataFrame, salidas: pd.DataFrame) -> pd.Series:
    """Scheduled ground time of each arrival before its aircraft departs again.

    For each arrival, the first departure from LEBL of the same registration
    (RM) at or after its ETA, provided the aircraft does not arrive again in
    between (otherwise a leg is missing from the file and the pair is not a
    turnaround). NaN when there is no such departure (night stop) or no RM.

    Args:
        llegadas: arrivals with RM and minutes_eta.
        salidas:  departures from LEBL with RM and ETD (HH:MM[:SS]).
    """
    tat = pd.Series(np.nan, index=llegadas.index)
    if 'RM' not in llegadas.columns or salidas.empty:
        return tat
    dep = salidas.dropna(subset=['RM']).copy()
    dep['etd'] = dep['ETD'].apply(parse_time_to_minutes)
    dep = dep.dropna(subset=['etd'])
    salidas_por_rm = {rm: np.sort(g['etd'].to_numpy()) for rm, g in dep.groupby('RM')}

    llegadas_rm = llegadas.dropna(subset=['RM']).sort_values('minutes_eta', kind='stable')
    siguiente_llegada = llegadas_rm.groupby('RM')['minutes_eta'].shift(-1)
    for idx, fila in llegadas_rm.iterrows():
        etds = salidas_por_rm.get(fila['RM'])
        if etds is None:
            continue
        k = np.searchsorted(etds, fila['minutes_eta'], side='left')
        if k == len(etds):
            continue
        prox = siguiente_llegada.loc[idx]
        if pd.notna(prox) and prox <= etds[k]:
            continue
        tat.loc[idx] = etds[k] - fila['minutes_eta']
    return tat

# --- DEBUG MODE ---
if __name__ == "__main__":
    print("DEBUG: lib_data_prep.py")

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p_vuelos = config.traffic_file()
    p_flota = config.FLEET_TABLE
    p_debug_out = os.path.join(base, 'debug/DEBUG_01_preprocesado.xlsx')

    df_debug = preparar_vuelos(p_vuelos, p_flota)

    os.makedirs(os.path.dirname(p_debug_out), exist_ok=True)
    df_debug.to_excel(p_debug_out, index=False)

    print(f"Prepared {len(df_debug)} arrivals -> {p_debug_out}")
