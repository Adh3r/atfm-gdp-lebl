# =============================================================================
# src/cost_model.py
# Shared delay-cost and ground-CO2 reference data, used by the GHP solver
# (lib_ghp_solver) and by the unified metrics engine (lib_metrics).
#
# - Cook & Tanner (2015), European airline delay cost reference values v4.1:
#     tactical cost of delay per flight (Tables 22/24 primary, 26/28 full,
#     at-gate and en-route), EUR 2014, 15 reference aircraft types, 9 delay
#     breakpoints. 'Full' costs add the reactionary (knock-on) costs.
# - Reactionary delay: the delay a late arrival passes on to the next rotation
#   of the same aircraft (IATA delay code 93) and from there to the network.
#   It only appears when the arrival delay exceeds the turnaround buffer
#   (scheduled ground time - minimum turnaround time), so it is computed per
#   flight from the registration mark (RM), as the WP3 statement asks.
# - ICAO type -> closest Cook & Tanner type mapping. Costs are then scaled by
#   real passengers (seats x European load factor) / Cook base passengers, so
#   the mapping captures the operating category (narrow-body, wide-body,
#   turboprop, business jet) rather than the exact seat count.
# - APU fuel flow at the gate (ICAO Doc 9889), for the CO2 of ground delay.
# =============================================================================

import numpy as np
import pandas as pd

# CO2 of ground delay (WP2 statement: "look for references for fuel
# consumption at gate ... one value for each aircraft category"; 1 kg of fuel
# = 3.16 kg of CO2).
#
# A flight held at its origin stand burns fuel only while its auxiliary power
# unit (APU) runs; the rest of the time it is on the stand's ground power.
# - EU airports of the TEN-T network must supply electricity at every contact
#   stand since 31 Dec 2024 and at every remote stand by 31 Dec 2029
#   (Regulation (EU) 2023/1804, AFIR, Art. 12), but airlines are not obliged
#   to use it, so APU use does not drop to zero.
# - Standard practice for the cost of delay in Europe (Cook & Tanner 2015,
#   the EUROCONTROL reference used for the cost model) charges at-gate APU
#   fuel for about 25 % of the time in the base scenario, none in the low
#   scenario and about 50 % in the high scenario. The same shares are used
#   here (base for every result, low/high as a sensitivity range).
# - Emissions accounting (EU ETS monitoring, Implementing Regulation (EU)
#   2018/2066, Art. 52) counts the APU fuel of a flight; the ground-power
#   electricity belongs to the airport's own footprint, so it is not added.
#
# APU fuel flow in normal running (maximum environmental control system load)
# by seat group: ICAO Doc 9889, Airport Air Quality Manual, 2nd ed., Table
# 3-A1-6 (newer-type values where the table separates older and newer types).
APU_FUEL_KG_PER_H = (
    (100, 101.0),           # business and regional jets (seats < 100)
    (200, 110.0),           # 100 <= seats < 200, newer types
    (300, 164.0),           # 200 <= seats < 300
    (float('inf'), 238.0),  # seats >= 300, newer types
)
APU_TIME_SHARE = {'low': 0.0, 'base': 0.25, 'high': 0.50}
CO2_PER_KG_FUEL = 3.16      # IATA Carbon Offset Programme (statement)
DEFAULT_SEATS = 180


def apu_fuel_kg_per_h(seats) -> np.ndarray:
    """APU fuel flow (kg/h) for an array of seat counts (NaN -> 180 seats)."""
    s = np.nan_to_num(np.asarray(seats, dtype=float), nan=DEFAULT_SEATS)
    limits = np.array([lim for lim, _ in APU_FUEL_KG_PER_H])
    flows = np.array([flow for _, flow in APU_FUEL_KG_PER_H])
    return flows[np.searchsorted(limits, s, side='right')]


def ground_co2_kg_per_min(seats, scenario: str = 'base') -> np.ndarray:
    """kg of CO2 per minute of ground delay: APU fuel flow x APU time share x 3.16."""
    return apu_fuel_kg_per_h(seats) / 60.0 * APU_TIME_SHARE[scenario] * CO2_PER_KG_FUEL


