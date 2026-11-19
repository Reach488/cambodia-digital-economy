"""
fetch_data.py
=============
Download, validate and save World Bank data for the
"Cambodia Digital Economy & Cyber Readiness Dashboard".

What this script does
---------------------
1. Requests 8 indicators for Cambodia, Vietnam, Myanmar and Laos from the
   World Bank Indicators API (REST + JSON), from 2000 to the latest year the
   API has.
2. Converts the JSON records into a tidy (long-format) pandas DataFrame:
   one row = one country + one year + one indicator.
3. Validates the data (country codes, indicator codes, years, duplicates,
   data types, plausible value ranges).
4. Saves the dataset to data/cambodia_econ.csv and the retrieval details
   (date/time, source, latest year per indicator and country) to
   data/metadata.json.

Missing observations stay missing (NaN in pandas, an empty cell in the CSV).
Nothing is estimated, filled in, or replaced with zero.

Usage (from the project folder, with the virtual environment active):
    python fetch_data.py
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE_URL = "https://api.worldbank.org/v2"
WDI_SOURCE_ID = 2  # 2 = World Development Indicators database
START_YEAR = 2000
REQUEST_TIMEOUT_SECONDS = 120  # this API can take 30-60 seconds to answer
MAX_ATTEMPTS = 3  # first try + 2 retries for network or server errors

# ISO3 country code -> name used in the dataset.
# The World Bank's official names are "Viet Nam" and "Lao PDR"; we use the
# common English names and record the official ones in metadata.json.
COUNTRIES = {
    "KHM": "Cambodia",
    "VNM": "Vietnam",
    "MMR": "Myanmar",
    "LAO": "Laos",
}

# World Bank indicator code -> short name, unit and category for this project.
INDICATORS = {
    "NY.GDP.MKTP.KD.ZG": {"name": "GDP growth", "unit": "annual %", "category": "Economics"},
    "NY.GDP.PCAP.CD": {"name": "GDP per capita", "unit": "current US$", "category": "Economics"},
    "FP.CPI.TOTL.ZG": {"name": "Inflation", "unit": "consumer prices, annual %", "category": "Economics"},
    "BX.KLT.DINV.WD.GD.ZS": {"name": "FDI net inflows", "unit": "% of GDP", "category": "Economics"},
    "BX.TRF.PWKR.DT.GD.ZS": {"name": "Remittances received", "unit": "% of GDP", "category": "Economics"},
    "IT.NET.USER.ZS": {"name": "Internet users", "unit": "% of population", "category": "Digital development"},
    "IT.CEL.SETS.P2": {"name": "Mobile subscriptions", "unit": "per 100 people", "category": "Digital development"},
    "IT.NET.SECR.P6": {
        "name": "Secure Internet servers",
        "unit": "per 1 million people",
        "category": "Digital infrastructure",
        "note": (
            "A digital infrastructure indicator: distinct, publicly-trusted TLS/SSL "
            "certificates found by Netcraft's Secure Server Survey, counted by hosting "
            "country, per 1 million people. It is NOT a comprehensive measure of "
            "cybersecurity readiness or national cyber resilience."
        ),
    },
}

# Plausibility rules used during validation.
PERCENT_OF_POPULATION = ["IT.NET.USER.ZS"]  # must lie between 0 and 100
NEVER_NEGATIVE = ["NY.GDP.PCAP.CD", "IT.NET.USER.ZS", "IT.CEL.SETS.P2", "IT.NET.SECR.P6"]
# Not capped at 100: mobile subscriptions per 100 people, because one person
# can own several SIM cards. Not forced positive: GDP growth, inflation and FDI
# net inflows, which can genuinely be negative.

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
CSV_PATH = DATA_DIR / "cambodia_econ.csv"
METADATA_PATH = DATA_DIR / "metadata.json"

COLUMN_ORDER = ["country", "country_code", "year", "indicator", "indicator_code", "value"]


class WorldBankAPIError(Exception):
    """The API answered, but with an error message or an unexpected format."""


# ---------------------------------------------------------------------------
# 1. Talking to the World Bank API
# ---------------------------------------------------------------------------

def get_json(url: str, params: dict) -> list:
    """Send a GET request and return the decoded JSON payload.

    Retries network problems and server errors (HTTP 5xx), because this
    public API is slow and occasionally fails. Client errors (HTTP 4xx) are
    not retried: asking again would not fix a bad request.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()  # turns HTTP 4xx/5xx into an exception
            payload = response.json()
            break
        except requests.RequestException as error:
            status = getattr(error.response, "status_code", None)
            is_client_error = status is not None and status < 500
            if is_client_error or attempt == MAX_ATTEMPTS:
                raise
            wait_seconds = 5 * attempt
            print(f"    attempt {attempt} failed ({type(error).__name__}), "
                  f"retrying in {wait_seconds} s ...", flush=True)
            time.sleep(wait_seconds)

    # The World Bank API reports some errors (for example an unknown indicator
    # code) with HTTP 200 and a body like [{"message": [...]}]. A successful
    # status code alone therefore does not prove that we received data.
    if isinstance(payload, list) and len(payload) == 1 and "message" in payload[0]:
        messages = "; ".join(item.get("value", "") for item in payload[0]["message"])
        raise WorldBankAPIError(messages)

    # A normal answer is a list of two parts: [page information, records].
    if not (isinstance(payload, list) and len(payload) == 2):
        raise WorldBankAPIError(f"Unexpected response format from {response.url}")
    return payload


