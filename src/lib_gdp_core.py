# =============================================================================
# src/lib_gdp_core.py
# WP2 - Ground Delay Program (GDP) engine.
#
# Takes the prepared flights of WP1 and runs the GDP step by step:
#   1. simular_curvas_newell()          -> when and how much the airport saturates
#   2. etiquetar_vuelos_gdp()           -> flight status (controlled / exempt)
#   3. asignar_slots_rbs()              -> arrival slot of every affected flight
#   4. calcular_delays()                -> delay of each flight and where it waits
#   5. calcular_retraso_minimo_newell() -> minimum delay imposed by the capacity
#   6. calcular_kpis_economicos()       -> cost and CO2 of the GDP vs. do-nothing
#   7. ejecutar_nucleo_gdp()            -> runs all of the above
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
from cost_model import co2_tierra_kg_por_min, costes_por_vuelo

# =============================================================================
# 1. NEWELL MODEL - WHEN AND HOW MUCH DOES THE AIRPORT SATURATE?
# =============================================================================

def simular_curvas_newell(
    df_vuelos: pd.DataFrame,
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
        df_vuelos: flights with 'minutes_eta'.
        params:    GDP parameters H_START, H_END, SLOT_NOM, SLOT_RED.

    Returns:
        (timeline, h_noreg)
    """
    h_start = params['H_START']
    h_end   = params['H_END']

    # Acceptance rates in aircraft per minute (inverse of the slot interval).
    tasa_nominal  = 1 / params['SLOT_NOM']  # AAR, nominal
    tasa_reducida = 1 / params['SLOT_RED']  # PAAR, low-visibility procedures

    timeline = pd.DataFrame({'minuto': range(1440)})

    # -------------------------------------------------------------------------
    # Cumulative demand. floor() bins an ETA with seconds (e.g. 06:30:45) into
    # its minute; without it, non-integer ETAs would not match the integer
    # timeline and would be dropped.
    # -------------------------------------------------------------------------
    minuto_eta = np.floor(df_vuelos['minutes_eta']).astype(int)
    vuelos_por_minuto = (
        minuto_eta.value_counts()                    # flights per ETA minute
        .reindex(timeline['minuto'], fill_value=0)   # minutes with no arrival -> 0
    )
    timeline['demand_accum'] = vuelos_por_minuto.cumsum()

    # -------------------------------------------------------------------------
    # Cumulative capacity, minute by minute:
    #   before Hstart:       nominal operations, capacity follows demand;
    #   Hstart .. Hend:      reduced capacity (PAAR), the queue builds up;
    #   after Hend:          nominal capacity (AAR), the queue dissolves.
    # -------------------------------------------------------------------------
    capacidad_acumulada = []
    capacidad_actual    = 0.0

    for minuto in timeline['minuto']:
        demanda_hasta_ahora = timeline.loc[minuto, 'demand_accum']

        if minuto < h_start:
            capacidad_actual = demanda_hasta_ahora
        elif h_start <= minuto <= h_end:
            capacidad_actual += tasa_reducida
        else:
            capacidad_actual += tasa_nominal

        # Cumulative arrivals can never exceed cumulative demand. The cap is
        # applied to the running counter itself: capacity left unused while
        # the queue is empty is lost, not banked for later.
        capacidad_actual = min(capacidad_actual, demanda_hasta_ahora)
        capacidad_acumulada.append(capacidad_actual)

    timeline['capacity_accum'] = capacidad_acumulada
    timeline['queue_size'] = (
        timeline['demand_accum'] - timeline['capacity_accum']
    ).clip(lower=0)

    # -------------------------------------------------------------------------
    # HNoReg: first minute after Hend with less than 0.5 aircraft in the queue
    # (0.5 rather than 0 absorbs the rounding of the fractional capacity). If
    # the queue never clears, 1440 (end of the day).
    # -------------------------------------------------------------------------
    despejado = timeline[(timeline['minuto'] > h_end) & (timeline['queue_size'] < 0.5)]
    h_noreg = int(despejado['minuto'].iloc[0]) if len(despejado) else 1440

    return timeline, h_noreg

# =============================================================================
# 2. FLIGHT STATUS
# =============================================================================

def etiquetar_vuelos_gdp(
    df_vuelos: pd.DataFrame,
    h_start: int,
    radius_km: int  = CFG.GDP_RADIUS_KM,
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
        df_vuelos:     flights with minutes_eta, minutes_etd, is_ecac, distancia_km.
        h_start:       Hstart (360 = 06:00 UTC).
        radius_km:     radius of exemption in km.
        h_file_offset: Hstart - Hfile, in minutes.
        h_noreg:       HNoReg; when given, flights outside [Hstart, HNoReg) are
                       NotAffected.
        ready_margin:  minutes after Hfile within which a flight counts as ready
                       to depart and is exempt (config.READY_MARGIN, 30 min as
                       the course suggests; the delivered project used 0).

    Returns:
        Copy of df_vuelos with is_departed, is_inside_radius, is_controlled and
        flight_status.
    """
    df = df_vuelos.copy()
    h_file = h_start - h_file_offset

    salio_dia_anterior = df['minutes_etd'] > df['minutes_eta']
    df['is_departed'] = (df['minutes_etd'] < h_file + ready_margin) | salio_dia_anterior
    df['is_inside_radius'] = df['distancia_km'] <= radius_km
    if h_noreg is None:
        df['is_affected'] = True
    else:
        df['is_affected'] = (df['minutes_eta'] >= h_start) & (df['minutes_eta'] < h_noreg)

    df['is_controlled'] = (
        df['is_affected']
        & df['is_ecac']
        & ~df['is_departed']
        & df['is_inside_radius']
    )

    # np.select evaluates the conditions in order: the first True one wins.
    condiciones = [
        ~df['is_affected'],
        df['is_controlled'],
        ~df['is_ecac'],
        df['is_departed'],
        ~df['is_inside_radius'],
    ]
    etiquetas = [
        FS_NOT_AFFECTED,
        FS_CONTROLLED,
        FS_EXEMPT_INTERNATIONAL,
        FS_EXEMPT_FLYING,
        FS_EXEMPT_RADIUS,
    ]
    df['flight_status'] = np.select(condiciones, etiquetas, default='UNKNOWN')

    return df

# =============================================================================
# 3. SLOT ALLOCATION - RATION-BY-SCHEDULE (RBS)
# =============================================================================

def asignar_slots_rbs(
    df_regulados: pd.DataFrame,
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
        df_regulados: affected flights with 'flight_status' and 'minutes_eta'.
        df_slots:     slots with 'slot_start_min' and 'occupied' (modified in
                      place: assigned slots are marked occupied).

    Returns:
        Copy of df_regulados with 'assigned_slot' (NaN if no slot was left).
    """
    df = df_regulados.copy()
    df['assigned_slot'] = np.nan

    for procesar_controlados in (False, True):
        if procesar_controlados:
            seleccion = df['flight_status'] == FS_CONTROLLED
        else:
            seleccion = df['flight_status'] != FS_CONTROLLED

        for indice_vuelo, datos_vuelo in df[seleccion].sort_values('minutes_eta').iterrows():
            libres = df_slots[
                (df_slots['slot_start_min'] >= datos_vuelo['minutes_eta'])
                & (~df_slots['occupied'])
            ]
            if not libres.empty:
                indice_slot = libres.index[0]          # earliest free slot
                df_slots.at[indice_slot, 'occupied']  = True
                df_slots.at[indice_slot, 'flight_id'] = indice_vuelo
                df.at[indice_vuelo, 'assigned_slot'] = df_slots.at[indice_slot, 'slot_start_min']

    return df

# =============================================================================
# 4. DELAYS - HOW MUCH IS EACH FLIGHT DELAYED, AND WHERE DOES IT WAIT?
# =============================================================================

def calcular_delays(df_res: pd.DataFrame) -> pd.DataFrame:
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

    controlado = df['flight_status'] == FS_CONTROLLED
    df['air_delay']    = np.where(~controlado, df['total_delay'], 0)
    df['ground_delay'] = np.where( controlado, df['total_delay'], 0)
    if 'minutes_etd' in df.columns:
        df['CTD'] = df['minutes_etd'] + df['ground_delay']

    return df

# =============================================================================
# 5. MINIMUM DELAY IMPOSED BY THE CAPACITY DROP
# =============================================================================

def calcular_retraso_minimo_newell(timeline: pd.DataFrame) -> float:
    """
    Total delay imposed by the capacity drop, whatever the allocation rule:
    the area between the cumulative demand and capacity curves (sum over
    minutes of the queue length), in minutes. Any slot allocation that serves
    flights at the reduced rate distributes at least this much delay.

    Args:
        timeline: Newell timeline with 'demand_accum' and 'capacity_accum'.
    """
    cola_por_minuto = (timeline['demand_accum'] - timeline['capacity_accum']).clip(lower=0)
    return cola_por_minuto.sum()

# =============================================================================
# 6. ECONOMIC AND ENVIRONMENTAL KPIs
# =============================================================================

def calcular_retraso_irrecuperable(df_res: pd.DataFrame, h_cancel: float = CFG.H_START) -> pd.Series:
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

    irrecuperable = pd.Series(0.0, index=df_res.index)
    caso2 = ctd <= h_cancel
    irrecuperable[caso2] = ground[caso2]
    caso3 = (etd < h_cancel) & (ctd > h_cancel)
    irrecuperable[caso3] = h_cancel - etd[caso3]
    return irrecuperable


def calcular_kpis_economicos(df_res: pd.DataFrame, h_start: float = CFG.H_START) -> dict:
    """
    Cost and CO2 of one GDP run against the do-nothing case (no regulation:
    every flight absorbs the same delay in an airborne holding). Used by the
    sensitivity heatmaps and the WP2 cost figure.

    COST: the same Cook & Tanner model as WP3-WP5 (cost_model.costes_por_vuelo):
        do-nothing: all the delay costed en route;
        GDP:        ground delay at the gate, air delay en route.
        The reactionary part depends only on the arrival delay, so it is the
        same in both cases: the saving comes from holding on the ground.

    CO2 (Montlaur et al., 2025 for flight emissions; FEGP for ground holding):
        airborne delay burns fuel at the flight's average rate:
            co2_air = co2_kg_vuelo * air_delay / block_time
        ground delay is spent at the gate on fixed electrical ground power:
            co2_ground = FEGP rate (RECAT) * ground_delay

    UNRECOVERABLE DELAY: see calcular_retraso_irrecuperable().
    """
    sin_gdp = df_res.assign(_g=0.0, _a=df_res['total_delay'])
    coste_sin_gdp = costes_por_vuelo(sin_gdp, '_g', '_a')['coste_retraso_eur'].sum()
    coste_con_gdp = costes_por_vuelo(df_res)['coste_retraso_eur'].sum()

    duracion_vuelo = df_res['duracion_vuelo_min'].clip(lower=1)
    tasa_aire = df_res['co2_kg_vuelo'] / duracion_vuelo                   # kg CO2 / min airborne
    tasa_tierra = df_res['recat'].map(co2_tierra_kg_por_min)              # kg CO2 / min at the gate

    co2_extra_aire   = (tasa_aire * df_res['air_delay']).sum()
    co2_extra_tierra = (tasa_tierra * df_res['ground_delay']).sum()
    co2_base         = df_res['co2_kg_vuelo'].sum()

    co2_sin_gdp = co2_base + (tasa_aire * df_res['total_delay']).sum()
    co2_con_gdp = co2_base + co2_extra_aire + co2_extra_tierra

    retraso_irrecuperable = calcular_retraso_irrecuperable(df_res, h_start).sum()

    return {
        'cost_baseline':       round(coste_sin_gdp, 2),
        'cost_gdp':            round(coste_con_gdp, 2),
        'cost_savings':        round(coste_sin_gdp - coste_con_gdp, 2),
        'co2_baseline':        round(co2_sin_gdp, 2),
        'co2_gdp':             round(co2_con_gdp, 2),
        'co2_savings':         round(co2_sin_gdp - co2_con_gdp, 2),
        'co2_aire_delay':      round(co2_extra_aire, 2),
        'co2_tierra_delay':    round(co2_extra_tierra, 2),
        'unrecoverable_delay': round(retraso_irrecuperable, 2),
    }

# =============================================================================
# 7. ORCHESTRATOR
# =============================================================================

def ejecutar_nucleo_gdp(
    df_vuelos: pd.DataFrame,
    params: dict,
    radius_km: int       = CFG.GDP_RADIUS_KM,
    h_file_offset: int = CFG.H_FILE_OFFSET,
    run_compression: bool = True,
) -> dict:
    """
    Run the GDP: Newell curves and HNoReg, flight status, slot grid, RBS slot
    allocation, delays and (optionally) penalty and compression.

    Args:
        df_vuelos:       prepared flights (WP1).
        params:          GDP parameters (H_START, H_END, AAR, PAAR, ...).
        radius_km:       radius of exemption.
        h_file_offset:   Hstart - Hfile, in minutes.
        run_compression: also run penalty + compression (WP2). The sensitivity
                         analysis turns it off.

    Raises:
        RuntimeError: if an affected flight gets no slot (the queue does not
                      clear before midnight).

    Returns:
        dict with vuelos_asignados (RBS result), slots, vuelos_comprimidos,
        vuelos_crudos, timeline, h_noreg and params.
    """
    # 1. Newell curves and HNoReg
    timeline, h_noreg = simular_curvas_newell(df_vuelos, params)

    # 2. Flight status. Only arrivals between Hstart and HNoReg are affected.
    df_vuelos_etiquetados = etiquetar_vuelos_gdp(
        df_vuelos,
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
    lista_slots = []
    tiempo = params['H_START']
    while tiempo < min(h_noreg + 1000, 1440):
        lista_slots.append(round(tiempo, 4))
        tiempo += params['SLOT_RED'] if tiempo < params['H_END'] else params['SLOT_NOM']

    df_slots = pd.DataFrame({
        'slot_start_min': lista_slots,
        'occupied':       False,
        'flight_id':      None,
    })

    # 4. RBS allocation of the affected flights
    df_en_ventana = df_vuelos_etiquetados[
        df_vuelos_etiquetados['flight_status'] != FS_NOT_AFFECTED
    ].copy()
    df_slots_original = df_slots.copy()
    df_resultado_original = asignar_slots_rbs(df_en_ventana, df_slots_original)
    sin_slot = df_resultado_original['assigned_slot'].isna()
    if sin_slot.any():
        raise RuntimeError(
            f"{sin_slot.sum()} flights got no slot before midnight: the queue does not "
            "clear within the simulated day (lower demand or raise capacity)."
        )
    df_resultado_original = calcular_delays(df_resultado_original)

    # 5. Penalty and compression (WP2)
    df_resultado_comprimido = None
    if run_compression:
        from lib_compression import penalty_and_compression
        df_resultado_comprimido = penalty_and_compression(
            df_vuelos_asignados=df_resultado_original,
            num_penalizados=12,
        )
        df_resultado_comprimido = calcular_delays(df_resultado_comprimido)

    return {
        'vuelos_asignados': df_resultado_original,
        'slots': df_slots_original,
        'vuelos_comprimidos': df_resultado_comprimido,
        'vuelos_crudos': df_vuelos,
        'timeline': timeline,
        'h_noreg': h_noreg,
        'params': params,
    }

# =============================================================================
# DEBUG MODE: python src/lib_gdp_core.py
# =============================================================================
if __name__ == "__main__":
    import lib_data_prep as prep

    base     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p_debug  = os.path.join(base, 'debug', 'DEBUG_02_flight_status.xlsx')
    params = CFG.to_params_dict()

    df_vuelos = prep.preparar_vuelos(config.traffic_file(), config.FLEET_TABLE)
    resultados = ejecutar_nucleo_gdp(df_vuelos, params)
    df_resultado = resultados['vuelos_asignados']

    os.makedirs(os.path.dirname(p_debug), exist_ok=True)
    cols_debug = ['ARCID', 'airline', 'ADEP', 'is_ecac', 'distancia_km', 'is_departed', 'flight_status']
    df_resultado[cols_debug].to_excel(p_debug, index=False)
    print(f"Flight status written to {p_debug}")

    h_noreg = resultados['h_noreg']
    kpis = calcular_kpis_economicos(df_resultado)
    print(f"HNoReg {h_noreg // 60:02d}:{h_noreg % 60:02d}, "
          f"unrecoverable delay {kpis['unrecoverable_delay']} min")