# On-time performance threshold: a flight is on time when its delay is below
# 15 min (EUROCONTROL CODA / IATA convention).
OTP_THRESHOLD_MIN = 15

# Minimum turnaround time (minimum ground time between the arrival and the next
# departure of the same aircraft). A rotation scheduled with less ground time
# than this has no buffer: any arrival delay propagates.
#   narrow-body / regional: 35 min, the minimum declared ground time of the
#       E190 operations-manual summary provided in the course ("MIN declared
#       GND time 35 min; delay code 93 if less time available").
#   wide-body (RECAT A-C): 80 min, the value of the delivered project.
MIN_TURNAROUND_MIN = {'narrow': 35, 'wide': 80}

# Cost bases (see flight_cost). 'rotation' is the model used for every
# result; 'primary' and 'full' bound it from below and above (sensitivity).
COST_BASES = ('primary', 'rotation', 'full')
DEFAULT_COST_BASE = 'rotation'

# Base-scenario passengers per reference type: Cook & Tanner, 'The cost of
# passenger delay to airlines in Europe', Univ. of Westminster, 2015, Annex 3
# (the updated passenger allocations used for the 2014 cost values).
_PAX_BASE_TABLE = {
    'A319': 113, 'A320': 130, 'A321': 158,
    'B733': 107, 'B734': 122, 'B738': 137,
    'B735':  95, 'B752': 167, 'B763': 207, 'B744': 334,
    'A332': 232, 'E190':  77, 'DH8D':  57,
    'AT72':  52, 'AT43':  36,
}

# European passenger load factor: 83.7 % (IATA Annual Review 2025).
LOAD_FACTOR_EU = 0.837

