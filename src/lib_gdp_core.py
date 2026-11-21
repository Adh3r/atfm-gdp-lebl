# =============================================================================
# src/lib_gdp_core.py
# WP2 - Ground Delay Program (GDP) engine.
#
# Takes the prepared flights of WP1 and runs the GDP step by step:
#   1. simulate_newell_curves()          -> when and how much the airport saturates
#   2. classify_flights()           -> flight status (controlled / exempt)
#   3. assign_slots_rbs()              -> arrival slot of every affected flight
#   4. compute_delays()                -> delay of each flight and where it waits
#   5. newell_minimum_delay() -> minimum delay imposed by the capacity
#   6. gdp_vs_do_nothing()       -> cost and CO2 of the GDP vs. do-nothing
#   7. run_gdp()            -> runs all of the above
#
# Figures live in lib_visualization.py, the Excel workbook in
# lib_excel_export.py and the parameters (AAR, PAAR, Hstart...) in config.py.
# =============================================================================

import os

import numpy as np
import pandas as pd

import config
from config import (
    CFG,
    FS_CONTROLLED,
    FS_EXEMPT_FLYING,
    FS_EXEMPT_INTERNATIONAL,
    FS_EXEMPT_RADIUS,
    FS_NOT_AFFECTED,
)
from cost_model import flight_costs, ground_co2_kg_per_min

# =============================================================================
# 1. NEWELL MODEL - WHEN AND HOW MUCH DOES THE AIRPORT SATURATE?
# =============================================================================


def simulate_newell_curves(
    df_flights: pd.DataFrame,
    params: dict,
) -> tuple[pd.DataFrame, int]:
    """
    Cumulative demand and capacity curves of the airport over the whole day
    (1440 minutes), following Newell's deterministic queueing model.

    Returns a timeline of 1440 rows (one per minute) with:
        demand_accum:   arrivals requested up to that minute (by ETA);
        capacity_accum: arrivals the airport has been able to accept;
        queue_size:     demand_accum - capacity_accum = aircraft waiting;
    and HNoReg, the first minute after Hend at which the queue has cleared
    (end of the regulation).

    Args:
        df_flights: flights with 'minutes_eta'.
        params:    GDP parameters H_START, H_END, SLOT_NOM, SLOT_RED.

    Returns:
        (timeline, h_noreg)
    """
    h_start = params['H_START']
    h_end = params['H_END']

    # Acceptance rates in aircraft per minute (inverse of the slot interval).
    rate_nominal = 1 / params['SLOT_NOM']  # AAR, nominal
    rate_reduced = 1 / params['SLOT_RED']  # PAAR, low-visibility procedures

    timeline = pd.DataFrame({'minute': range(1440)})

    # -------------------------------------------------------------------------
    # Cumulative demand. floor() bins an ETA with seconds (e.g. 06:30:45) into
    # its minute; without it, non-integer ETAs would not match the integer
    # timeline and would be dropped.
    # -------------------------------------------------------------------------
    eta_minute = np.floor(df_flights['minutes_eta']).astype(int)
    flights_per_minute = (
        eta_minute.value_counts().reindex(  # flights per ETA minute
            timeline['minute'], fill_value=0
        )  # minutes with no arrival -> 0
    )
    timeline['demand_accum'] = flights_per_minute.cumsum()

    # -------------------------------------------------------------------------
    # Cumulative capacity, minute by minute:
    #   before Hstart:       nominal operations, capacity follows demand;
    #   Hstart .. Hend:      reduced capacity (PAAR), the queue builds up;
    #   after Hend:          nominal capacity (AAR), the queue dissolves.
    # -------------------------------------------------------------------------
    cumulative_capacity = []
    capacity_now = 0.0

    for minute in timeline['minute']:
        demand_so_far = timeline.loc[minute, 'demand_accum']

        if minute < h_start:
            capacity_now = demand_so_far
        elif h_start <= minute <= h_end:
            capacity_now += rate_reduced
        else:
            capacity_now += rate_nominal

        # Cumulative arrivals can never exceed cumulative demand. The cap is
        # applied to the running counter itself: capacity left unused while
        # the queue is empty is lost, not banked for later.
        capacity_now = min(capacity_now, demand_so_far)
        cumulative_capacity.append(capacity_now)

    timeline['capacity_accum'] = cumulative_capacity
    timeline['queue_size'] = (timeline['demand_accum'] - timeline['capacity_accum']).clip(lower=0)

    # -------------------------------------------------------------------------
    # HNoReg: first minute after Hend with less than 0.5 aircraft in the queue
    # (0.5 rather than 0 absorbs the rounding of the fractional capacity). If
    # the queue never clears, 1440 (end of the day).
    # -------------------------------------------------------------------------
    cleared = timeline[(timeline['minute'] > h_end) & (timeline['queue_size'] < 0.5)]
    h_noreg = int(cleared['minute'].iloc[0]) if len(cleared) else 1440

    return timeline, h_noreg


