"""Decode basic facts from a VIN: manufacturer, country and model year.

Only the parts that are standardized for every vehicle are decoded (ISO 3779 / US 49 CFR 565):
the World Manufacturer Identifier (characters 1-3) and the model-year code (character 10).
The exact model and engine need the manufacturer's own tables, so they are not guessed.
"""

from __future__ import annotations

import datetime

# Model-year codes, character 10: 1980-2009, then the cycle repeats for 2010-2039
YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"

WMI_MAKES = {
    # GM
    "1G1": "Chevrolet", "1GC": "Chevrolet", "1GN": "Chevrolet", "2G1": "Chevrolet", "3G1": "Chevrolet",
    "1GB": "Chevrolet", "3GN": "Chevrolet", "3GC": "Chevrolet", "1GT": "GMC", "1GK": "GMC", "3GT": "GMC",
    "1G4": "Buick", "2G4": "Buick", "1G6": "Cadillac", "1GY": "Cadillac", "KL": "Chevrolet / Holden (GM Korea)",
    "W0L": "Opel / Vauxhall / Holden (Opel-built)", "W0V": "Opel / Vauxhall / Holden (Opel-built)",
    # Holden (GM Australia) and Holden-built exports
    "6G1": "Holden", "6H8": "Holden", "6G2": "Pontiac (Holden-built)", "6G3": "Chevrolet (Holden-built)",
    # Ford group
    "1FA": "Ford", "1FM": "Ford", "1FT": "Ford", "1FD": "Ford", "1FB": "Ford", "2FM": "Ford", "3FA": "Ford",
    "3FM": "Ford", "WF0": "Ford (Europe)", "1ZV": "Ford", "1LN": "Lincoln", "5LM": "Lincoln", "3LN": "Lincoln",
    "JM1": "Mazda", "JM3": "Mazda", "4F2": "Mazda", "4F4": "Mazda",
    # Toyota group / Subaru
    "JTH": "Lexus", "JTJ": "Lexus", "2T2": "Lexus", "JT": "Toyota", "4T1": "Toyota", "4T3": "Toyota",
    "5TD": "Toyota", "5TF": "Toyota", "2T1": "Toyota", "2T3": "Toyota", "5YF": "Toyota",
    "JF1": "Subaru", "JF2": "Subaru", "4S3": "Subaru", "4S4": "Subaru",
    # Honda
    "JHM": "Honda", "1HG": "Honda", "2HG": "Honda", "5FN": "Honda", "5J6": "Honda", "SHH": "Honda",
    "JH4": "Acura", "19U": "Acura", "5J8": "Acura",
    # Hyundai / Kia
    "KMH": "Hyundai", "5NP": "Hyundai", "KM8": "Hyundai", "KNA": "Kia", "KND": "Kia", "5XY": "Kia", "3KP": "Kia",
    # VW group
    "WVW": "Volkswagen", "WVG": "Volkswagen", "3VW": "Volkswagen", "1VW": "Volkswagen", "WV1": "Volkswagen",
    "WV2": "Volkswagen", "WAU": "Audi", "WA1": "Audi", "TRU": "Audi", "TMB": "Skoda", "VSS": "Seat",
    "WP0": "Porsche", "WP1": "Porsche",
    # Nissan
    "JNK": "Infiniti", "5N3": "Infiniti", "JN": "Nissan", "1N4": "Nissan", "1N6": "Nissan", "3N1": "Nissan",
    "5N1": "Nissan", "3N6": "Nissan",
    # BMW / MINI / Mercedes
    "WBA": "BMW", "WBS": "BMW M", "WBY": "BMW i", "4US": "BMW", "5UX": "BMW", "5UM": "BMW", "5YM": "BMW",
    "WMW": "MINI", "WDD": "Mercedes-Benz", "WDB": "Mercedes-Benz", "WDC": "Mercedes-Benz", "W1K": "Mercedes-Benz",
    "W1N": "Mercedes-Benz", "4JG": "Mercedes-Benz",
    # Stellantis (Chrysler / Dodge / Jeep / Ram) and Europe
    "1C3": "Chrysler", "2C3": "Chrysler", "1C4": "Jeep / Chrysler", "1J4": "Jeep", "1J8": "Jeep",
    "1C6": "Ram", "3C6": "Ram", "1B3": "Dodge", "2B3": "Dodge", "2C4": "Chrysler / Dodge", "3C4": "Chrysler",
    "ZFA": "Fiat", "ZAR": "Alfa Romeo", "VF1": "Renault", "VF3": "Peugeot", "VF7": "Citroën",
    # Others
    "SAL": "Land Rover", "SAJ": "Jaguar", "YV1": "Volvo", "YV4": "Volvo", "JS": "Suzuki",
    "JA3": "Mitsubishi", "JA4": "Mitsubishi", "5YJ": "Tesla", "7SA": "Tesla",
}

COUNTRIES = {
    "1": "USA", "4": "USA", "5": "USA", "2": "Canada", "3": "Mexico", "J": "Japan", "K": "South Korea",
    "W": "Germany", "S": "United Kingdom", "Z": "Italy", "Y": "Sweden / Finland", "L": "China",
    "T": "Czech Republic / Switzerland", "M": "India / Indonesia / Thailand", "7": "New Zealand", "V": "France / Spain", "9": "Brazil", "6": "Australia",
}


def make(vin: str | None) -> str | None:
    if not vin or len(vin) < 3:
        return None
    v = vin.upper()
    return WMI_MAKES.get(v[:3]) or WMI_MAKES.get(v[:2])


def model_year(vin: str | None) -> int | None:
    if not vin or len(vin) != 17:
        return None
    v = vin.upper()
    idx = YEAR_CODES.find(v[9])
    if idx < 0:
        return None
    early, late = 1980 + idx, 2010 + idx
    this_year = datetime.date.today().year
    if v[0] in "12345":
        # North America: a letter in position 7 means 2010 or later, a digit means 1980-2009
        year = late if v[6].isalpha() else early
    else:
        year = late if late <= this_year + 1 else early
    return year if year <= this_year + 1 else early


def decode(vin: str | None) -> dict:
    v = (vin or "").upper()
    return {"make": make(v), "year": model_year(v), "country": COUNTRIES.get(v[:1]) if v else None}


def describe(vin: str | None) -> str | None:
    """'2012 Ford (USA)' or None."""
    d = decode(vin)
    if not (d["make"] or d["year"]):
        return None
    parts = " ".join(str(p) for p in (d["year"], d["make"]) if p)
    return f"{parts} ({d['country']})" if d["country"] else parts