# =============================================================================
# ICAO TYPE -> COOK & TANNER REFERENCE TYPE (v4.1, 2014)
#
# Principle: pick the Cook type whose cost structure (fuel, crew, maintenance,
# passengers) is closest to the real aircraft. passenger_factor() then corrects
# the difference in capacity, so the mapping has to capture the operating
# category (narrow-body, wide-body, turboprop, business jet), not just the
# seat count.
#
# LEBL types and their mapping:
#   A320/A20N (173 seats) -> A320 (Cook base: 153 seats, 130 pax)
#   A321/A21N (207/220)   -> A321 (Cook base: 187 seats, 158 pax)
#   A319      (144)       -> A319 (Cook base: 133 seats, 113 pax)
#   B738/B38M (184/189)   -> B738 (Cook base: 161 seats, 137 pax)
#   B739      (183)       -> B738 (closest operationally)
#   BCS3      (145)       -> A319 (same seat range, similar narrow-body costs)
#   B733      (142)       -> B733
#   B789/B788 (284/266)   -> A332 (wide-body of similar capacity)
#   A332/A333/A338/A339   -> A332 (Cook base: 279 seats, 232 pax)
#   A359      (297)       -> A332 (closest type in Cook)
#   B764      (245)       -> B763 (passenger scaling covers the difference)
#   B772/B77L/B77W        -> B744 (heavy wide-body)
#   A388      (492)       -> B744 (the only jumbo in Cook)
#   CRJ9/CRJX (90-100)    -> E190 (closest regional jet)
#   CRJ1/CRJ2 (~50)       -> AT43
#   E190, AT43            -> direct match
#   Business jets         -> AT43, the cheapest Cook type; the passenger factor
#                            (real pax / 36) scales the cost down for 5-15 pax.
# =============================================================================
_FLEET_TO_COOK = {
    # --- Airbus A320 family ---
    'A318': 'A319', 'A319': 'A319',
    'A320': 'A320', 'A20N': 'A320',   # A320neo -> same Cook table, real seats
    'A321': 'A321', 'A21N': 'A321',   # A321neo -> same Cook table
    # --- A220 (Bombardier CRJ -> Airbus) ---
    'BCS1': 'A319',                    # A220-100 (~115 seats) -> A319 (133 seats Cook)
    'BCS3': 'A319',                    # A220-300 (~145 seats) -> A319 (closest)
    # --- Boeing 737 Classic and NG ---
    'B733': 'B733', 'B734': 'B734', 'B735': 'B735', 'B736': 'B735',
    'B737': 'B734',
    'B738': 'B738', 'B38M': 'B738',   # B737 MAX 8 -> B738 as proxy
    'B739': 'B738',                    # B737-900 -> B738 (same family)
    'B752': 'B752', 'B753': 'B752',
    # --- Embraer E-Jets ---
    'E170': 'E190', 'E175': 'E190', 'E190': 'E190', 'E195': 'E190',
    'E75L': 'E190', 'E75S': 'E190',
    # --- Bombardier CRJ ---
    'CRJ1': 'AT43', 'CRJ2': 'AT43',   # CRJ-100/200 (~50 seats) -> AT43
    'CRJ7': 'AT72',                    # CRJ-700 (~70 seats) -> AT72
    'CRJ9': 'E190', 'CRJX': 'E190',   # CRJ-900/1000 (~90-100 seats) -> E190
    # --- DHC-8 / Turboprop ---
    'AT43': 'AT43', 'AT46': 'AT43',
    'AT72': 'AT72', 'ATP':  'AT72',
    'DH8A': 'AT43', 'DH8B': 'AT43', 'DH8C': 'AT43', 'DH8D': 'DH8D',
    'F50':  'AT43', 'F70':  'AT72', 'SB20': 'AT43',
    # --- Other narrow-bodies ---
    'F100': 'E190', 'RJ1H': 'E190', 'RJ85': 'E190',
    'B712': 'B733', 'B462': 'B733', 'B463': 'B733',
    'MD80': 'B738', 'MD82': 'B738', 'MD83': 'B738',
    'T154': 'B733', 'YK42': 'B733', 'SU95': 'E190',
    'E120': 'AT43', 'E135': 'AT43', 'E145': 'AT43',
    'B190': 'AT43', 'CARJ': 'AT43', 'SF34': 'AT43',
    'A140': 'AT43', 'A148': 'AT43',
    # --- Mid-size wide-bodies (A330 / B787) ---
    'A306': 'B763', 'A310': 'B763',
    'A332': 'A332', 'A333': 'A332', 'A338': 'A332', 'A339': 'A332',
    'A340': 'A332', 'A343': 'A332',
    'A359': 'A332', 'A35K': 'A332',   # A350-900/1000 -> A332 (closest Cook type)
    'B788': 'A332', 'B789': 'A332',   # B787-8/9 (~260-280 seats) -> A332
    'B78X': 'B744',                    # B787-10 (~330 seats) -> B744
    # --- Heavy wide-bodies ---
    'A345': 'B744', 'A346': 'B744',
    'A388': 'B744',                    # A380 (492 seats) -> B744 (only jumbo in Cook)
    'B744': 'B744', 'B748': 'B744',
    'B762': 'B763', 'B763': 'B763', 'B764': 'B763',
    'B772': 'B744',                    # B777-200 (~300 seats) -> B744
    'B773': 'B744', 'B77W': 'B744', 'B77L': 'B744',
    'IL96': 'B763', 'T204': 'B763',
    # --- Business jets (RECAT E/F at LEBL) ---
    # Proxy: AT43, the cheapest Cook type. The passenger factor
    # (seats x LF) / 36 scales the cost down (e.g. PC24, 8 seats -> ~0.19).
    'GLEX': 'AT43', 'GLF6': 'AT43', 'GLF5': 'AT43', 'GLF4': 'AT43',
    'GA5C': 'AT43', 'GA6C': 'AT43',
    'C525': 'AT43', 'C56X': 'AT43', 'C560': 'AT43', 'C650': 'AT43',
    'C68A': 'AT43', 'C750': 'AT43',
    'E35L': 'AT43', 'E50P': 'AT43', 'E55P': 'AT43',
    'F2TH': 'AT43', 'FA50': 'AT43', 'FA7X': 'AT43',
    'PC24': 'AT43', 'PC12': 'AT43',
    'CL35': 'AT43', 'CL60': 'AT43', 'CL30': 'AT43',
    'H25B': 'AT43', 'H25C': 'AT43',
    'LJ45': 'AT43', 'LJ60': 'AT43',
    'C25A': 'AT43', 'C25B': 'AT43', 'C25C': 'AT43',
    'BE40': 'AT43', 'BE20': 'AT43', 'BE9L': 'AT43',
    'PRM1': 'AT43', 'TBM9': 'AT43', 'TBM8': 'AT43',
}

