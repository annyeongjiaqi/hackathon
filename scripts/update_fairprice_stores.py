"""Build the offline FairPrice outlet catalogue from FairPrice's store API."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

API_URL = "https://public-api.omni.fairprice.com.sg/stores"
PHYSICAL_TYPES = {"FairPrice", "Fairprice", "FairPrice Finest", "FairPrice Xtra"}


def normalize(payload: dict) -> list[dict]:
    stores = payload.get("data", {}).get("fpstores", [])
    result = []
    for store in stores:
        name = str(store.get("name", "")).strip()
        store_type = str(store.get("storeType", "")).strip()
        if store.get("active") != 1 or store_type not in PHYSICAL_TYPES:
            continue
        if "click and collect" in name.lower():
            continue
        postal_code = str(store.get("postalCode", "")).strip()
        if postal_code.isdigit():
            postal_code = postal_code.zfill(6)
        result.append({
            "id": str(store.get("id", "")),
            "name": name,
            "type": "FairPrice" if store_type == "Fairprice" else store_type,
            "postal_code": postal_code,
            "address": str(store.get("address", "")).strip(),
            "zone": str(store.get("zoneId", "")).strip(),
            "latitude": store.get("lat"),
            "longitude": store.get("long"),
            "is_24_hour": bool(store.get("is24Hour", False)),
        })
    return sorted(result, key=lambda item: (item["type"], item["name"], item["postal_code"]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, help="Use a downloaded API response instead of fetching")
    parser.add_argument("--output", type=Path, default=Path("agent/data/fairprice_stores.json"))
    args = parser.parse_args()
    if args.source:
        payload = json.loads(args.source.read_text())
    else:
        request = Request(API_URL, headers={"User-Agent": "meal-planner-store-catalogue/1.0"})
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    stores = normalize(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(stores, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(stores)} physical outlets to {args.output}")


if __name__ == "__main__":
    main()