def fetch_indicator_data(indicator_code: str) -> tuple[list[dict], str]:
    """Download every observation of one indicator for our four countries.

    Returns the raw JSON records and the date the World Bank last updated
    the database.
    """
    country_list = ";".join(COUNTRIES)  # "KHM;VNM;MMR;LAO"
    url = f"{API_BASE_URL}/country/{country_list}/indicator/{indicator_code}"
    params = {
        "format": "json",
        "source": WDI_SOURCE_ID,
        # Ask up to the current year; the API only returns years it has.
        "date": f"{START_YEAR}:{datetime.now().year}",
        "per_page": 1000,
        "page": 1,
    }

    records = []
    while True:  # follow pagination until the last page
        page_info, page_records = get_json(url, params)
        records.extend(page_records or [])  # page_records is None when empty
        if page_info["page"] >= page_info["pages"]:
            break
        params["page"] += 1
    return records, page_info.get("lastupdated", "unknown")


def fetch_indicator_metadata(indicator_code: str) -> dict:
    """Download the official name, source organisation and definition."""
    _, details = get_json(f"{API_BASE_URL}/indicator/{indicator_code}", {"format": "json"})
    if not details:
        raise WorldBankAPIError(f"No metadata returned for {indicator_code}")
    return {
        "world_bank_name": details[0].get("name"),
        "source_organization": details[0].get("sourceOrganization"),
        "definition": details[0].get("sourceNote"),
    }


# ---------------------------------------------------------------------------
# 2. From JSON records to a tidy DataFrame
# ---------------------------------------------------------------------------