# Tactical delay costs per flight, EUR 2014, BASE cost scenario.
# A. Cook & G. Tanner, European airline delay cost reference values, v4.1,
# University of Westminster for EUROCONTROL PRU, 2015:
#   Table 22  AT-GATE  / primary tactical costs     Table 26  AT-GATE  / full tactical costs
#   Table 24  EN-ROUTE / primary tactical costs     Table 28  EN-ROUTE / full tactical costs
# 'primary' = costs of the delayed flight itself (passengers, crew, fuel,
# maintenance); 'full' = primary plus the reactionary costs the delay passes
# on to later rotations, all costed at the gate. Hence full - primary is the
# same at-gate and en-route (checked in the tests).
# Columns are the delay breakpoints in _DELAY_BREAKPOINTS.
_COOK_TACTICAL = {
    ('at_gate', 'primary'): {
        'A319': [    80,    400,   1230,   4040,   7980,  12800,  25030,  40550,  59200],
        'A320': [    90,    450,   1390,   4590,   9100,  14620,  28620,  46430,  67820],
        'A321': [   100,    520,   1630,   5470,  10890,  17530,  34440,  55970,  81850],
        'B733': [    80,    400,   1200,   3900,   7670,  12280,  23930,  38710,  56440],
        'B734': [    90,    440,   1340,   4390,   8650,  13870,  27090,  43870,  64020],
        'B735': [    80,    360,   1090,   3500,   6870,  10970,  21350,  34510,  50280],
        'B738': [   100,    490,   1490,   4900,   9680,  15520,  30340,  49160,  71760],
        'B752': [   110,    560,   1740,   5820,  11570,  18620,  36540,  59340,  86740],
        'B763': [   140,    710,   2190,   7280,  14430,  23200,  45460,  73780, 107800],
        'B744': [   200,   1080,   3420,  11500,  22930,  36960,  72650, 118100, 172760],
        'A332': [   150,    780,   2430,   8110,  16100,  25900,  50810,  82500, 120580],
        'E190': [    70,    300,    900,   2870,   5620,   8960,  17410,  28110,  40930],
        'DH8D': [    50,    240,    700,   2190,   4250,   6760,  13080,  21060,  30620],
        'AT72': [    50,    230,    650,   2030,   3920,   6230,  12020,  19330,  28080],
        'AT43': [    40,    180,    490,   1490,   2850,   4490,   8580,  13730,  19880],
    },
    ('at_gate', 'full'): {
        'A319': [    70,    440,   1600,   7320,  20040,  37850,  51240,  69420,  93180],
        'A320': [    80,    500,   1820,   8350,  22920,  43250,  58420,  78890, 105380],
        'A321': [   100,    580,   2160,  10010,  27580,  51990,  70060,  94250, 125240],
        'B733': [    70,    430,   1550,   7020,  19160,  36220,  49040,  66480,  89310],
        'B734': [    80,    480,   1740,   7930,  21690,  40960,  55340,  74780, 100040],
        'B735': [    70,    390,   1400,   6280,  17110,  32350,  43900,  59720,  80590],
        'B738': [    90,    540,   1940,   8860,  24270,  45750,  61740,  83220, 110920],
        'B752': [   100,    620,   2290,  10620,  29250,  55150,  74240,  99700, 132200],
        'B763': [   170,    900,   3200,  14780,  39960,  85300, 121880, 152860, 191990],
        'B744': [   240,   1370,   5000,  23430,  63710, 136330, 194330, 242440, 302200],
        'A332': [   180,    990,   3550,  16480,  44620,  95330, 136120, 170480, 213660],
        'E190': [    60,    320,   1150,   5140,  13970,  26440,  36060,  49420,  67340],
        'DH8D': [    40,    250,    890,   3900,  10530,  19990,  27480,  38120,  52780],
        'AT72': [    40,    240,    820,   3600,   9690,  18430,  25380,  35350,  49210],
        'AT43': [    30,    180,    610,   2610,   6960,  13290,  18550,  26360,  37610],
    },
    ('en_route', 'primary'): {
        'A319': [   250,    910,   2250,   6070,  11030,  16860,  31120,  48670,  69340],
        'A320': [   260,    960,   2420,   6650,  12180,  18730,  34790,  54650,  78100],
        'A321': [   300,   1130,   2850,   7910,  14550,  22420,  41770,  65740,  94070],
        'B733': [   260,    930,   2270,   6040,  10880,  16550,  30330,  47250,  67110],
        'B734': [   270,    970,   2400,   6520,  11850,  18130,  33480,  52390,  74670],
        'B735': [   240,    850,   2060,   5450,   9790,  14870,  27200,  42300,  60030],
        'B738': [   280,   1030,   2580,   7080,  12950,  19890,  36890,  57900,  82680],
        'B752': [   350,   1280,   3180,   8700,  15890,  24380,  45170,  70840, 101120],
        'B763': [   480,   1730,   4230,  11360,  20550,  31360,  57700,  90100, 128200],
        'B744': [   890,   3150,   7560,  19780,  35350,  53520,  97490, 151220, 214160],
        'A332': [   540,   1930,   4740,  12730,  23040,  35150,  64670, 100980, 143690],
        'E190': [   190,    680,   1660,   4400,   7900,  12010,  21980,  34200,  48540],
        'DH8D': [   120,    440,   1090,   2980,   5440,   8340,  15440,  24210,  34550],
        'AT72': [   100,    380,    960,   2640,   4850,   7450,  13860,  21790,  31150],
        'AT43': [    80,    290,    710,   1920,   3490,   5340,   9870,  15450,  22020],
    },
    ('en_route', 'full'): {
        'A319': [   240,    950,   2620,   9350,  23090,  41910,  57330,  77540, 103320],
        'A320': [   250,   1010,   2850,  10410,  26000,  47360,  64590,  87110, 115660],
        'A321': [   300,   1190,   3380,  12450,  31240,  56880,  77390, 104020, 137460],
        'B733': [   250,    960,   2620,   9160,  22370,  40490,  55440,  75020,  99980],
        'B734': [   260,   1010,   2800,  10060,  24890,  45220,  61730,  83300, 110690],
        'B735': [   230,    880,   2370,   8230,  20030,  36250,  49750,  67510,  90340],
        'B738': [   270,   1080,   3030,  11040,  27540,  50120,  68290,  91960, 121840],
        'B752': [   340,   1340,   3730,  13500,  33570,  60910,  82870, 111200, 146580],
        'B763': [   510,   1920,   5240,  18860,  46080,  93460, 134120, 169180, 212390],
        'B744': [   930,   3440,   9140,  31710,  76130, 152890, 219170, 275560, 343600],
        'A332': [   570,   2140,   5860,  21100,  51560, 104580, 149980, 188960, 236770],
        'E190': [   180,    700,   1910,   6670,  16250,  29490,  40630,  55510,  74950],
        'DH8D': [   110,    450,   1280,   4690,  11720,  21570,  29840,  41270,  56710],
        'AT72': [    90,    390,   1130,   4210,  10620,  19650,  27220,  37810,  52280],
        'AT43': [    70,    290,    830,   3040,   7600,  14140,  19840,  28080,  39750],
    },
}

