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
    caught by prepare_flights() instead of silently becoming 00:00.
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


def load_airports(airports_path: str) -> pd.DataFrame:
    """Load the airport table (icao; name; lat_deg; lon_deg) indexed by ICAO."""
    return pd.read_csv(airports_path, sep=';').set_index('icao')


# =============================================================================
# MAIN FUNCTION: LOAD FLIGHTS
# =============================================================================


def prepare_flights(
    traffic_path: str,
    fleet_path: str,
    airports_path: str | None = None,
) -> pd.DataFrame:
    """
    Args:
        traffic_path:      daily flight list CSV (see config.traffic_file()).
        fleet_path:       fleet table CSV (fleet_cat_seat.csv).
        airports_path: airport coordinates CSV. Defaults to
                          config.AIRPORT_TABLE.

    Returns:
        Arrivals to LEBL sorted by ETA, with minutes_eta/etd/tt, is_ecac,
        airline, distance_km (great circle), flight_co2_kg,
        block_time_min and scheduled_turnaround_min (scheduled turnaround,
        NaN when the aircraft does not depart again from LEBL that day).

    Raises:
        ValueError: if a time cannot be parsed or an origin airport has no
                    coordinates.
    """
    print("   -> Reading traffic and computing great-circle distances...")
    if airports_path is None:
        airports_path = config.AIRPORT_TABLE

    # -------------------------------------------------------------------------
    # 1. Read the inputs
    # -------------------------------------------------------------------------
    df_flights = pd.read_csv(traffic_path, sep=';')
    df_fleet = pd.read_csv(fleet_path, sep=';')
    airports_df = load_airports(airports_path)

    # The fleet CSV carries an unnamed index column and hundreds of blank rows.
    df_fleet.columns = df_fleet.columns.str.strip()
    df_fleet = (
        df_fleet.rename(columns={'f': 'ATYP'})
        .dropna(subset=['ATYP'])[['ATYP', 'recat', 'size_seats_avg']]
        .drop_duplicates()
    )
    if df_fleet['ATYP'].duplicated().any():
        dup = sorted(df_fleet.loc[df_fleet['ATYP'].duplicated(), 'ATYP'])
        raise ValueError(f"Conflicting fleet entries for aircraft types: {dup}")

    # -------------------------------------------------------------------------
    # 2. Arrivals to LEBL only (LEBL->LEBL local circuits are dropped). The
    #    departures are kept aside to compute the turnarounds (step 8).
    # -------------------------------------------------------------------------
    departures_df = df_flights[(df_flights['ADEP'] == AIRPORT) & (df_flights['ADES'] != AIRPORT)][
        ['RM', 'ETD']
    ].copy()
    df_flights = df_flights[(df_flights['ADES'] == AIRPORT) & (df_flights['ADEP'] != AIRPORT)].copy()

    # -------------------------------------------------------------------------
    # 3. Fleet data: RECAT category and average seats per aircraft type
    # -------------------------------------------------------------------------
    df_flights = pd.merge(df_flights, df_fleet, on='ATYP', how='left')

    # -------------------------------------------------------------------------
    # 4. Times in minutes from midnight UTC
    # -------------------------------------------------------------------------
    for col, raw in (('minutes_eta', 'ETA'), ('minutes_etd', 'ETD'), ('minutes_tt', 'TT')):
        df_flights[col] = df_flights[raw].apply(parse_time_to_minutes)
        bad = df_flights[col].isna()
        if bad.any():
            ids = df_flights.loc[bad, 'ARCID'].tolist()
            raise ValueError(f"Unparseable {raw} for flights {ids}")

    # -------------------------------------------------------------------------
    # 5. ECAC origin and airline designator
    # -------------------------------------------------------------------------
    df_flights['is_ecac'] = df_flights['ADEP'].astype(str).str.startswith(ECAC_PREFIXES)
    is_airline = df_flights['ARCID'].str.match(AIRLINE_CALLSIGN)
    df_flights['airline'] = np.where(is_airline, df_flights['ARCID'].str[:3], GENERAL_AVIATION)

    # -------------------------------------------------------------------------
    # 6. Great-circle distance from the origin airport to LEBL
    #    Used for the GDP coverage radius, the rail-substitution filter and
    #    the CO2 model.
    # -------------------------------------------------------------------------
    missing_coords = sorted(set(df_flights['ADEP']) - set(airports_df.index))
    if missing_coords or AIRPORT not in airports_df.index:
        raise ValueError(
            f"Missing coordinates for {missing_coords or [AIRPORT]} in {airports_path}; "
            "regenerate it with tools/build_airport_table.py"
        )
    origin = airports_df.loc[df_flights['ADEP']]
    destination = airports_df.loc[AIRPORT]
    df_flights['distance_km'] = great_circle_km(
        origin['lat_deg'].to_numpy(),
        origin['lon_deg'].to_numpy(),
        destination['lat_deg'],
        destination['lon_deg'],
    )

    # Block time ETD -> ETA (an arrival after midnight adds 24 h).
    block = df_flights['minutes_eta'] - df_flights['minutes_etd']
    df_flights['block_time_min'] = np.where(block <= 0, block + 1440, block).clip(min=1)

    # -------------------------------------------------------------------------
    # 7. Baseline CO2 per flight (Montlaur, Trapote-Barreira & Delgado, 2025)
    #    CO2 [kg] = gCO2/ASK * distance * seats / 1000
    # -------------------------------------------------------------------------
    def _flight_co2(row) -> float:
        distance = row['distance_km']
        seats = row['size_seats_avg']
        if distance <= 0 or pd.isna(seats) or seats <= 0:
            return 0.0
        # force=True extrapolates outside the model's fitted seat/distance range
        co2_per_ask = compute_co2_ask(distance, int(seats), force=True)
        return round(co2_per_ask * distance * seats / 1000, 2)

    df_flights['flight_co2_kg'] = df_flights.apply(_flight_co2, axis=1)

    # -------------------------------------------------------------------------
    # 8. Scheduled turnaround (WP3: reactionary delay)
    # -------------------------------------------------------------------------
    df_flights['scheduled_turnaround_min'] = scheduled_turnaround(df_flights, departures_df)

    # The GDP engine expects chronological arrival order.
    return df_flights.sort_values('minutes_eta', kind='stable').reset_index(drop=True)


