# =============================================================================
# src/config.py
# Project-wide constants: input files, GDP parameters, ECAC prefixes and
# flight-status labels.
#
#   from config import CFG, ECAC_PREFIXES
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
# GDP parameters at LEBL (Barcelona-El Prat)
# =============================================================================

@dataclass(frozen=True)
class AirportConfig:
    """
    Parameters of the Ground Delay Program at LEBL.

    frozen=True: the values cannot be changed once created, so no module can
    modify, say, the AAR by accident during a run.

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
        READY_MARGIN:    flights due to depart less than this after Hfile are
                         considered ready to depart (boarded, doors closed) and
                         exempt as ExemptFlying. The course suggests 30 min
                         ("consider a margin, 30 min for example"); the
                         delivered project used 0.

        HNoReg (end time of the regulation, when the queue has dissolved) is
        not a parameter: it is computed from the Newell curves.
    """
    AAR:             int = 44    # arrivals/hour, nominal
    PAAR:            int = 20    # arrivals/hour, during the GDP
    H_START:         int = 360   # 06:00 UTC
    H_END:           int = 780   # 13:00 UTC
    H_FILE_OFFSET:   int = 150   # Hfile = Hstart - 150 min = 03:30 UTC
    GDP_RADIUS_KM:   int = 3000  # radius of exemption
    READY_MARGIN:    int = 30    # min after Hfile: flights ready to depart are exempt

    @property
    def H_FILE(self) -> int:
        """Hfile, the file time of the regulation (minutes from midnight UTC)."""
        return self.H_START - self.H_FILE_OFFSET

    @property
    def SLOT_NOM(self) -> float:
        """
        Nominal slot interval: AAR 44 arrivals/h -> one slot every 60/44 = 1.36 min.
        """
        return 60 / self.AAR

    @property
    def SLOT_RED(self) -> float:
        """
        Reduced slot interval: PAAR 20 arrivals/h -> one slot every 60/20 = 3 min.
        """
        return 60 / self.PAAR

    def to_params_dict(self) -> dict:
        """
        Returns parameters that run_gdp() will need.

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
# ICAO LOCATION-INDICATOR PREFIXES OF THE ECAC STATES
# =============================================================================
# ECAC: European Civil Aviation Conference (44 member states). The first two
# letters of an ICAO airport code identify the state (LE = Spain, with GC for
# the Canary Islands and GE for Ceuta and Melilla).
#
# Following the course, a flight from outside the ECAC area is exempt
# (ExemptInternational): in the European ATFM system, flights departing from
# outside the area where the Network Manager issues slots (CTOTs) cannot be
# held on the ground by a regulation.

ECAC_PREFIXES: tuple = tuple([
    'EB', 'ED', 'EE', 'EF', 'EG', 'EH', 'EI', 'EK', 'EL', 'EN', 'EP',
    'ES', 'ET', 'EV', 'EY',
    'LA', 'LB', 'LC', 'LD', 'LE', 'LF', 'LG', 'LH', 'LI', 'LJ', 'LK',
    'LM', 'LN', 'LO', 'LP', 'LQ', 'LR', 'LS', 'LT', 'LU', 'LW', 'LY', 'LZ',
    'BI', 'GC', 'GE', 'UD', 'UG', 'UK', 'UB',
])


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