_DELAY_BREAKPOINTS = [5, 15, 30, 60, 90, 120, 180, 240, 300]


def _cook_type(icao_type: str, recat: str, seats: int = 0) -> str:
    """
    Closest Cook & Tanner v4.1 reference type for an ICAO aircraft type.

      1. Direct lookup in _FLEET_TO_COOK.
      2. Otherwise by RECAT category and seats:
         - RECAT A/B/C (wide-body):   B744 if seats > 350, A332 if > 240, else B763
         - RECAT D     (narrow-body): A321 if seats > 195, A320 if > 160,
                                      A319 if > 130, else B733
         - RECAT E     (regional):    E190 if seats > 80, AT72 if > 55, else AT43
         - RECAT F     (light):       AT43 (with a very low passenger factor)
    """
    icao_upper = str(icao_type).upper().strip()
    if icao_upper in _FLEET_TO_COOK:
        return _FLEET_TO_COOK[icao_upper]

    recat_upper = str(recat).upper()
    if recat_upper in ('A', 'B', 'C'):
        if seats > 350:
            return 'B744'
        if seats > 240:
            return 'A332'
        return 'B763'
    if recat_upper == 'D':
        if seats > 195:
            return 'A321'
        if seats > 160:
            return 'A320'
        if seats > 130:
            return 'A319'
        return 'B733'
    if recat_upper == 'E':
        if seats > 80:
            return 'E190'
        if seats > 55:
            return 'AT72'
        return 'AT43'
    # RECAT F or unknown
    return 'AT43'


