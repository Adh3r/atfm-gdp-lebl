# =============================================================================
# src/lib_intermodal.py
# WP4: Intermodality — replace short flights with high-speed rail (HSR)
#
# Following the WP4 statement:
#   1. Within the regulation interval (affected flights, not 24 h), identify
#      the routes with a direct rail option of reasonable duration.
#   2. Compare door-to-door (D2D) time and CO2 of air and rail for those
#      flights, first without delay and then with their regulation delay.
#   3. Remove those flights from the demand and apply GDP and GHP (both
#      objectives) again to the reduced set, with the same hypotheses.
#
# Rail times: direct HSR services from Barcelona (project values, see the
# report). Rail CO2: EcoPassenger per-passenger footprints (UIC/ifeu).
# D2D access/egress: ALG (2024), "Modal competition and substitution: HSR vs
# air transport" - 90 + 60 min for air, 40 + 20 min for rail.
# =============================================================================

import os

import pandas as pd

import lib_gdp_core as gdp
from cost_model import LOAD_FACTOR_EU
from lib_ghp_solver import run_ghp
from lib_metrics import evaluate_scenario, summary_table

# --- PARAMETERS ---
MAX_RAIL_DISTANCE_KM = 500  # great-circle distance limit for substitution
HSR_SPEED_KMH = 200  # fallback rail speed (route not in the table)
AIRPORT_ACCESS_MIN = 90  # to the airport + procedures
AIRPORT_EGRESS_MIN = 60  # from the airport to the destination
STATION_ACCESS_MIN = 40
STATION_EGRESS_MIN = 20
CRUISE_SPEED_KT = 440  # air in-vehicle time estimate
KT_TO_KMH = 1.852
LOAD_FACTOR = LOAD_FACTOR_EU  # IATA, Europe, August 2025
DEFAULT_SEATS = 180

# Direct HSR services to Barcelona (travel time in hours).
HSR_ROUTES = {
    'LEMD': {'city': 'Madrid', 'rail_time_h': 3.1},
    'LEVC': {'city': 'Valencia', 'rail_time_h': 3.0},
    'LEZG': {'city': 'Zaragoza', 'rail_time_h': 1.5},
    'LFBO': {'city': 'Toulouse', 'rail_time_h': 4.0},
    'LEAL': {'city': 'Alicante', 'rail_time_h': 5.2},
    'LFML': {'city': 'Marseille', 'rail_time_h': 4.8},
    'LFLL': {'city': 'Lyon', 'rail_time_h': 5.0},
}

# Rail CO2 per passenger, kg (EcoPassenger).
RAIL_CO2_KG_PER_PAX = {
    'LEMD': 19.0,
    'LEVC': 10.7,
    'LEAL': 15.5,
    'LEZG': 8.3,
    'LFML': 7.3,
    'LFBO': 6.4,
    'LFLL': 7.9,
}


def _passengers(row) -> int:
    seats = row.get('size_seats_avg', DEFAULT_SEATS)
    seats = DEFAULT_SEATS if pd.isna(seats) else seats
    return int(seats * LOAD_FACTOR)