# =============================================================================
# 2. FLIGHT STATUS
# =============================================================================


def classify_flights(
    df_flights: pd.DataFrame,
    h_start: int,
    radius_km: int = CFG.GDP_RADIUS_KM,
    h_file_offset: int = CFG.H_FILE_OFFSET,
    h_noreg: int | None = None,
    ready_margin: int = CFG.READY_MARGIN,
) -> pd.DataFrame:
    """
    Flight status as defined in the course (WP2, "determine flight status"):

        NotAffected          ETA before Hstart or at/after HNoReg: outside the
                             regulation, flies as scheduled. Only assigned when
                             h_noreg is given.
        ExemptInternational  origin outside the ECAC area.
        ExemptFlying         already flying when the GDP is defined: ETD earlier
                             than the file time Hfile (+ ready_margin, for
                             flights already boarded and ready to depart), or
                             ETD later than ETA, which in the traffic file
                             means the flight left the previous day.
        ExemptRadius         origin airport further than the radius of exemption.
        Controlled           everything else: receives ground delay (CTD/CTA).

    Exempt flights take air (holding) delay; controlled flights take ground delay.

    Priority when several exemptions apply: International, then Flying, then
    Radius (a long-haul flight already airborne is ExemptInternational).

    Hfile (file time) = h_start - h_file_offset: when the regulation is set and
    the slots are sent to the airlines. Project value: 150 min (03:30 UTC).

    Args:
        df_flights:     flights with minutes_eta, minutes_etd, is_ecac, distance_km.
        h_start:       Hstart (360 = 06:00 UTC).
        radius_km:     radius of exemption in km.
        h_file_offset: Hstart - Hfile, in minutes.
        h_noreg:       HNoReg; when given, flights outside [Hstart, HNoReg) are
                       NotAffected.
        ready_margin:  minutes after Hfile within which a flight counts as ready
                       to depart and is exempt (config.READY_MARGIN, 30 min as
                       the course suggests; the delivered project used 0).

    Returns:
        Copy of df_flights with is_departed, is_inside_radius, is_controlled and
        flight_status.
    """
    df = df_flights.copy()
    h_file = h_start - h_file_offset

    left_previous_day = df['minutes_etd'] > df['minutes_eta']
    df['is_departed'] = (df['minutes_etd'] < h_file + ready_margin) | left_previous_day
    df['is_inside_radius'] = df['distance_km'] <= radius_km
    if h_noreg is None:
        df['is_affected'] = True
    else:
        df['is_affected'] = (df['minutes_eta'] >= h_start) & (df['minutes_eta'] < h_noreg)

    df['is_controlled'] = df['is_affected'] & df['is_ecac'] & ~df['is_departed'] & df['is_inside_radius']

    # np.select evaluates the conditions in order: the first True one wins.
    conditions = [
        ~df['is_affected'],
        df['is_controlled'],
        ~df['is_ecac'],
        df['is_departed'],
        ~df['is_inside_radius'],
    ]
    labels = [
        FS_NOT_AFFECTED,
        FS_CONTROLLED,
        FS_EXEMPT_INTERNATIONAL,
        FS_EXEMPT_FLYING,
        FS_EXEMPT_RADIUS,
    ]
    df['flight_status'] = np.select(conditions, labels, default='UNKNOWN')

    return df


# =============================================================================
# 3. SLOT ALLOCATION - RATION-BY-SCHEDULE (RBS)
# =============================================================================


