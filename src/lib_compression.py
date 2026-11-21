# =============================================================================
# src/lib_compression.py
# WP2 - Cancellations, substitution and compression (original algorithm by
# Dènia Cabrera), following the WP2 statement:
#
#   "simulate that one airline (one with lots of flights at your airport)
#    decides to cancel all flights with high delays (for instance > 2 or 3 h)
#    ... 1. eliminate them from the controlled slots, 2. apply substitution for
#    the chosen airline (maintaining original order of flights), 3. once this
#    airline has used all its emptied slots, apply compression in order to fill
#    all slots."
#
# Slot ownership: a slot belongs to the airline whose flight held it. In the
# European ATFM system the same ideas appear as slot swapping between an
# operator's own flights (substitution) and the automatic slot improvement of
# CASA when a slot is released (compression).
# =============================================================================

import numpy as np
import pandas as pd

from config import FS_CONTROLLED

CANCEL_DELAY_THRESHOLD_MIN = 180   # "> 2 or 3 h depending on your airport": 3 h


def _eligible(df: pd.DataFrame, slot: float) -> pd.DataFrame:
    """Controlled flights that can move up into `slot` (ETA <= slot < current slot)."""
    return df[
        (df['flight_status'] == FS_CONTROLLED)
        & df['assigned_slot'].notna()
        & (df['assigned_slot'] > slot)
        & (df['minutes_eta'] <= slot)
    ]


def _move(df: pd.DataFrame, idx, slot: float) -> float:
    """Move flight idx to slot; return the slot it leaves free."""
    freed = df.at[idx, 'assigned_slot']
    df.at[idx, 'assigned_slot'] = slot
    return freed


def cancel_and_compress(
    df_assigned: pd.DataFrame,
    airline: str | None = None,
    delay_threshold: float = CANCEL_DELAY_THRESHOLD_MIN,
    verbose: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """
    Cancel the controlled flights of one airline delayed by more than
    delay_threshold, then reallocate the freed slots.

    1. Substitution: each slot freed by the airline goes to its own next
       controlled flight that can use it (earliest current slot first, so the
       airline keeps the original order of its flights). The slot that flight
       leaves is the airline's too and is reused the same way.
    2. Compression: the slots the airline cannot use are offered, in time
       order, first to a flight of the slot's owner and otherwise to the
       eligible flight with the earliest ETA. The slot that flight leaves
       belongs to its airline and goes back into the pool.

    Args:
        df_assigned:     RBS allocation (assign_slots_rbs + compute_delays).
        airline:         cancelling airline; default the one with most
                         controlled flights.
        delay_threshold: flights of that airline with more delay are cancelled.
        verbose:         print a one-line summary.

    Returns:
        (allocation, summary). Cancelled flights have NaN slot and delays.
    """
    df = df_assigned.copy()
    controlled = df['flight_status'] == FS_CONTROLLED
    if airline is None:
        airline = df.loc[controlled, 'airline'].value_counts().idxmax()

    cancel = controlled & (df['airline'] == airline) & (df['total_delay'] > delay_threshold)
    pool = sorted(df.loc[cancel, 'assigned_slot'].tolist())
    df.loc[cancel, ['assigned_slot', 'total_delay', 'air_delay', 'ground_delay']] = np.nan
    if 'CTA' in df.columns:
        df.loc[cancel, 'CTA'] = np.nan

    # 1. Substitution within the cancelling airline
    by_substitution, rest = 0, []
    while pool:
        slot = pool.pop(0)
        own = _eligible(df, slot)
        own = own[own['airline'] == airline]
        if own.empty:
            rest.append((slot, airline))
            continue
        idx = own.sort_values('assigned_slot', kind='stable').index[0]
        pool = sorted(pool + [_move(df, idx, slot)])
        by_substitution += 1

    # 2. Compression of the remaining slots (owner first, then earliest ETA)
    by_compression, unused = 0, 0
    rest.sort()
    while rest:
        slot, owner = rest.pop(0)
        candidates = _eligible(df, slot)
        if candidates.empty:
            unused += 1
            continue
        same_owner = candidates[candidates['airline'] == owner]
        chosen = same_owner if not same_owner.empty else candidates
        idx = chosen.sort_values(['minutes_eta', 'assigned_slot'], kind='stable').index[0]
        mover_airline = df.at[idx, 'airline']
        rest = sorted(rest + [(_move(df, idx, slot), mover_airline)])
        by_compression += 1

    before = df_assigned['assigned_slot']
    after = df['assigned_slot']
    moved = after.notna() & (after < before)
    remaining = (df['airline'] == airline) & controlled & ~cancel
    summary = {
        'airline': airline,
        'delay_threshold_min': delay_threshold,
        'cancelled_flights': int(cancel.sum()),
        'moved_by_substitution': by_substitution,
        'moved_by_compression': by_compression,
        'flights_moved_earlier': int(moved.sum()),
        'delay_saved_min': float((before - after)[moved].sum()),
        'airline_mean_delay_before_min': float(df_assigned.loc[remaining, 'total_delay'].mean()),
        'airline_mean_delay_after_min': float((after - df['minutes_eta'])[remaining].mean()),
        'unused_slots': unused,
    }
    if verbose:
        print(f"   Cancellations: {airline} cancels {summary['cancelled_flights']} flights delayed "
              f"> {delay_threshold:.0f} min; {by_substitution} substitutions, "
              f"{by_compression} compression moves, {summary['delay_saved_min']:.0f} min saved")
    return df, summary
