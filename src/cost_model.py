# =============================================================================
# src/cost_model.py
# Shared delay-cost and ground-CO2 reference data, used by the GHP solver
# (lib_ghp_solver) and by the unified metrics engine (lib_metrics).
#
# - Cook & Tanner (2015), European airline delay cost reference values v4.1:
#     tactical cost of delay per flight (Tables 22/24 primary, 26/28 full,
#     at-gate and en-route), EUR 2014, 15 reference aircraft types, 9 delay
#     breakpoints. 'Full' costs include reactionary (network) delay.
# - ICAO type -> closest Cook & Tanner type mapping. Costs are then scaled by
#   real passengers (seats x European load factor) / Cook base passengers, so
#   the mapping captures the operating category (narrow-body, wide-body,
#   turboprop, business jet) rather than the exact seat count.
# - Fixed Electrical Ground Power (FEGP) demand per RECAT category and the
#   Spanish grid emission factor, for CO2 emitted while holding on the ground.
# =============================================================================

import pandas as pd

# Consumo eléctrico demandado por categoría RECAT en kW
FEGP_KW_PER_RECAT = {
    'A': 288.0,  # A388: 4 x 90 kVA (360 kVA * 0.8 = 288 kW)
    'B': 144.0,  # B789: 2 x 90 kVA (180 kVA * 0.8 = 144 kW)
    'C': 144.0,  # B764: 2 x 90 kVA (180 kVA * 0.8 = 144 kW)
    'D': 72.0,   # A320: 1 x 90 kVA (90 kVA * 0.8 = 72 kW)
    'E': 32.0,   # E190: Límite del bus interno (40 kVA * 0.8 = 32 kW)
    'F': 12.8,   # PC24: 28.5 VDC * 450 Amperios continuos = 12.8 kW
}

# Factor de emisión de la red eléctrica en España (kg CO2 / kWh)
# Fuente: Red Eléctrica de España (REE) / MITERD 2023
GRID_EMISSION_FACTOR_KG_KWH = 0.283


def co2_tierra_kg_por_min(recat) -> float:
    """kg of CO2 per minute for an aircraft held at the gate on FEGP.

    Unknown or missing RECAT categories fall back to D (A320 family).
    """
    clave = str(recat).upper() if isinstance(recat, str) else 'D'
    potencia_kw = FEGP_KW_PER_RECAT.get(clave, FEGP_KW_PER_RECAT['D'])
    return potencia_kw / 60.0 * GRID_EMISSION_FACTOR_KG_KWH

# Umbral OTP: delay < 15 min = "on-time" (IATA/CODA estándar).
OTP_THRESHOLD_MIN = 15

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

# Load factor europeo. Fuente: IATA Annual Review 2025, promedio europeo: 83,7%
LOAD_FACTOR_EU = 0.837

