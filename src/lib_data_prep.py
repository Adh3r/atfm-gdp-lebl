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
# =============================================================================

import os
import re

import numpy as np
import pandas as pd

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
        path_vuelos:      daily flight list CSV (e.g. LEBL_10AUG2025.csv).
        path_flota:       fleet table CSV (fleet_cat_seat.csv).
        path_aeropuertos: airport coordinates CSV. Defaults to airports.csv
                          next to the flight list.

    Returns:
        Arrivals to LEBL sorted by ETA, with minutes_eta/etd/tt, is_ecac,
        airline, distancia_km (great circle), co2_kg_vuelo and
        duracion_vuelo_min.

    Raises:
        ValueError: if a time cannot be parsed or an origin airport has no
                    coordinates.
    """
    print("   -> Leyendo datos y calculando distancias ortodrómicas...")
    if path_aeropuertos is None:
        path_aeropuertos = os.path.join(os.path.dirname(path_vuelos), 'airports.csv')

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
    # 2. Arrivals to LEBL only (LEBL->LEBL local circuits are dropped)
    # -------------------------------------------------------------------------
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

    # The GDP engine expects chronological arrival order.
    return df_vuelos.sort_values('minutes_eta', kind='stable').reset_index(drop=True)

# --- DEBUG MODE ---
if __name__ == "__main__":
    print("🛠️  DEBUG: Testing lib_data_prep.py...")

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p_vuelos = os.path.join(base, 'data/raw/LEBL_10AUG2025.csv')
    p_flota = os.path.join(base, 'data/raw/fleet_cat_seat.csv')
    p_debug_out = os.path.join(base, 'debug/DEBUG_01_preprocesado.xlsx')

    df_debug = preparar_vuelos(p_vuelos, p_flota)

    os.makedirs(os.path.dirname(p_debug_out), exist_ok=True)
    df_debug.to_excel(p_debug_out, index=False)

    print(f"✅ Success! Enriched {len(df_debug)} flights.")
    print(f"📍 Output: {p_debug_out}")
