"""
Defines the schema mapping for each ad platform onto one normalized metric
schema, and applies it during load. Currency conversion uses the exchange
rate on the date spend occurred, not the date the pipeline runs, so historical
comparisons stay stable as exchange rates move.
"""

import logging
from dataclasses import dataclass, field

from google.cloud import bigquery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("normalize_platform_schemas")


@dataclass
class PlatformSchema:
    platform_name: str
    field_map: dict  # normalized_field -> platform's native field name
    currency_field: str
    native_currency: str = "USD"


PLATFORM_SCHEMAS = {
    "google_ads": PlatformSchema(
        platform_name="google_ads",
        field_map={
            "spend": "cost_micros",
            "impressions": "impressions",
            "conversions": "conversions",
            "date": "segments_date",
        },
        currency_field="account_currency_code",
    ),
    "meta_ads": PlatformSchema(
        platform_name="meta_ads",
        field_map={
            "spend": "spend",
            "impressions": "impressions",
            "conversions": "actions_offsite_conversion",
            "date": "date_start",
        },
        currency_field="account_currency",
    ),
    "linkedin_ads": PlatformSchema(
        platform_name="linkedin_ads",
        field_map={
            "spend": "costInLocalCurrency",
            "impressions": "impressions",
            "conversions": "externalWebsiteConversions",
            "date": "dateRange_start",
        },
        currency_field="account_currency_code",
    ),
}


def get_exchange_rate(currency: str, date: str, bq_client: bigquery.Client) -> float:
    """Looks up the exchange rate to USD for the given currency on the given date."""
    if currency == "USD":
        return 1.0

    query = """
        select rate
        from `project.reference.exchange_rates`
        where currency_code = @currency and rate_date = @date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("currency", "STRING", currency),
            bigquery.ScalarQueryParameter("date", "DATE", date),
        ]
    )
    result = list(bq_client.query(query, job_config=job_config).result())
    return result[0].rate if result else 1.0


def normalize_row(raw_row: dict, schema: PlatformSchema, bq_client: bigquery.Client) -> dict:
    """Maps a single raw platform row onto the normalized schema, converting currency."""
    date = raw_row[schema.field_map["date"]]
    native_currency = raw_row.get(schema.currency_field, schema.native_currency)
    exchange_rate = get_exchange_rate(native_currency, date, bq_client)

    raw_spend = raw_row[schema.field_map["spend"]]
    # Google Ads reports cost in micros (1/1,000,000 of the currency unit)
    if schema.platform_name == "google_ads":
        raw_spend = raw_spend / 1_000_000

    return {
        "platform": schema.platform_name,
        "date": date,
        "spend_usd": round(raw_spend * exchange_rate, 2),
        "native_currency": native_currency,
        "impressions": raw_row[schema.field_map["impressions"]],
        "conversions": raw_row[schema.field_map["conversions"]],
    }


def normalize_platform_data(platform_name: str, raw_rows: list[dict], bq_client: bigquery.Client) -> list[dict]:
    schema = PLATFORM_SCHEMAS[platform_name]
    normalized = [normalize_row(row, schema, bq_client) for row in raw_rows]
    logger.info(f"Normalized {len(normalized)} rows for {platform_name}")
    return normalized


def load_normalized_data(normalized_rows: list[dict], bq_client: bigquery.Client) -> None:
    table_id = "project.normalized.marketing_metrics_raw"
    errors = bq_client.insert_rows_json(table_id, normalized_rows)
    if errors:
        raise RuntimeError(f"Failed to load normalized data: {errors}")
    logger.info(f"Loaded {len(normalized_rows)} normalized rows into {table_id}")


if __name__ == "__main__":
    bq_client = bigquery.Client()
    for platform in PLATFORM_SCHEMAS:
        # raw_rows would come from each platform's own extractor
        raw_rows = []  # placeholder
        normalized = normalize_platform_data(platform, raw_rows, bq_client)
        load_normalized_data(normalized, bq_client)