# =============================================================================
# MAPEO ICAO → TIPO COOK & TANNER (v4.1, 2014)
#
# Principio: identificar el tipo Cook cuya estructura de costes (fuel, crew,
# maintenance, pax) sea lo mas similar posible a la aeronave real. El ajuste
# por densidad de pasajeros (factor_pasajeros) corrige despues
# la diferencia de capacidad, por lo que el mapeo debe capturar la CATEGORIA
# de operacion (turbofan narrowbody, widebody, turboprop, bizjet), no solo el
# numero de asientos.
#
# Aeronaves del CSV de LEBL con sus mappings:
#   A320/A20N (173 seats) → A320 (Cook base: 153 seats, pax 130)
#   A321/A21N (207/220)   → A321 (Cook base: 187 seats, pax 158)
#   A319      (144)       → A319 (Cook base: 133 seats, pax 113)
#   B738/B38M (184/189)   → B738 (Cook base: 161 seats, pax 137)
#   B739      (183)       → B738 (mas cercano operacionalmente)
#   BCS3      (145)       → A319 (mismo rango de asientos; la A220-300 tiene
#                           estructura de costes de narrowbody similar a A319)
#   B733      (142, RECAT E en CSV → probable error de datos; tratamos como
#                           narrowbody pequeno → B733 de Cook)
#   B789/B788 (284/266)   → A332 (widebody de capacidad similar)
#   A332/A333/A338/A339   → A332 (Cook base: 279 seats, pax 232)
#   A359      (297)       → A332 (mas cercano disponible en Cook)
#   B764      (245)       → B763 (Cook base: 279 seats, ajuste pax cubre dif.)
#   B772/B77L/B77W (296-331) → B744 (widebody pesado, estructura similar)
#   A388      (492)       → B744 (unico jumbo disponible en Cook)
#   CRJ9/CRJX (90-100)   → E190 (regional jet, mas cercano en Cook)
#   CRJ1/CRJ2 (~50)      → AT43
#   E190      (104)       → E190 (match directo)
#   AT43      (49)        → AT43 (match directo)
#   Bizjets (GLEX,GLF6,GA5C,C525,C560,C650,C68A,E35L,E50P,
#            E55P,F2TH,PC24,PC12,CL35) → AT43 como proxy de coste de bizjet
#            (estructura de coste minima en Cook; el factor_pax = seats_reales/36
#            escala el coste a la baja automaticamente para aviones con 5-15 pax)
# =============================================================================
_FLEET_TO_COOK = {
    # --- Familia Airbus A320 ---
    'A318': 'A319', 'A319': 'A319',
    'A320': 'A320', 'A20N': 'A320',   # A320neo -> misma tabla Cook, seats reales del CSV
    'A321': 'A321', 'A21N': 'A321',   # A321neo -> misma tabla Cook
    # --- A220 (Bombardier CRJ -> Airbus) ---
    'BCS1': 'A319',                    # A220-100 (~115 seats) -> A319 (133 seats Cook)
    'BCS3': 'A319',                    # A220-300 (~145 seats) -> A319 (mas cercano)
    # --- Boeing 737 classics y NG ---
    'B733': 'B733', 'B734': 'B734', 'B735': 'B735', 'B736': 'B735',
    'B737': 'B734',
    'B738': 'B738', 'B38M': 'B738',   # B737 MAX 8 -> B738 como proxy
    'B739': 'B738',                    # B737-900 -> B738 (misma familia)
    'B752': 'B752', 'B753': 'B752',
    # --- Embraer comercial ---
    'E170': 'E190', 'E175': 'E190', 'E190': 'E190', 'E195': 'E190',
    'E75L': 'E190', 'E75S': 'E190',
    # --- CRJ comercial ---
    'CRJ1': 'AT43', 'CRJ2': 'AT43',   # CRJ-100/200 (~50 seats) -> AT43
    'CRJ7': 'AT72',                    # CRJ-700 (~70 seats) -> AT72
    'CRJ9': 'E190', 'CRJX': 'E190',   # CRJ-900/1000 (~90-100 seats) -> E190
    # --- DHC-8 / Turboprop ---
    'AT43': 'AT43', 'AT46': 'AT43',
    'AT72': 'AT72', 'ATP':  'AT72',
    'DH8A': 'AT43', 'DH8B': 'AT43', 'DH8C': 'AT43', 'DH8D': 'DH8D',
    'F50':  'AT43', 'F70':  'AT72', 'SB20': 'AT43',
    # --- Otros narrowbody comerciales ---
    'F100': 'E190', 'RJ1H': 'E190', 'RJ85': 'E190',
    'B712': 'B733', 'B462': 'B733', 'B463': 'B733',
    'MD80': 'B738', 'MD82': 'B738', 'MD83': 'B738',
    'T154': 'B733', 'YK42': 'B733', 'SU95': 'E190',
    'E120': 'AT43', 'E135': 'AT43', 'E145': 'AT43',
    'B190': 'AT43', 'CARJ': 'AT43', 'SF34': 'AT43',
    'A140': 'AT43', 'A148': 'AT43',
    # --- Widebody medio (A330 / B787) ---
    'A306': 'B763', 'A310': 'B763',
    'A332': 'A332', 'A333': 'A332', 'A338': 'A332', 'A339': 'A332',
    'A340': 'A332', 'A343': 'A332',
    'A359': 'A332', 'A35K': 'A332',   # A350-900/1000 -> A332 (mas cercano Cook)
    'B788': 'A332', 'B789': 'A332',   # B787-8/9 (~260-280 seats) -> A332
    'B78X': 'B744',                    # B787-10 (~330 seats) -> B744
    # --- Widebody pesado / jumbo ---
    'A345': 'B744', 'A346': 'B744',
    'A388': 'B744',                    # A380 (492 seats) -> B744 (unico jumbo Cook)
    'B744': 'B744', 'B748': 'B744',
    'B762': 'B763', 'B763': 'B763', 'B764': 'B763',
    'B772': 'B744',                    # B777-200 (~300 seats) -> B744
    'B773': 'B744', 'B77W': 'B744', 'B77L': 'B744',
    'IL96': 'B763', 'T204': 'B763',
    # --- Business jets / VIP (RECAT E/F en LEBL) ---
    # Proxy: AT43 como tipo Cook de menor coste disponible.
    # El factor_pax = (seats_reales x LF) / 36 escala el coste a la baja
    # automaticamente (p.ej. PC24 con 8 seats → factor ~0.19).
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
# 'primary' = costs of the delayed flight itself; 'full' = primary plus the
# reactionary (network) costs the delay propagates to later rotations.
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

def _mapear_tipo_cook(tipo_icao: str, recat: str, seats: int = 0) -> str:
    """
    Mapea tipo ICAO al tipo Cook & Tanner v4.1 mas cercano.

    Orden de prioridad:
      1. Lookup directo en _FLEET_TO_COOK (identificacion exacta por tipo ICAO).
      2. Fallback por RECAT + seats cuando el tipo no esta en la tabla:
         - RECAT A/B/C (widebody): B744 si seats > 350, A332 si seats > 240, B763
         - RECAT D     (narrowbody): A321 si seats > 195, A320 si seats > 160,
                                     A319 si seats > 130, B733
         - RECAT E     (regional):   E190 si seats > 80, AT72 si seats > 55, AT43
         - RECAT F     (ligero):     AT43 (con factor_pax muy bajo)
    """
    tipo_upper = str(tipo_icao).upper().strip()
    if tipo_upper in _FLEET_TO_COOK:
        return _FLEET_TO_COOK[tipo_upper]

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
    # RECAT F o desconocido
    return 'AT43'


def _coste_tabla(tipo_cook: str, delay_min: float, fase: str) -> float:
    """
    Cook & Tanner tactical cost (EUR, per flight, base scenario) of a delay.

    - delay < OTP threshold (15 min): 'primary' table. On-time flights cause no
      reactionary delay, so only the flight's own costs apply.
    - delay >= 15 min: 'full' table, which adds the reactionary costs.
    - Linear interpolation between breakpoints, from 0 EUR at 0 min.
    - Beyond 300 min the last segment is extended linearly, so the cost keeps
      growing and the optimiser never sees extra delay as free.
    """
    if delay_min <= 0:
        return 0.0
    alcance = 'primary' if delay_min < OTP_THRESHOLD_MIN else 'full'
    tabla = _COOK_TACTICAL[(fase, alcance)]
    costes = tabla.get(tipo_cook, tabla['A320'])
    x = [0] + _DELAY_BREAKPOINTS
    y = [0] + costes
    if delay_min >= x[-1]:
        pendiente = (y[-1] - y[-2]) / (x[-1] - x[-2])
        return y[-1] + pendiente * (delay_min - x[-1])
    for i in range(len(x) - 1):
        if delay_min <= x[i + 1]:
            t = (delay_min - x[i]) / (x[i + 1] - x[i])
            return y[i] + t * (y[i + 1] - y[i])
    return float(y[-1])


def factor_pasajeros(tipo_cook: str, asientos) -> float:
    """Real passengers (seats x European load factor) / Cook base passengers."""
    try:
        asientos = float(asientos)
    except (TypeError, ValueError):
        asientos = 180.0
    if not asientos > 0:
        asientos = 180.0
    pax_reales = max(int(asientos * LOAD_FACTOR_EU), 1)
    return pax_reales / _PAX_BASE_TABLE.get(tipo_cook, 130)


def coste_retraso_eur(atyp, recat, asientos, delay_min: float, fase: str = 'at_gate') -> float:
    """
    Cost of delay of one flight (EUR 2014): Cook & Tanner tactical cost for its
    reference aircraft type, scaled by its real passenger load.

    fase: 'at_gate' for ground delay (controlled flights held at the origin
          gate), 'en_route' for airborne delay (exempt flights holding).
    """
    tipo = _mapear_tipo_cook(atyp, recat, int(asientos) if pd.notna(asientos) else 0)
    return _coste_tabla(tipo, delay_min, fase) * factor_pasajeros(tipo, asientos)


def coste_tabla_vec(tipo_cook: str, delays, fase: str = 'at_gate'):
    """Vectorised _coste_tabla() for an array of delays (same rules)."""
    import numpy as np
    d = np.asarray(delays, dtype=float)
    x = np.array([0] + _DELAY_BREAKPOINTS, dtype=float)
    out = np.zeros_like(d)
    for alcance, mask in (('primary', (d > 0) & (d < OTP_THRESHOLD_MIN)),
                          ('full', d >= OTP_THRESHOLD_MIN)):
        tabla = _COOK_TACTICAL[(fase, alcance)]
        y = np.array([0] + tabla.get(tipo_cook, tabla['A320']), dtype=float)
        pendiente = (y[-1] - y[-2]) / (x[-1] - x[-2])
        vals = np.interp(d[mask], x, y)
        mas_alla = d[mask] > x[-1]
        vals[mas_alla] = y[-1] + pendiente * (d[mask][mas_alla] - x[-1])
        out[mask] = vals
    return out
