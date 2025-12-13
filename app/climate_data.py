"""
Climate data module for accessing NOAA climate normals and temperature records.

This module provides functions to:
- Find the nearest NOAA weather station for a given lat/lon
- Load daily temperature normals (1991-2020) for a station
- Query normal high/low temperatures by date
"""

import csv
import logging
import math
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# Path to climate data directory
DATA_DIR = Path(__file__).parent / "data"
INVENTORY_FILE = DATA_DIR / "inventory_30yr.txt"
NORMALS_CACHE_DIR = DATA_DIR / "normals_cache"

# Ensure cache directory exists
NORMALS_CACHE_DIR.mkdir(exist_ok=True)


def _haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance between two points on Earth (in km).

    Args:
        lat1, lon1: First point coordinates
        lat2, lon2: Second point coordinates

    Returns:
        Distance in kilometers
    """
    # Ensure inputs are floats (they might be strings from config)
    lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)

    # Convert to radians
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])

    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))

    # Radius of Earth in kilometers
    r = 6371

    return c * r


def find_nearest_station(lat, lon, max_distance_km=100):
    """
    Find the nearest NOAA climate normals station to the given coordinates.

    Args:
        lat: Latitude
        lon: Longitude
        max_distance_km: Maximum acceptable distance in km (default 100)

    Returns:
        Tuple of (station_id, station_name, distance_km) or None if no station found
    """
    if not INVENTORY_FILE.exists():
        logger.error(f"Inventory file not found: {INVENTORY_FILE}")
        return None

    nearest_station = None
    min_distance = float("inf")

    with open(INVENTORY_FILE) as f:
        for line in f:
            # Parse fixed-width format
            station_id = line[0:11].strip()
            try:
                station_lat = float(line[12:20].strip())
                station_lon = float(line[21:30].strip())
                # Station name starts around column 40
                station_name = line[40:].strip()
            except (ValueError, IndexError):
                continue

            # Calculate distance
            distance = _haversine_distance(lat, lon, station_lat, station_lon)

            if distance < min_distance:
                min_distance = distance
                nearest_station = (station_id, station_name, distance)

    if nearest_station and min_distance <= max_distance_km:
        logger.info(
            f"Found nearest station: {nearest_station[0]} ({nearest_station[1]}) at {min_distance:.1f} km"
        )
        return nearest_station
    else:
        logger.warning(f"No station found within {max_distance_km} km of ({lat}, {lon})")
        return None


def _download_station_normals(station_id):
    """
    Download climate normals data for a specific station.

    Args:
        station_id: NOAA station identifier

    Returns:
        Path to downloaded file or None on error
    """
    cache_file = NORMALS_CACHE_DIR / f"{station_id}.csv"

    # Check if already cached
    if cache_file.exists():
        logger.debug(f"Using cached normals for {station_id}")
        return cache_file

    # Download from NOAA
    url = f"https://www.ncei.noaa.gov/data/normals-daily/1991-2020/access/{station_id}.csv"
    logger.info(f"Downloading normals from {url}")

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        # Save to cache
        with open(cache_file, "wb") as f:
            f.write(response.content)

        logger.info(f"Downloaded normals for {station_id}")
        return cache_file

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to download normals for {station_id}: {e}")
        return None


def load_station_normals(station_id):
    """
    Load climate normals data for a station.

    Args:
        station_id: NOAA station identifier

    Returns:
        Dictionary mapping (month, day) tuples to dict with 'tmax_normal' and 'tmin_normal'
        Returns None on error
    """
    # Download/get cached file
    normals_file = _download_station_normals(station_id)
    if not normals_file:
        return None

    # Parse CSV
    normals = {}

    try:
        with open(normals_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Parse date (MM-DD format)
                date_str = row.get("DATE", "")
                if not date_str:
                    continue

                try:
                    month, day = map(int, date_str.split("-"))
                except (ValueError, AttributeError):
                    continue

                # Get temperature normals (already in degrees F)
                tmax_str = row.get("DLY-TMAX-NORMAL", "")
                tmin_str = row.get("DLY-TMIN-NORMAL", "")

                # Parse temperature values
                tmax_normal = None
                tmin_normal = None

                try:
                    if tmax_str and tmax_str.strip() and tmax_str.strip() != "-9999.0":
                        tmax_normal = float(tmax_str)
                except ValueError:
                    pass

                try:
                    if tmin_str and tmin_str.strip() and tmin_str.strip() != "-9999.0":
                        tmin_normal = float(tmin_str)
                except ValueError:
                    pass

                normals[(month, day)] = {"tmax_normal": tmax_normal, "tmin_normal": tmin_normal}

        logger.info(f"Loaded {len(normals)} daily normals for {station_id}")
        return normals

    except Exception as e:
        logger.error(f"Failed to parse normals file for {station_id}: {e}")
        return None


def get_normals_for_date(normals, dt):
    """
    Get temperature normals for a specific date.

    Args:
        normals: Dictionary from load_station_normals()
        dt: datetime.date or datetime.datetime object

    Returns:
        Dictionary with 'tmax_normal' and 'tmin_normal' or None if not available
    """
    if normals is None:
        return None

    key = (dt.month, dt.day)
    return normals.get(key)


def get_normals_for_location(lat, lon, max_stations_to_try=10):
    """
    Convenience function to get climate normals for a location.

    Tries multiple nearby stations until one with temperature data is found.

    Args:
        lat: Latitude
        lon: Longitude
        max_stations_to_try: Maximum number of stations to check (default 10)

    Returns:
        Tuple of (normals_dict, station_info) or (None, None) on error
        where station_info is (station_id, station_name, distance_km)
    """
    if not INVENTORY_FILE.exists():
        logger.error(f"Inventory file not found: {INVENTORY_FILE}")
        return None, None

    # Get all stations sorted by distance
    stations_by_distance = []
    with open(INVENTORY_FILE) as f:
        for line in f:
            station_id = line[0:11].strip()
            try:
                station_lat = float(line[12:20].strip())
                station_lon = float(line[21:30].strip())
                station_name = line[40:].strip()
            except (ValueError, IndexError):
                continue

            distance = _haversine_distance(lat, lon, station_lat, station_lon)
            if distance <= 100:  # Within 100km
                stations_by_distance.append((station_id, station_name, distance))

    # Sort by distance
    stations_by_distance.sort(key=lambda x: x[2])

    # Try stations until we find one with temperature data
    for station_id, station_name, distance in stations_by_distance[:max_stations_to_try]:
        normals = load_station_normals(station_id)
        if normals:
            # Check if this station has temperature data
            # Look at first day's data
            first_day_normals = next(iter(normals.values()))
            if first_day_normals.get("tmax_normal") is not None:
                station_info = (station_id, station_name, distance)
                logger.info(f"Found station with temp data: {station_id} ({distance:.1f} km away)")
                return normals, station_info

    logger.warning(f"No station with temperature data found within 100km of ({lat}, {lon})")
    return None, None