def assign_slots_rbs(
    df_affected: pd.DataFrame,
    df_slots: pd.DataFrame,
) -> pd.DataFrame:
    """
    Assign an arrival slot to every affected flight with Ration-By-Schedule.

    Ration-By-Schedule is the slot-allocation rule of FAA Ground Delay
    Programs under Collaborative Decision Making, as taught in the course:
    arrival slots are rationed in the order of the original schedule (ETA),
    which is considered fair. EUROCONTROL's Network Manager allocates
    regulation slots with CASA (Computer Assisted Slot Allocation) on a
    similar "first planned, first served" basis, issuing CTOTs.

    Two passes:
        1. Exempt flights (ExemptFlying, ExemptInternational, ExemptRadius)
           cannot be held at their origin: each takes the first free slot at
           or after its ETA, in ETA order.
        2. Controlled flights then take the first free slot at or after their
           ETA, in ETA order; a slot later than the ETA means ground delay.
    Serving the exempt flights first stops a controlled flight from taking the
    slot of an exempt flight arriving in the same minute.

    Args:
        df_affected: affected flights with 'flight_status' and 'minutes_eta'.
        df_slots:     slot table with 'slot_start_min', 'occupied', 'flight_id'
                      and 'airline' (modified in place: each assigned slot
                      records its flight and the airline that owns it).

    Returns:
        Copy of df_affected with 'assigned_slot' (NaN if no slot was left).
    """
    df = df_affected.copy()
    df['assigned_slot'] = np.nan
    if 'airline' not in df_slots.columns:
        df_slots['airline'] = None

    for controlled_pass in (False, True):
        if controlled_pass:
            selection = df['flight_status'] == FS_CONTROLLED
        else:
            selection = df['flight_status'] != FS_CONTROLLED

        for flight_idx, flight in df[selection].sort_values('minutes_eta').iterrows():
            free = df_slots[(df_slots['slot_start_min'] >= flight['minutes_eta']) & (~df_slots['occupied'])]
            if not free.empty:
                slot_idx = free.index[0]  # earliest free slot
                df_slots.at[slot_idx, 'occupied'] = True
                df_slots.at[slot_idx, 'flight_id'] = flight_idx
                df_slots.at[slot_idx, 'airline'] = flight['airline']
                df.at[flight_idx, 'assigned_slot'] = df_slots.at[slot_idx, 'slot_start_min']

    return df


# =============================================================================
# 4. DELAYS - HOW MUCH IS EACH FLIGHT DELAYED, AND WHERE DOES IT WAIT?
# =============================================================================


def compute_delays(df_res: pd.DataFrame) -> pd.DataFrame:
    """
    Delay of each flight and where it is absorbed (course notation):

        CTA          = assigned slot             (Controlled Time of Arrival)
        total_delay  = CTA - ETA
        air_delay    = total_delay for exempt flights (AirDelay, holding)
        ground_delay = total_delay for controlled flights (GroundDelay)
        CTD          = ETD + ground_delay        (Controlled Time of Departure)

    Exempt flights cannot be held at their origin, so any delay is airborne.
    Controlled flights wait at the origin gate, which is cheaper.
    air_delay + ground_delay = total_delay always holds.

    CTD/CTA are the FAA/course terms (CTD is also called EDCT). In the European
    ATFM system the equivalent of the CTD is the CTOT (Calculated Take-Off
    Time) issued by the Network Manager.

    Args:
        df_res: table with 'assigned_slot', 'minutes_eta', 'minutes_etd' and
                'flight_status'.

    Returns:
        Copy with CTA, CTD, total_delay, air_delay and ground_delay.
    """
    df = df_res.copy()

    df['CTA'] = df['assigned_slot']
    df['total_delay'] = (df['CTA'] - df['minutes_eta']).clip(lower=0)

    controlled_mask = df['flight_status'] == FS_CONTROLLED
    df['air_delay'] = np.where(~controlled_mask, df['total_delay'], 0)
    df['ground_delay'] = np.where(controlled_mask, df['total_delay'], 0)
    if 'minutes_etd' in df.columns:
        df['CTD'] = df['minutes_etd'] + df['ground_delay']

    return df


# =============================================================================
# 5. MINIMUM DELAY IMPOSED BY THE CAPACITY DROP
# =============================================================================


def newell_minimum_delay(timeline: pd.DataFrame) -> float:
    """
    Total delay imposed by the capacity drop, read from the Newell curves:
    the area between cumulative demand and cumulative capacity (sum over
    minutes of the queue length), in minutes. It depends only on the
    regulation, not on how slots are allocated (WP1 statement). It is a fluid
    approximation: with discrete slots the allocated total differs by a few
    percent (checked in the tests).

    Args:
        timeline: Newell timeline with 'demand_accum' and 'capacity_accum'.
    """
    queue_per_minute = (timeline['demand_accum'] - timeline['capacity_accum']).clip(lower=0)
    return queue_per_minute.sum()


