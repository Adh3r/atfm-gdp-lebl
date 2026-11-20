# =============================================================================
# src/lib_compression.py
# WP2 - Penalty and compression (Dènia Cabrera).
#   1. Penalty: the most delayed controlled flights are cancelled.
#   2. Compression: their slots are then reallocated.
#   3. Slot ownership: a freed slot belongs to the airline that held it; it is
#      offered first to that airline's flights, then to any flight.
#   4. Ties are broken first come, first served (ETA, then current slot).
# =============================================================================

import numpy as np
import pandas as pd

from config import FS_CONTROLLED

def penalty_and_compression(df_vuelos_asignados: pd.DataFrame, num_penalizados: int = 12) -> pd.DataFrame:
    """
    Cancel the num_penalizados most delayed controlled flights and compress
    the schedule: each freed slot goes to a later flight of the slot-owning
    airline that can use it (ETA <= slot), or, if the owner has none, to the
    eligible flight with the earliest ETA. The slot that flight leaves behind
    is freed in turn, with its own owner.

    Args:
        df_vuelos_asignados: result of asignar_slots_rbs() / calcular_delays().
        num_penalizados:     number of flights that lose their slot.

    Returns:
        Copy with the reassigned slots; cancelled flights have NaN slot and delays.
    """
    df = df_vuelos_asignados.copy()

    # Only penalize the flights we can regulate (FS_CONTROLLED). 
    # EXEMPTS cannot be touched.

    candidatos = df[df['flight_status'] == FS_CONTROLLED]
    
    if candidatos.empty:
        return df # Nothing to compress
        
    peores_vuelos = candidatos.nlargest(num_penalizados, 'total_delay')
    
    # identify the most affected airline
    counts_afectados = peores_vuelos['airline'].value_counts()
    top_airline = counts_afectados.index[0] if not counts_afectados.empty else "N/A"
    top_count = counts_afectados.max() if not counts_afectados.empty else 0

    # free the slots, keeping their airline ownership
    slots_liberados = peores_vuelos[['assigned_slot', 'airline']].dropna()
    slots_abiertos = [ {'time': row['assigned_slot'], 'airline': row['airline']} for _, row in slots_liberados.iterrows()]

    # cancelled flights have no slot and no delay
    df.loc[peores_vuelos.index, 'assigned_slot'] = np.nan
    df.loc[peores_vuelos.index, 'total_delay'] = np.nan
    df.loc[peores_vuelos.index, 'air_delay'] = np.nan
    df.loc[peores_vuelos.index, 'ground_delay'] = np.nan
    
    # sort the slot list 
    slots_abiertos = sorted(slots_abiertos, key=lambda x: x['time'])

    v_movidos = 0
    ahorro_min = 0

    # COMPRESSION ALGORITHM
    while slots_abiertos:
        # first free slot
        slot_actual = slots_abiertos.pop(0)
        t_slot = slot_actual['time']
        aerolinea_duena = slot_actual['airline']

        # Finding flights that can jump into this slot:
        # 1. Already have a later slot (assigned_slot > t_slot)
        # 2. ETA allows them to make it on time (ETA <= t_slot)
        # 3. Must be FS_CONTROLLED (don't touch exempt flights)
        elegibles = df[
            (df['assigned_slot'] > t_slot) & 
            (df['minutes_eta'] <= t_slot) & 
            (df['assigned_slot'].notna()) &
            (df['flight_status'] == FS_CONTROLLED)
        ]

        if elegibles.empty:
            continue # No one can take advantage of this slot, we lose it.

        # COMPRESSION 1: Priority for the airline that owns the slot
        elegibles_duena = elegibles[elegibles['airline'] == aerolinea_duena]
        
        vuelo_seleccionado_idx = None
        
        if not elegibles_duena.empty:
            # the owner airline moves its flight to the earlier slot
            vuelo_seleccionado_idx = elegibles_duena.sort_values('assigned_slot', kind='stable').index[0]
        else:
            # COMPRESSION 2: if the owner can't fill the slot, it frees the ownership
            # we give the slot to the next flight with the earliest ETA
            vuelo_seleccionado_idx = elegibles.sort_values(['minutes_eta', 'assigned_slot'], kind='stable').index[0]

        # EXECUTE CHANGES 

        slot_dejado_libre = df.at[vuelo_seleccionado_idx, 'assigned_slot']
        aerolinea_que_deja_slot = df.at[vuelo_seleccionado_idx, 'airline']

        # Saving time
        v_movidos += 1
        ahorro_min += (slot_dejado_libre - t_slot)

        # move the flight to the new slot
        df.at[vuelo_seleccionado_idx, 'assigned_slot'] = t_slot

        # the slot that this moved flight has left free goes to the common bag of open slots but mantains the airline-ownership
        slots_abiertos.append({'time': slot_dejado_libre, 'airline': aerolinea_que_deja_slot})
        
        # Resort the list to ensure we always give priority to the earliest
        slots_abiertos = sorted(slots_abiertos, key=lambda x: x['time'])

    # Results printed in core
    print(f"   Compression: {len(peores_vuelos)} flights cancelled "
          f"({top_count} of {top_airline}), {v_movidos} moved earlier, "
          f"{ahorro_min:.0f} min of delay saved")

    return df