# =============================================================================
# COST CURVES
# =============================================================================

def _curve(cook_type: str, phase: str, scope: str, delays) -> 'np.ndarray':
    """Cook & Tanner cost (EUR per flight, Cook base load) for an array of delays.

    Linear interpolation between the breakpoints, from 0 EUR at 0 min. Beyond
    300 min the last segment is extended, so extra delay is never free.
    """
    d = np.clip(np.asarray(delays, dtype=float), 0, None)
    table = _COOK_TACTICAL[(phase, scope)]
    x = np.array([0] + _DELAY_BREAKPOINTS, dtype=float)
    y = np.array([0] + table.get(cook_type, table['A320']), dtype=float)
    out = np.interp(d, x, y)
    beyond = d > x[-1]
    out[beyond] = y[-1] + (y[-1] - y[-2]) / (x[-1] - x[-2]) * (d[beyond] - x[-1])
    return out


def primary_cost(cook_type: str, delays, phase: str = 'at_gate') -> 'np.ndarray':
    """Primary tactical cost (Tables 22/24): the delayed flight's own costs."""
    return _curve(cook_type, phase, 'primary', delays)


def reactionary_cost(cook_type: str, delays) -> 'np.ndarray':
    """Reactionary cost of a delay passed on to the next rotation.

    Cook & Tanner full minus primary at-gate cost (Table 26 - Table 22). At
    5 min the full value is slightly below the primary one (rounding and the
    different passenger treatment of the two tables), so the difference is
    floored at 0 and made non-decreasing.
    """
    d = np.clip(np.asarray(delays, dtype=float), 0, None)
    x = np.array([0] + _DELAY_BREAKPOINTS, dtype=float)
    full = _COOK_TACTICAL[('at_gate', 'full')]
    prim = _COOK_TACTICAL[('at_gate', 'primary')]
    y = np.array([0] + full.get(cook_type, full['A320']), dtype=float) \
        - np.array([0] + prim.get(cook_type, prim['A320']), dtype=float)
    y = np.maximum.accumulate(np.clip(y, 0, None))
    out = np.interp(d, x, y)
    beyond = d > x[-1]
    out[beyond] = y[-1] + (y[-1] - y[-2]) / (x[-1] - x[-2]) * (d[beyond] - x[-1])
    return out


def propagated_delay(delay, buffer) -> 'np.ndarray':
    """Delay passed on to the next departure of the same aircraft.

    buffer: turnaround buffer (scheduled ground time - minimum turnaround
    time, >= 0). NaN means no later departure of this aircraft from LEBL that
    day (night stop): nothing propagates.
    """
    d = np.asarray(delay, dtype=float)
    h = np.asarray(buffer, dtype=float)
    return np.where(np.isnan(h), 0.0, np.clip(d - np.nan_to_num(h), 0, None))


# =============================================================================
# COST OF DELAY OF A FLIGHT
# =============================================================================

