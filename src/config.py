# =============================================================================
# src/config.py
#all constant used in project
#
# import constants from another file:
#   from config import CFG, COST_AIR_MIN, ECAC_PREFIXES
# =============================================================================

import os
from dataclasses import dataclass

# =============================================================================
# INPUT FILES
# =============================================================================
# The real traffic sample (LEBL arrivals on 10 August 2025) was provided by the
# course and is not redistributed. When it is present locally it is used;
# otherwise the simulator falls back to the synthetic day in data/sample/.
# Set ATFM_TRAFFIC to use any other file with the same layout.

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_TRAFFIC = os.path.join(BASE_DIR, 'data', 'raw', 'LEBL_10AUG2025.csv')
SAMPLE_TRAFFIC = os.path.join(BASE_DIR, 'data', 'sample', 'synthetic_LEBL_day.csv')
FLEET_TABLE = os.path.join(BASE_DIR, 'data', 'raw', 'fleet_cat_seat.csv')
AIRPORT_TABLE = os.path.join(BASE_DIR, 'data', 'raw', 'airports.csv')


def traffic_file() -> str:
    """Path of the traffic CSV to simulate: $ATFM_TRAFFIC, the real day, or the synthetic one."""
    override = os.environ.get('ATFM_TRAFFIC')
    if override:
        return override
    return REAL_TRAFFIC if os.path.exists(REAL_TRAFFIC) else SAMPLE_TRAFFIC

# =============================================================================
# GPD parameters in LEBL (BCN EL PRAT)
# =============================================================================

@dataclass(frozen=True)
class AirportConfig:
    """
    Parameters of Ground Delay Program in LEBL.

    DATACLASS FROZEN:
        frozen=True values can't be changed once created. Avoids modifying it by 
        accident the AAR during the program execution.

    TIME UNITS:
        Minutes from midnight UTC.

    PARAMETERS (names as in the MGTA course, after the FAA GDP literature):
        AAR:             Airport Acceptance Rate: arrivals/hour accepted in
                         nominal conditions.
        PAAR:            (GDP) Program Airport Acceptance Rate: arrivals/hour
                         accepted while the GDP is active (here, low-visibility
                         procedures).
        H_START:         Hstart, start time of reduced capacity.
        H_END:           Hend, end time of reduced capacity.
        H_FILE_OFFSET:   Hstart - Hfile, i.e. how long before Hstart the GDP is
                         filed. Hfile (file time) is when the regulation is set
                         and the slots are sent to the airlines; flights already
                         flying at Hfile are exempt (ExemptFlying).
        GDP_RADIUS_KM:   Radius of exemption: flights from origins further away
                         are exempt (ExemptRadius).

        HNoReg (end time of the regulation, when the queue has dissolved) is
        not a parameter: it is computed from the Newell curves.
    """
    AAR:             int = 44    # arrivals/hour, nominal
    PAAR:            int = 20    # arrivals/hour, during the GDP
    H_START:         int = 360   # 06:00 UTC
    H_END:           int = 780   # 13:00 UTC
    H_FILE_OFFSET:   int = 150   # Hfile = Hstart - 150 min = 03:30 UTC
    GDP_RADIUS_KM:   int = 3000  # radius of exemption

    @property
    def H_FILE(self) -> int:
        """Hfile, the file time of the regulation (minutes from midnight UTC)."""
        return self.H_START - self.H_FILE_OFFSET

    @property
    def SLOT_NOM(self) -> float:
        """
        airport covers 44aircraft/h → 1 aircraft each 60/44 = 1.36 min.
        """
        return 60 / self.AAR

    @property
    def SLOT_RED(self) -> float:
        """
        Airport covers 20 aircraft/h → 1 aircraft each 60/20 = 3 min.
        """
        return 60 / self.PAAR

    def to_params_dict(self) -> dict:
        """
        Returns parameters that ejecutar_nucleo_gdp() will need.

        THIS METHOD:
            with this method, we gather all info in a unique place (this dictionary) 
            and if anything changes in the parameters, the function will read from here.

        USE:
            from config import CFG
            params = CFG.to_params_dict()
        """
        return {
            'H_START':  self.H_START,
            'H_END':    self.H_END,
            'AAR':      self.AAR,
            'PAAR':     self.PAAR,
            'SLOT_NOM': self.SLOT_NOM,
            'SLOT_RED': self.SLOT_RED,
        }


# =============================================================================
# OACI PREFIXES OF ECAC AIRPORTS
# =============================================================================
# ECAC: European Civil Aviation Conference
#
# First two characters of OACI idetify the region of procedence. The ones down are the EUROPE ones.
#
# Only flights that are taking off from ECAC can recieve CTOT.
# INTERNATIONAL are outside the European law.

ECAC_PREFIXES: tuple = tuple([
    'EB', 'ED', 'EE', 'EF', 'EG', 'EH', 'EI', 'EK', 'EL', 'EN', 'EP',
    'ES', 'ET', 'EV', 'EY',
    'LA', 'LB', 'LC', 'LD', 'LE', 'LF', 'LG', 'LH', 'LI', 'LJ', 'LK',
    'LM', 'LN', 'LO', 'LP', 'LQ', 'LR', 'LS', 'LT', 'LU', 'LW', 'LY', 'LZ',
    'BI', 'GC', 'GE', 'UD', 'UG', 'UK', 'UB',
])


# =============================================================================
# ECONOMIC CONSTANTS
# =============================================================================
# Source: University of Westminster / Eurocontrol,
#   "European airline delay cost reference values", v4.1, 2015.
#
# Cost of delay per minute in EUR
# In flight: The expensive. Fuel + crew + handling compensations.
COST_AIR_MIN: int = 100  

# On ground: Much cheaper. The whole point of GDP is to keep them
# parked instead of burning fuel in a holding pattern.
COST_GND_MIN: int = 35

# =============================================================================
# CO2 EMISSION MODEL
# =============================================================================
#
# Emissions aren't handcoded. We use the Montlaur (2025) model https://doi.org/10.3390/app15179688
# based on flight distance + seat count. 
# Logic is in src/emissions_fuel_model.py and runs during Phase 1.

# =============================================================================
# FLIGHT_STATUS TAGS 
# =============================================================================
# Use these constants instead of raw strings to filter df['flight_status'].
# Because typos in strings (like 'GPD' instead of 'GDP') fail usually.
# Using constants triggers a NameError, which is way easier to debug.

#
# The labels are the flight-status outputs suggested in the course (WP2,
# "determine flight status"). The delivered project used its own labels
# (GDP CANDIDATE, EXEMPT AIRBORNE, EXEMPT DISTANCE, EXEMPT INTERNATIONAL).

FS_NOT_AFFECTED         = 'NotAffected'          # ETA before Hstart or at/after HNoReg
FS_CONTROLLED           = 'Controlled'           # receives ground delay (CTD/CTA)
FS_EXEMPT_INTERNATIONAL = 'ExemptInternational'  # origin outside the ECAC area
FS_EXEMPT_FLYING        = 'ExemptFlying'         # already flying at Hfile
FS_EXEMPT_RADIUS        = 'ExemptRadius'         # origin beyond the radius of exemption


# =============================================================================
# GLOBAL INSTANCE
# =============================================================================
# Single AirportConfig instance to be shared across all modules.
# Import this directly: from config import CFG
CFG = AirportConfig()