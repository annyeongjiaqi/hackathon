"""Resolve Singapore postcodes and choose the nearest bundled FairPrice outlet."""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

import requests

FAIRPRICE_ADDRESS_URL = "https://public-api.omni.fairprice.com.sg/address/search"


def geocode_postal_code(postal_code: str, timeout: float = 5) -> tuple[float, float] | None:
    postal = postal_code.strip()
    if len(postal) != 6 or not postal.isdigit():
        return None
    response = requests.get(
        FAIRPRICE_ADDRESS_URL,
        params={"type": "addresses", "service": "all", "addressesLimit": 5, "term": postal},
        timeout=timeout,
    )
    response.raise_for_status()
    addresses = response.json().get("data", {}).get("addresses", [])
    exact = next((item for item in addresses if str(item.get("postcode")) == postal), None)
    address = exact or (addresses[0] if addresses else None)
    return (float(address["lat"]), float(address["long"])) if address else None


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    value = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * radius * asin(sqrt(value))


def nearest_store(stores: list[dict], latitude: float, longitude: float) -> tuple[dict, float]:
    if not stores:
        raise ValueError("No FairPrice outlets are available")
    store = min(stores, key=lambda item: distance_km(latitude, longitude, float(item["latitude"]), float(item["longitude"])))
    return store, round(distance_km(latitude, longitude, float(store["latitude"]), float(store["longitude"])), 2)