def flight_cost(
    cook_type: str,
    pax_factor: float,
    ground_delay,
    air_delay=0.0,
    buffer=np.nan,
    base: str = DEFAULT_COST_BASE,
) -> 'np.ndarray':
    """
    Cost of delay (EUR 2014) of one flight for an array of delays.

        primary   = P_gate(ground) + P_enroute(air)
        'primary' : primary only (lower bound: no knock-on effect).
        'rotation': primary + R(p), where p = max(0, delay - turnaround buffer)
                    is the delay passed on to the aircraft's next departure and
                    R = Cook full - primary is the reactionary cost of a delay
                    that enters the network. A flight whose buffer absorbs the
                    delay, or with no further rotation that day, costs primary.
        'full'    : primary + R(delay), Cook & Tanner's network average, which
                    assumes an average buffer (upper bound here).

    All terms are scaled by pax_factor (real passengers / Cook base load).
    """
    if base not in COST_BASES:
        raise ValueError(f"base must be one of {COST_BASES}")
    g = np.clip(np.asarray(ground_delay, dtype=float), 0, None)
    a = np.clip(np.broadcast_to(np.asarray(air_delay, dtype=float), g.shape), 0, None)
    cost = primary_cost(cook_type, g, 'at_gate') + primary_cost(cook_type, a, 'en_route')
    if base == 'full':
        cost = cost + reactionary_cost(cook_type, g + a)
    elif base == 'rotation':
        cost = cost + reactionary_cost(cook_type, propagated_delay(g + a, buffer))
    return cost * pax_factor


def passenger_factor(cook_type: str, seats) -> float:
    """Real passengers (seats x European load factor) / Cook base passengers."""
    try:
        seats = float(seats)
    except (TypeError, ValueError):
        seats = 180.0
    if not seats > 0:
        seats = 180.0
    real_pax = max(int(seats * LOAD_FACTOR_EU), 1)
    return real_pax / _PAX_BASE_TABLE.get(cook_type, 130)


def _cook_type_and_factor(row) -> tuple[str, float]:
    seats = row.get('size_seats_avg', 180)
    seats = 180 if pd.isna(seats) else seats
    cook_type = _cook_type(row.get('ATYP', ''), row.get('recat', 'D'), int(seats))
    return cook_type, passenger_factor(cook_type, seats)


def turnaround_buffer(df: pd.DataFrame, mtt_extra: float = 0.0) -> pd.Series:
    """Turnaround buffer of each arrival: scheduled ground time - MTT (>= 0).

    NaN when the aircraft has no later departure from LEBL that day.
    mtt_extra shifts the minimum turnaround times (sensitivity analysis).
    """
    if 'scheduled_turnaround_min' not in df.columns:
        return pd.Series(np.nan, index=df.index)
    wide = df.get('recat', pd.Series('D', index=df.index)).isin(['A', 'B', 'C'])
    mtt = np.where(wide, MIN_TURNAROUND_MIN['wide'], MIN_TURNAROUND_MIN['narrow']) + mtt_extra
    return (df['scheduled_turnaround_min'] - mtt).clip(lower=0)


def flight_costs(
    df: pd.DataFrame,
    ground_col: str = 'ground_delay',
    air_col: str = 'air_delay',
    base: str = DEFAULT_COST_BASE,
    mtt_extra: float = 0.0,
) -> pd.DataFrame:
    """Cost of delay of every flight of a scenario.

    Returns a DataFrame (same index) with:
        primary_cost_eur, reactionary_cost_eur, delay_cost_eur (their sum)
        propagated_delay_min (delay passed on to the next rotation)
    """
    n = len(df)
    def _col(name):
        if name in df.columns:
            return df[name].fillna(0).to_numpy(dtype=float)
        return np.zeros(n)
    g, a = _col(ground_col), _col(air_col)
    buffer = turnaround_buffer(df, mtt_extra).to_numpy(dtype=float)
    cook_types, fpax = map(np.array, zip(*(_cook_type_and_factor(f) for _, f in df.iterrows()))) if n else ([], [])

    prim, total = np.zeros(n), np.zeros(n)
    for cook_type in np.unique(cook_types):
        m = cook_types == cook_type
        prim[m] = flight_cost(cook_type, fpax[m].astype(float), g[m], a[m], base='primary')
        total[m] = flight_cost(cook_type, fpax[m].astype(float), g[m], a[m], buffer[m], base)
    if base == 'rotation':
        prop = propagated_delay(g + a, buffer)
    elif base == 'full':
        prop = g + a
    else:
        prop = np.zeros(n)
    return pd.DataFrame({'primary_cost_eur': prim, 'reactionary_cost_eur': total - prim,
                         'delay_cost_eur': total, 'propagated_delay_min': prop},
                        index=df.index)