# =============================================================================
# 6. ECONOMIC AND ENVIRONMENTAL KPIs
# =============================================================================


def unrecoverable_delay(df_res: pd.DataFrame, h_cancel: float = CFG.H_START) -> pd.Series:
    """
    Unrecoverable ground delay per flight if the GDP were cancelled at h_cancel
    (the project assumes cancellation at H_START).

    CTD (Controlled Time of Departure) = ETD + ground_delay.
        CASE 1  ETD >= h_cancel:        the flight had not reached its departure
                                        time yet -> nothing lost (0).
        CASE 2  CTD <= h_cancel:        it already took off after absorbing all of
                                        its ground delay -> all of it is lost.
        CASE 3  ETD < h_cancel < CTD:   it is still held at the gate; released at
                                        h_cancel, it has lost (h_cancel - ETD).

    Airborne (air) delay is not counted: it is not caused by holding the flight
    on the ground and is absorbed whether or not the GDP is cancelled.
    """
    etd = df_res['minutes_etd']
    ground = df_res['ground_delay']
    ctd = etd + ground

    lost = pd.Series(0.0, index=df_res.index)
    case2 = ctd <= h_cancel
    lost[case2] = ground[case2]
    case3 = (etd < h_cancel) & (ctd > h_cancel)
    lost[case3] = h_cancel - etd[case3]
    return lost


def gdp_vs_do_nothing(df_res: pd.DataFrame, h_start: float = CFG.H_START) -> dict:
    """
    Cost and CO2 of one GDP run against the do-nothing case (no regulation:
    every flight absorbs the same delay in an airborne holding). Used by the
    sensitivity heatmaps and the WP2 cost figure.

    COST: the same Cook & Tanner model as WP3-WP5 (cost_model.flight_costs):
        do-nothing: all the delay costed en route;
        GDP:        ground delay at the gate, air delay en route.
        The reactionary part depends only on the arrival delay, so it is the
        same in both cases: the saving comes from holding on the ground.

    CO2 (Montlaur et al., 2025 for flight emissions; APU at the gate for ground delay):
        airborne delay burns fuel at the flight's average rate:
            co2_air = flight_co2_kg * air_delay / block_time
        ground delay is spent at the origin gate with the APU running:
            co2_ground = APU fuel flow (seats, ICAO Doc 9889) * 3.16 * ground_delay

    UNRECOVERABLE DELAY: see unrecoverable_delay().
    """
    no_gdp = df_res.assign(_g=0.0, _a=df_res['total_delay'])
    cost_no_gdp = flight_costs(no_gdp, '_g', '_a')['delay_cost_eur'].sum()
    cost_gdp_value = flight_costs(df_res)['delay_cost_eur'].sum()

    block_time = df_res['block_time_min'].clip(lower=1)
    air_rate = df_res['flight_co2_kg'] / block_time  # kg CO2 / min airborne
    ground_rate = ground_co2_kg_per_min(df_res['size_seats_avg'])  # kg CO2 / min at the gate

    co2_air = (air_rate * df_res['air_delay']).sum()
    co2_ground = (ground_rate * df_res['ground_delay']).sum()
    co2_nominal_total = df_res['flight_co2_kg'].sum()

    co2_no_gdp = co2_nominal_total + (air_rate * df_res['total_delay']).sum()
    co2_with_gdp = co2_nominal_total + co2_air + co2_ground

    lost_total = unrecoverable_delay(df_res, h_start).sum()

    return {
        'cost_baseline': round(cost_no_gdp, 2),
        'cost_gdp': round(cost_gdp_value, 2),
        'cost_savings': round(cost_no_gdp - cost_gdp_value, 2),
        'co2_baseline': round(co2_no_gdp, 2),
        'co2_gdp': round(co2_with_gdp, 2),
        'co2_savings': round(co2_no_gdp - co2_with_gdp, 2),
        'air_delay_co2': round(co2_air, 2),
        'ground_delay_co2': round(co2_ground, 2),
        'unrecoverable_delay': round(lost_total, 2),
    }


# =============================================================================
# 7. ORCHESTRATOR
# =============================================================================


