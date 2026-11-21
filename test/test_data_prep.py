"""Tests for WP1 data preparation (src/lib_data_prep.py)."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

import config  # noqa: E402
import lib_data_prep as prep  # noqa: E402

FLIGHTS = config.REAL_TRAFFIC
FLEET = config.FLEET_TABLE


# -----------------------------------------------------------------------------
# parse_time_to_minutes
# -----------------------------------------------------------------------------

@pytest.mark.parametrize('text, minutes', [
    ('06:30', 390.0),
    ('06:30:45', 390.75),
    ('00:00', 0.0),
    ('23:59', 1439.0),
    ('0:05:00', 5.0),
    ('15', 15.0),
])
def test_parse_valid_times(text, minutes):
    assert prep.parse_time_to_minutes(text) == pytest.approx(minutes)


@pytest.mark.parametrize('bad', [None, float('nan'), 'N/A', '', '12:ab', '1:2:3:4'])
def test_parse_invalid_times_return_nan(bad):
    assert np.isnan(prep.parse_time_to_minutes(bad))


# -----------------------------------------------------------------------------
# great_circle_km
# -----------------------------------------------------------------------------

def test_great_circle_zero_and_symmetry():
    assert prep.great_circle_km(41.3, 2.08, 41.3, 2.08) == pytest.approx(0.0)
    d1 = prep.great_circle_km(41.2971, 2.07846, 40.4934, -3.5722)
    d2 = prep.great_circle_km(40.4934, -3.5722, 41.2971, 2.07846)
    assert d1 == pytest.approx(d2)


def test_great_circle_known_distances():
    # LEBL-LEMD is 261 NM (483 km); a quarter of the equator is pi/2 * R.
    assert prep.great_circle_km(41.2971, 2.07846, 40.4934, -3.5722) == pytest.approx(483, abs=2)
    assert prep.great_circle_km(0, 0, 0, 90) == pytest.approx(np.pi / 2 * prep.EARTH_RADIUS_KM)


# -----------------------------------------------------------------------------
# prepare_flights on small synthetic inputs
# -----------------------------------------------------------------------------

def _write_inputs(tmp_path, flights_rows, airports_rows=None):
    flights = tmp_path / 'flights.csv'
    pd.DataFrame(flights_rows, columns=['ARCID', 'ATYP', 'ADEP', 'ADES', 'RM', 'ARF', 'ETD', 'TT', 'ETA']) \
        .to_csv(flights, sep=';', index=False)
    fleet = tmp_path / 'fleet.csv'
    # Same layout as the real file: unnamed index column and trailing blank rows.
    fleet.write_text(';f;recat;size_seats_avg\n1;A320;D;173\n2;GA5C;F;8\n;;;\n;;;\n', encoding='utf8')
    airports = tmp_path / 'airports.csv'
    rows = airports_rows or [('LEBL', 'Barcelona', 41.2971, 2.07846),
                             ('LEMD', 'Madrid', 40.493407, -3.572249),
                             ('KJFK', 'New York JFK', 40.639801, -73.7789)]
    pd.DataFrame(rows, columns=['icao', 'name', 'lat_deg', 'lon_deg']).to_csv(airports, sep=';', index=False)
    return str(flights), str(fleet), str(airports)


SYNTHETIC = [
    ('VLG1001', 'A320', 'LEMD', 'LEBL', 'ECABC', 360, '07:00:00', '12', '08:10:00'),
    ('VLG1002', 'A320', 'LEBL', 'LEMD', 'ECABD', 360, '07:30:00', '10', '08:40:00'),  # departure
    ('N818CA',  'GA5C', 'LEMD', 'LEBL', 'N818CA', 410, '06:00:00', '8', '07:05:00'),
    ('DAL110',  'A320', 'KJFK', 'LEBL', 'N123DL', 370, '23:30:00', '15', '06:45:00'),  # overnight
]


def test_synthetic_arrivals_are_prepared(tmp_path):
    df = prep.prepare_flights(*_write_inputs(tmp_path, SYNTHETIC)).set_index('ARCID')
    assert list(df.index) == ['DAL110', 'N818CA', 'VLG1001']          # arrivals only, by ETA
    assert df.loc['N818CA', 'airline'] == prep.GENERAL_AVIATION
    assert df.loc['VLG1001', 'airline'] == 'VLG'
    assert not df.loc['DAL110', 'is_ecac']
    assert df.loc['VLG1001', 'is_ecac']
    assert df.loc['DAL110', 'block_time_min'] == 435                 # crosses midnight
    assert df.loc[['N818CA', 'VLG1001'], 'distance_km'].tolist() == pytest.approx([483, 483], abs=2)
    assert (df['flight_co2_kg'] > 0).all()


def test_missing_airport_coordinates_raise(tmp_path):
    paths = _write_inputs(tmp_path, SYNTHETIC, airports_rows=[('LEBL', 'Barcelona', 41.2971, 2.07846)])
    with pytest.raises(ValueError, match='Missing coordinates'):
        prep.prepare_flights(*paths)


def test_unparseable_time_raises(tmp_path):
    rows = SYNTHETIC + [('VLG1003', 'A320', 'LEMD', 'LEBL', 'ECABE', 360, '07:00:00', '12', 'late')]
    with pytest.raises(ValueError, match='Unparseable ETA'):
        prep.prepare_flights(*_write_inputs(tmp_path, rows))


# -----------------------------------------------------------------------------
# prepare_flights on the synthetic sample day (always available)
# -----------------------------------------------------------------------------

@pytest.fixture(scope='module')
def sample():
    return prep.prepare_flights(config.SAMPLE_TRAFFIC, FLEET)


def test_sample_day_is_complete(sample):
    assert len(sample) == 540
    assert sample['minutes_eta'].is_monotonic_increasing
    assert sample[['recat', 'size_seats_avg']].notna().all().all()
    assert (sample['distance_km'] > 50).all()
    assert (sample['flight_co2_kg'] > 0).all()


def test_sample_day_overloads_the_reduced_capacity(sample):
    in_window = sample[(sample['minutes_eta'] >= 360) & (sample['minutes_eta'] < 780)]
    assert len(in_window) / 7 > 20   # more than the 20 arrivals/h available under LVP


# -----------------------------------------------------------------------------
# prepare_flights on the real LEBL day (skipped when the file is not present)
# -----------------------------------------------------------------------------

@pytest.fixture(scope='module')
def lebl():
    if not os.path.exists(FLIGHTS):
        pytest.skip('real traffic file not available (not redistributed)')
    return prep.prepare_flights(FLIGHTS, FLEET)


def test_real_day_shape(lebl):
    raw = pd.read_csv(FLIGHTS, sep=';')
    expected = ((raw['ADES'] == 'LEBL') & (raw['ADEP'] != 'LEBL')).sum()
    assert len(lebl) == expected == 550
    assert lebl['minutes_eta'].is_monotonic_increasing


def test_real_day_every_type_has_fleet_data(lebl):
    assert lebl['recat'].notna().all()
    assert lebl['size_seats_avg'].notna().all()


def test_real_day_distances_are_plausible(lebl):
    assert (lebl['distance_km'] > 50).all()
    palma = lebl.loc[lebl['ADEP'] == 'LEPA', 'distance_km']
    assert palma.round().unique().tolist() == [202]


def test_real_day_co2_positive(lebl):
    assert (lebl['flight_co2_kg'] > 0).all()


def test_real_day_ecac_tagging(lebl):
    by_origin = lebl.drop_duplicates('ADEP').set_index('ADEP')['is_ecac']
    assert by_origin['LEPA'] and by_origin['EGKK'] and by_origin['GCLP']
    assert not by_origin['KJFK']