def records_to_dataframe(records: list[dict]) -> pd.DataFrame:
    """Turn raw API records into a tidy DataFrame with standard columns.

    One API record looks like this (fields we do not need are left out):
        {"indicator": {"id": "IT.NET.USER.ZS", "value": "Individuals using ..."},
         "countryiso3code": "KHM", "date": "2024", "value": 68.47}
    """
    rows = [
        {
            "country_code": record["countryiso3code"],
            "year": record["date"],
            "indicator_code": record["indicator"]["id"],
            "value": record["value"],  # None when the observation is missing
        }
        for record in records
    ]
    df = pd.DataFrame(rows)

    # Standardise data types. Both conversions raise an error instead of
    # guessing if the API ever sends something unexpected (e.g. "2024Q1").
    df["year"] = df["year"].astype("int64")
    df["value"] = pd.to_numeric(df["value"], errors="raise").astype("float64")  # None -> NaN

    # Add readable names. An unknown code becomes NaN, which validate() reports.
    df["country"] = df["country_code"].map(COUNTRIES)
    df["indicator"] = df["indicator_code"].map({code: info["name"] for code, info in INDICATORS.items()})

    df = df[COLUMN_ORDER]
    return df.sort_values(["country_code", "indicator_code", "year"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 3. Validation
# ---------------------------------------------------------------------------

def validate(df: pd.DataFrame) -> list[str]:
    """Check the dataset. Returns a list of problems (an empty list = valid)."""
    problems = []

    if list(df.columns) != COLUMN_ORDER:
        problems.append(f"Unexpected columns: {list(df.columns)}")

    # Exactly the countries and indicators we asked for, no more, no less.
    for column, expected in [("country_code", set(COUNTRIES)), ("indicator_code", set(INDICATORS))]:
        returned = set(df[column])
        if returned != expected:
            problems.append(f"{column}: missing {sorted(expected - returned)}, "
                            f"unexpected {sorted(returned - expected)}")

    # Every code must have a readable name (NaN means an unknown code).
    if df["country"].isna().any() or df["indicator"].isna().any():
        problems.append("Some rows have no country or indicator name")

    # Years must be inside the requested range.
    current_year = datetime.now().year
    if df["year"].min() < START_YEAR or df["year"].max() > current_year:
        problems.append(f"Years outside {START_YEAR}-{current_year}: "
                        f"{df['year'].min()}-{df['year'].max()}")

    # One value per country + year + indicator.
    duplicate_count = df.duplicated(subset=["country_code", "year", "indicator_code"]).sum()
    if duplicate_count:
        problems.append(f"{duplicate_count} duplicate country-year-indicator rows")

    if not pd.api.types.is_float_dtype(df["value"]):
        problems.append(f"value column has type {df['value'].dtype}, expected float")

    # An indicator with no real observation at all has failed, even if the
    # API returned rows for it (they would all be empty).
    observed_indicators = set(df.dropna(subset=["value"])["indicator_code"])
    for code in sorted(set(INDICATORS) - observed_indicators):
        problems.append(f"{code} ({INDICATORS[code]['name']}) has no observations at all")

    # Plausibility checks. dropna() skips missing values; they are allowed.
    for code in PERCENT_OF_POPULATION:
        values = df.loc[df["indicator_code"] == code, "value"].dropna()
        if not values.between(0, 100).all():
            problems.append(f"{code} has values outside 0-100")
    for code in NEVER_NEGATIVE:
        values = df.loc[df["indicator_code"] == code, "value"].dropna()
        if (values < 0).any():
            problems.append(f"{code} has negative values")

    return problems


# ---------------------------------------------------------------------------
# 4. Reporting
# ---------------------------------------------------------------------------

def latest_year_table(df: pd.DataFrame) -> pd.DataFrame:
    """Latest year with a real (non-missing) value, per indicator and country."""
    observed = df.dropna(subset=["value"])
    table = observed.groupby(["indicator_code", "country_code"])["year"].max().unstack()
    # Make sure every indicator and country appears, even with no data at all.
    table = table.reindex(index=list(INDICATORS), columns=list(COUNTRIES))
    return table.astype("Int64")  # nullable integer: shows <NA> instead of 2024.0


def print_summary(df: pd.DataFrame) -> None:
    """Print a readable report of what was collected."""
    is_missing = df["value"].isna()
    observed = df[~is_missing]

    print("\n" + "=" * 72)
    print("DATA COLLECTION SUMMARY")
    print("=" * 72)
    print(f"Rows in dataset (country x year x indicator): {len(df)}")
    print(f"  with a real value: {len(observed)}")
    print(f"  missing (NaN):     {is_missing.sum()}")
    print(f"Year range in dataset:          {df['year'].min()}-{df['year'].max()}")
    print(f"Years with at least one value:  {observed['year'].min()}-{observed['year'].max()}")

    print("\nCountries collected:")
    for code, name in COUNTRIES.items():
        print(f"  {code}  {name}")

    print("\nIndicators collected (observations / missing / exact zeros / years with data):")
    for code, info in INDICATORS.items():
        rows = df[df["indicator_code"] == code]
        values = rows["value"]
        years_with_data = rows.dropna(subset=["value"])["year"]
        print(f"  {info['name']:<24} {code:<22} "
              f"obs={values.notna().sum():>3}  missing={values.isna().sum():>3}  "
              f"zeros={(values == 0).sum()}  {years_with_data.min()}-{years_with_data.max()}")

    print("\nMissing values per country:")
    print(df[is_missing].groupby("country")["value"].size().reindex(COUNTRIES.values(), fill_value=0).to_string())

    print("\nLatest year with a real value (<NA> = no data at all):")
    table = latest_year_table(df)
    table.index = [INDICATORS[code]["name"] for code in table.index]
    table.columns = [COUNTRIES[code] for code in table.columns]
    print(table.to_string())

    print("\nNote on Secure Internet servers:")
    print("  " + INDICATORS["IT.NET.SECR.P6"]["note"])


# ---------------------------------------------------------------------------
# 5. Saving and checking the output
# ---------------------------------------------------------------------------

def build_metadata(df: pd.DataFrame, api_info: dict, retrieved_at: datetime) -> dict:
    """Collect everything needed to describe and reproduce this dataset."""
    latest = latest_year_table(df)
    indicators = {}
    for code, info in INDICATORS.items():
        values = df.loc[df["indicator_code"] == code, "value"]
        indicators[code] = {
            **info,
            **api_info[code],
            "observations": int(values.notna().sum()),
            "missing": int(values.isna().sum()),
            "latest_year": {
                country: (None if pd.isna(year) else int(year))
                for country, year in latest.loc[code].items()
            },
        }
    return {
        "source": "World Bank, World Development Indicators (WDI)",
        "source_url": "https://data.worldbank.org/",
        "api": API_BASE_URL,
        "license": "CC BY 4.0",
        "retrieved_at_utc": retrieved_at.isoformat(timespec="seconds"),
        "requested_years": f"{START_YEAR}-{datetime.now().year}",
        "dataset_year_range": [int(df["year"].min()), int(df["year"].max())],
        "rows": len(df),
        "countries": {code: {"name": name, "world_bank_name": api_info["countries"][code]}
                      for code, name in COUNTRIES.items()},
        "indicators": indicators,
    }


def save_outputs(df: pd.DataFrame, metadata: dict) -> None:
    """Write the CSV and the metadata JSON, then check the CSV reads back identically."""
    DATA_DIR.mkdir(exist_ok=True)
    df.to_csv(CSV_PATH, index=False)  # NaN is written as an empty cell
    METADATA_PATH.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    # Round-trip check: what we saved must be exactly what we collected.
    reloaded = pd.read_csv(CSV_PATH)
    pd.testing.assert_frame_equal(reloaded, df)  # raises AssertionError if not


# ---------------------------------------------------------------------------
# Main program
# ---------------------------------------------------------------------------

def main() -> int:
    """Run the whole pipeline. Returns 0 on success, 1 on failure."""
    print(f"Requesting {len(INDICATORS)} indicators for {', '.join(COUNTRIES)} "
          f"from {API_BASE_URL} (this API is slow; expect several minutes)\n")

    all_records = []
    api_info = {"countries": {}}
    failures = {}

    for number, code in enumerate(INDICATORS, start=1):
        print(f"[{number}/{len(INDICATORS)}] {code} ({INDICATORS[code]['name']})", flush=True)
        started = time.perf_counter()
        try:
            records, last_updated = fetch_indicator_data(code)
            api_info[code] = fetch_indicator_metadata(code) | {"database_last_updated": last_updated}
        except (requests.RequestException, WorldBankAPIError) as error:
            failures[code] = f"{type(error).__name__}: {error}"
            print(f"    FAILED: {failures[code]}", flush=True)
            continue
        for record in records:  # remember the official World Bank country names
            api_info["countries"][record["countryiso3code"]] = record["country"]["value"]
        all_records.extend(records)
        print(f"    {len(records)} records, database last updated {last_updated}, "
              f"{time.perf_counter() - started:.0f} s", flush=True)

    retrieved_at = datetime.now(timezone.utc)

    # Never drop a failed indicator silently: stop, report, and keep any
    # previously saved dataset untouched.
    if failures:
        print("\nThe following indicators could not be collected:")
        for code, reason in failures.items():
            print(f"  {code}: {reason}")
            print(f"    check: https://data.worldbank.org/indicator/{code}")
        print("\nNothing was saved. Fix or replace these indicators, then run again.")
        return 1

    df = records_to_dataframe(all_records)

    problems = validate(df)
    if problems:
        print("\nValidation FAILED. Nothing was saved:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nValidation passed: codes, years, duplicates, data types and value ranges OK.")

    print_summary(df)

    save_outputs(df, build_metadata(df, api_info, retrieved_at))
    print(f"\nSaved {len(df)} rows to {CSV_PATH.relative_to(PROJECT_DIR)}")
    print(f"Saved retrieval details to {METADATA_PATH.relative_to(PROJECT_DIR)}")
    print(f"Retrieved at (UTC): {retrieved_at.isoformat(timespec='seconds')}")
    print("CSV round-trip check passed: the saved file matches the collected data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