def flag_rail_replaceable(df_flights: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Flag the arrivals with a direct HSR alternative within the distance limit."""
    df = df_flights.copy()
    df['rail_replaceable'] = (
        (df['distance_km'] <= MAX_RAIL_DISTANCE_KM)
        & df['ADEP'].isin(HSR_ROUTES.keys())
        & (df['ADES'] == 'LEBL')
    )
    if verbose:
        n = int(df['rail_replaceable'].sum())
        pct = n / len(df) * 100 if len(df) else 0
        print(f"\n   Intermodal: {n} rail-replaceable flights ({pct:.1f}% of {len(df)})")
    return df


def door_to_door_min(distance_km: float, origin_icao: str, by_air: bool = True) -> float:
    """Door-to-door time in minutes, without delay."""
    if by_air:
        flight_time_h = distance_km / (CRUISE_SPEED_KT * KT_TO_KMH)
        return AIRPORT_ACCESS_MIN + flight_time_h * 60 + AIRPORT_EGRESS_MIN
    rail_time_h = HSR_ROUTES.get(origin_icao, {}).get('rail_time_h', distance_km / HSR_SPEED_KMH)
    return STATION_ACCESS_MIN + rail_time_h * 60 + STATION_EGRESS_MIN


def rail_co2_kg(distance_km: float, origin_icao: str, n_passengers: int) -> float:
    """Rail CO2 (kg) for n passengers; generic per-km factor when the route is not tabulated."""
    kg_per_pax = RAIL_CO2_KG_PER_PAX.get(origin_icao)
    if kg_per_pax is not None:
        return kg_per_pax * n_passengers
    g_per_pax_km = 15.0 if origin_icao.startswith('LE') else 20.0
    return distance_km * n_passengers * g_per_pax_km / 1000


def modal_comparison(df_replaced: pd.DataFrame) -> pd.DataFrame:
    """Air vs rail D2D time and CO2 for each replaced flight (air with its GDP delay)."""
    rows = []
    for _, flight in df_replaced.iterrows():
        distance, origin = flight['distance_km'], flight['ADEP']
        n_pax = _passengers(flight)
        delay = flight.get('total_delay', 0)
        t_air = door_to_door_min(distance, origin, by_air=True)
        t_rail = door_to_door_min(distance, origin, by_air=False)
        co2_air_kg = flight.get('flight_co2_kg', 0)
        co2_rail = rail_co2_kg(distance, origin, n_pax)
        rows.append(
            {
                'ARCID': flight.get('ARCID', 'N/A'),
                'ADEP': origin,
                'distance_km': round(distance, 1),
                'passengers': n_pax,
                'atfm_delay_min': round(delay, 1),
                'd2d_air_nominal_min': round(t_air, 1),
                'd2d_air_delayed_min': round(t_air + delay, 1),
                'd2d_rail_min': round(t_rail, 1),
                'rail_time_saving_min': round(t_air + delay - t_rail, 1),
                'co2_air_kg': round(co2_air_kg, 1),
                'co2_rail_kg': round(co2_rail, 1),
                'net_co2_saving_kg': round(co2_air_kg - co2_rail, 1),
            }
        )
    return pd.DataFrame(rows)


def simulate_without(df_flights: pd.DataFrame, removed_idx, params: dict) -> dict:
    """Re-run GDP and GHP (both objectives) without the replaced flights."""
    df_reduced = df_flights.drop(index=removed_idx)
    res_gdp = gdp.run_gdp(df_reduced, params, run_compression=False)
    res_ghp = run_ghp(res_gdp['allocation'], list(res_gdp['slots']['slot_start_min']), params)
    return {'gdp_results': res_gdp, 'ghp_results': res_ghp, 'df_reduced': df_reduced}


def run_intermodal_analysis(
    df_flights: pd.DataFrame,
    base_results: dict,
    base_ghp_results: dict,
    params: dict,
    base_dir: str,
) -> dict:
    """
    WP4 pipeline. The system CO2 boundary is every arrival of the day, so the
    baseline and the intermodal scenarios are directly comparable:

        system CO2 = nominal CO2 of all flights flown + delay CO2 + rail CO2

    Returns a dict with the modal comparison, the summary table, the scenario
    tables and the re-simulation results.
    """
    print("\n" + "=" * 80)
    print("[WP4] Intermodality: replacing short flights with high-speed rail")
    print("=" * 80)
    h_start = params['H_START']

    # 1. Rail-replaceable flights among the flights affected by the regulation.
    base_affected = base_results['allocation']
    flagged = flag_rail_replaceable(base_affected, verbose=True)
    replaced = flagged[flagged['rail_replaceable']]
    df_modal = modal_comparison(replaced)

    # 2. Re-simulation without them.
    reduced_run = simulate_without(df_flights, replaced.index, params)

    # 3. KPIs of the six scenarios (base and reduced, GDP and both GHP).
    base_scenarios = {
        'GDP (RBS)': base_affected,
        'GHP emissions': base_ghp_results['GHP_Emissions'],
        'GHP cost': base_ghp_results['GHP_Cost'],
    }
    reduced_scenarios = {
        'Intermodal GDP (RBS)': reduced_run['gdp_results']['allocation'],
        'Intermodal GHP emissions': reduced_run['ghp_results']['GHP_Emissions'],
        'Intermodal GHP cost': reduced_run['ghp_results']['GHP_Cost'],
    }
    kpis = {n: evaluate_scenario(d, n, h_start) for n, d in {**base_scenarios, **reduced_scenarios}.items()}

    co2_all_flights = df_flights['flight_co2_kg'].sum()
    co2_replaced_flights = replaced['flight_co2_kg'].sum()
    co2_rail_total = df_modal['co2_rail_kg'].sum() if len(df_modal) else 0.0
    h_noreg = {
        **{n: base_results['h_noreg'] for n in base_scenarios},
        **{n: reduced_run['gdp_results']['h_noreg'] for n in reduced_scenarios},
    }
    co2_nominal = {
        **{n: co2_all_flights for n in base_scenarios},
        **{n: co2_all_flights - co2_replaced_flights for n in reduced_scenarios},
    }
    co2_rail = {**{n: 0.0 for n in base_scenarios}, **{n: co2_rail_total for n in reduced_scenarios}}
    co2_system = {n: co2_nominal[n] + kpis[n]['delay_co2_kg'] + co2_rail[n] for n in kpis}

    df_summary = summary_table(
        kpis,
        extras={
            'HNoReg (min UTC)': h_noreg,
            'Flights replaced by rail': {
                **{n: 0 for n in base_scenarios},
                **{n: len(replaced) for n in reduced_scenarios},
            },
            'Nominal CO2 of all flights flown (kg)': {n: round(v, 1) for n, v in co2_nominal.items()},
            'Rail CO2 (kg)': {n: round(v, 1) for n, v in co2_rail.items()},
            'System CO2: flights + delay + rail (kg)': {n: round(v, 1) for n, v in co2_system.items()},
        },
    )

    print(f"   Replaced flights (within the regulation): {len(replaced)}")
    print(f"   HNoReg: {base_results['h_noreg']} -> {reduced_run['gdp_results']['h_noreg']} min UTC")
    print(
        f"   Direct CO2 saving of the substitution: "
        f"{co2_replaced_flights - co2_rail_total:,.0f} kg "
        f"(flights {co2_replaced_flights:,.0f} - rail {co2_rail_total:,.0f})"
    )
    print(df_summary.to_string(index=False))

    os.makedirs(os.path.join(base_dir, 'data/processed'), exist_ok=True)
    df_modal.to_csv(os.path.join(base_dir, 'data/processed/wp4_modal_comparison.csv'), index=False)
    df_summary.to_csv(os.path.join(base_dir, 'data/processed/wp4_summary.csv'), index=False)

    return {
        'df_modal': df_modal,
        'summary': df_summary,
        'reduced': reduced_run,
        'reduced_scenarios': reduced_scenarios,
        'kpis': kpis,
        'co2': {'replaced_flights': co2_replaced_flights, 'rail': co2_rail_total},
    }
