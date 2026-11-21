"""Build data/raw/airports.csv: coordinates of every airport in the traffic file.

The coordinates come from OurAirports (https://ourairports.com/data/), which is
in the public domain. Only the airports that appear in the traffic sample are
kept, so the table stays small enough to version.

Usage (from the repository root):
    curl -L -o airports_full.csv https://davidmegginson.github.io/ourairports-data/airports.csv
    python tools/build_airport_table.py airports_full.csv
"""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TRAFFIC = ROOT / "data" / "raw" / "LEBL_10AUG2025.csv"
OUTPUT = ROOT / "data" / "raw" / "airports.csv"


def main(ourairports_csv: str) -> None:
    traffic = pd.read_csv(TRAFFIC, sep=";")
    needed = sorted(set(traffic["ADEP"]) | set(traffic["ADES"]))

    oa = pd.read_csv(
        ourairports_csv,
        usecols=["ident", "icao_code", "gps_code", "name", "latitude_deg", "longitude_deg"],
        keep_default_na=False,
    )
    # An ICAO code may live in any of these columns; prefer the explicit one.
    rows = []
    for code in needed:
        match = pd.DataFrame()
        for col in ("icao_code", "ident", "gps_code"):
            match = oa[oa[col] == code]
            if not match.empty:
                break
        if match.empty:
            raise SystemExit(f"No coordinates found in OurAirports for {code}")
        r = match.iloc[0]
        rows.append(
            {
                "icao": code,
                "name": r["name"],
                "lat_deg": round(float(r["latitude_deg"]), 6),
                "lon_deg": round(float(r["longitude_deg"]), 6),
            }
        )

    pd.DataFrame(rows).to_csv(OUTPUT, sep=";", index=False)
    print(f"Wrote {len(rows)} airports to {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