def run_gdp(
    df_flights: pd.DataFrame,
    params: dict,
    radius_km: int = CFG.GDP_RADIUS_KM,
    h_file_offset: int = CFG.H_FILE_OFFSET,
    run_compression: bool = True,
) -> dict:
    """
    Run the GDP: Newell curves and HNoReg, flight status, slot grid, RBS slot
    allocation, delays and (optionally) cancellations, substitution and compression.

    Args:
        df_flights:       prepared flights (WP1).
        params:          GDP parameters (H_START, H_END, AAR, PAAR, ...).
        radius_km:       radius of exemption.
        h_file_offset:   Hstart - Hfile, in minutes.
        run_compression: also run cancellations + compression (WP2). The sensitivity
                         analysis turns it off.

    Raises:
        RuntimeError: if an affected flight gets no slot (the queue does not
                      clear before midnight).

    Returns:
        dict with allocation (RBS result), slots, compressed_allocation,
        flights, timeline, h_noreg and params.
    """
    # 1. Newell curves and HNoReg
    timeline, h_noreg = simulate_newell_curves(df_flights, params)

    # 2. Flight status. Only arrivals between Hstart and HNoReg are affected.
    df_classified = classify_flights(
        df_flights,
        h_start=params['H_START'],
        radius_km=radius_km,
        h_file_offset=h_file_offset,
        h_noreg=h_noreg,
    )

    # 3. Slot grid. From Hstart, one slot every 60/PAAR min until Hend, then
    # every 60/AAR min. The course builds the slots from Hstart to HNoReg and
    # suggests adding extra slots after HNoReg for flights that may need them;
    # here the extra slots run to midnight, and RBS leaves the unneeded ones
    # empty.
    slot_times = []
    t_slot = params['H_START']
    while t_slot < min(h_noreg + 1000, 1440):
        slot_times.append(round(t_slot, 4))
        t_slot += params['SLOT_RED'] if t_slot < params['H_END'] else params['SLOT_NOM']

    df_slots = pd.DataFrame(
        {
            'slot_start_min': slot_times,
            'occupied': False,
            'flight_id': None,
            'airline': None,
        }
    )

    # 4. RBS allocation of the affected flights
    df_window = df_classified[df_classified['flight_status'] != FS_NOT_AFFECTED].copy()
    df_slots_rbs = df_slots.copy()
    df_rbs_alloc = assign_slots_rbs(df_window, df_slots_rbs)
    no_slot = df_rbs_alloc['assigned_slot'].isna()
    if no_slot.any():
        raise RuntimeError(
            f"{no_slot.sum()} flights got no slot before midnight: the queue does not "
            "clear within the simulated day (lower demand or raise capacity)."
        )
    df_rbs_alloc = compute_delays(df_rbs_alloc)

    # 5. Cancellations, substitution and compression (WP2)
    df_compressed, compression_summary = None, None
    if run_compression:
        from lib_compression import cancel_and_compress

        df_compressed, compression_summary = cancel_and_compress(df_rbs_alloc)
        df_compressed = compute_delays(df_compressed)

    return {
        'allocation': df_rbs_alloc,
        'slots': df_slots_rbs,
        'compressed_allocation': df_compressed,
        'compression_summary': compression_summary,
        'flights': df_flights,
        'timeline': timeline,
        'h_noreg': h_noreg,
        'params': params,
    }


# =============================================================================
# DEBUG MODE: python src/lib_gdp_core.py
# =============================================================================
if __name__ == "__main__":
    import lib_data_prep as prep

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p_debug = os.path.join(base, 'debug', 'DEBUG_02_flight_status.xlsx')
    params = CFG.to_params_dict()

    df_flights = prep.prepare_flights(config.traffic_file(), config.FLEET_TABLE)
    results = run_gdp(df_flights, params)
    df_result = results['allocation']

    os.makedirs(os.path.dirname(p_debug), exist_ok=True)
    cols_debug = ['ARCID', 'airline', 'ADEP', 'is_ecac', 'distance_km', 'is_departed', 'flight_status']
    df_result[cols_debug].to_excel(p_debug, index=False)
    print(f"Flight status written to {p_debug}")

    h_noreg = results['h_noreg']
    kpis = gdp_vs_do_nothing(df_result)
    print(
        f"HNoReg {h_noreg // 60:02d}:{h_noreg % 60:02d}, "
        f"unrecoverable delay {kpis['unrecoverable_delay']} min"
    )
