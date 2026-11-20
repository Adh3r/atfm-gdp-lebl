"""Generate a synthetic day of LEBL traffic with the same layout as the real file.

The real traffic sample used in the course project is not redistributed (see
CREDITS.md). This script builds a stand-in so that anyone can run the
simulator and the tests. It is generated from scratch with a fixed seed: the
origins are drawn from data/raw/airports.csv, the aircraft types from the fleet
table, and arrival times from a generic summer-day profile; most aircraft then
depart again from LEBL after a random turnaround. No flight is copied from the
real data.

Usage (from the repository root):
    python tools/make_synthetic_traffic.py            # writes data/sample/synthetic_LEBL_day.csv
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
AIRPORTS = ROOT / "data" / "raw" / "airports.csv"
OUTPUT = ROOT / "data" / "sample" / "synthetic_LEBL_day.csv"

SEED = 2026
N_ARRIVALS = 540

# Generic arrival profile (relative weight per UTC hour): quiet night, a
# morning wave and a busy day. Demand in 06-13 UTC must exceed the reduced
# capacity of 20 arrivals/h so that a GDP is meaningful.
HOURLY_PROFILE = [2, 1, 1, 1, 2, 8, 30, 34, 36, 34, 32, 30,
                  32, 33, 34, 33, 32, 30, 28, 26, 22, 16, 10, 5]

AIRLINES = ['VLG', 'RYR', 'EZY', 'IBE', 'DLH', 'AFR', 'BAW', 'KLM', 'TAP', 'SWR']
SHORT_HAUL = ['A320', 'A20N', 'A321', 'A21N', 'B738', 'B38M', 'A319', 'E190']
LONG_HAUL = ['A332', 'A333', 'A359', 'B789', 'B77W', 'B763']
LONG_HAUL_AIRLINES = ['DAL', 'UAL', 'QTR', 'UAE', 'AAL']
BUSINESS_JETS = ['PC24', 'E55P']

LEBL = (41.2971, 2.07846)


def _great_circle_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def _minutes(hhmmss: str) -> int:
    h, m, _ = (int(x) for x in hhmmss.split(':'))
    return h * 60 + m


def _hhmmss(minutes: float) -> str:
    m = int(round(minutes)) % 1440
    return f"{m // 60}:{m % 60:02d}:00"


def main() -> None:
    rng = np.random.default_rng(SEED)
    ap = pd.read_csv(AIRPORTS, sep=';')
    ap = ap[ap['icao'] != 'LEBL'].copy()
    ap['dist_km'] = _great_circle_km(LEBL[0], LEBL[1], ap['lat_deg'], ap['lon_deg'])
    # Nearer airports are more frequent origins.
    weights = 1.0 / (1.0 + ap['dist_km'] / 800.0) ** 2
    weights /= weights.sum()

    hours = rng.choice(24, size=N_ARRIVALS, p=np.array(HOURLY_PROFILE) / sum(HOURLY_PROFILE))
    eta = np.sort(hours * 60 + rng.integers(0, 60, size=N_ARRIVALS))
    origins = ap.sample(n=N_ARRIVALS, replace=True, weights=weights, random_state=SEED)

    rows = []
    for i, (t, (_, o)) in enumerate(zip(eta, origins.iterrows())):
        long_haul = o['dist_km'] > 3500
        if long_haul:
            airline, atyp = rng.choice(LONG_HAUL_AIRLINES), rng.choice(LONG_HAUL)
            arcid = f"{airline}{rng.integers(10, 999)}"
        elif rng.random() < 0.02:
            atyp = rng.choice(BUSINESS_JETS)
            arcid = f"D{''.join(rng.choice(list('ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 4))}"
        else:
            airline = rng.choice(AIRLINES, p=[0.35, 0.15, 0.08, 0.08, 0.06, 0.06, 0.06, 0.06, 0.05, 0.05])
            atyp = rng.choice(SHORT_HAUL)
            arcid = f"{airline}{rng.integers(100, 9999)}"
        block_min = o['dist_km'] / (780 / 60) + 25 + rng.normal(0, 4)
        rows.append({
            'ARCID': arcid,
            'ATYP': atyp,
            'ADEP': o['icao'],
            'ADES': 'LEBL',
            'RM': f"SYN{i:04d}",
            'ARF': int(rng.choice([340, 360, 380, 400])),
            'ETD': _hhmmss(t - block_min),
            'TT': int(rng.integers(8, 20)),
            'ETA': _hhmmss(t),
        })

    # Next departure of the same aircraft (same RM), so that turnarounds and
    # reactionary delay can be computed. A separate generator keeps the
    # arrivals identical to earlier versions of the file. About 9 in 10
    # aircraft rotate the same day; scheduled ground time is 40-200 min
    # (median about 80 min) for narrow-bodies and 100-240 min for wide-bodies.
    rng_dep = np.random.default_rng(SEED + 1)
    departures = []
    for r in rows:
        if rng_dep.random() > 0.9:
            continue
        wide = r['ATYP'] in LONG_HAUL
        tat = rng_dep.uniform(100, 240) if wide else 40 + rng_dep.gamma(2.0, 20.0)
        etd = _minutes(r['ETA']) + round(min(tat, 200 if not wide else 240))
        if etd >= 24 * 60:
            continue
        block = _minutes(r['ETA']) - _minutes(r['ETD'])
        block = block + 1440 if block <= 0 else block
        departures.append({
            'ARCID': r['ARCID'][:3] + str(int(rng_dep.integers(100, 9999))) if r['ARCID'][:3].isalpha() else r['ARCID'],
            'ATYP': r['ATYP'], 'ADEP': 'LEBL', 'ADES': r['ADEP'], 'RM': r['RM'], 'ARF': r['ARF'],
            'ETD': _hhmmss(etd), 'TT': int(rng_dep.integers(8, 20)), 'ETA': _hhmmss(etd + block),
        })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows + departures).to_csv(OUTPUT, sep=';', index=False)
    print(f"Wrote {len(rows)} synthetic arrivals and {len(departures)} departures "
          f"to {OUTPUT.relative_to(ROOT)}")


if __name__ == '__main__':
    main()