def scheduled_turnaround(arrivals: pd.DataFrame, departures_df: pd.DataFrame) -> pd.Series:
    """Scheduled ground time of each arrival before its aircraft departs again.

    For each arrival, the first departure from LEBL of the same registration
    (RM) at or after its ETA, provided the aircraft does not arrive again in
    between (otherwise a leg is missing from the file and the pair is not a
    turnaround). NaN when there is no such departure (night stop) or no RM.

    Args:
        arrivals: arrivals with RM and minutes_eta.
        departures_df:  departures from LEBL with RM and ETD (HH:MM[:SS]).
    """
    tat = pd.Series(np.nan, index=arrivals.index)
    if 'RM' not in arrivals.columns or departures_df.empty:
        return tat
    dep = departures_df.dropna(subset=['RM']).copy()
    dep['etd'] = dep['ETD'].apply(parse_time_to_minutes)
    dep = dep.dropna(subset=['etd'])
    departures_by_rm = {rm: np.sort(g['etd'].to_numpy()) for rm, g in dep.groupby('RM')}

    arrivals_rm = arrivals.dropna(subset=['RM']).sort_values('minutes_eta', kind='stable')
    next_arrival = arrivals_rm.groupby('RM')['minutes_eta'].shift(-1)
    for idx, row in arrivals_rm.iterrows():
        etds = departures_by_rm.get(row['RM'])
        if etds is None:
            continue
        k = np.searchsorted(etds, row['minutes_eta'], side='left')
        if k == len(etds):
            continue
        prox = next_arrival.loc[idx]
        if pd.notna(prox) and prox <= etds[k]:
            continue
        tat.loc[idx] = etds[k] - row['minutes_eta']
    return tat


# --- DEBUG MODE ---
if __name__ == "__main__":
    print("DEBUG: lib_data_prep.py")

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p_traffic = config.traffic_file()
    p_fleet = config.FLEET_TABLE
    p_debug_out = os.path.join(base, 'debug/DEBUG_01_prepared.xlsx')

    df_debug = prepare_flights(p_traffic, p_fleet)

    os.makedirs(os.path.dirname(p_debug_out), exist_ok=True)
    df_debug.to_excel(p_debug_out, index=False)

    print(f"Prepared {len(df_debug)} arrivals -> {p_debug_out}")
