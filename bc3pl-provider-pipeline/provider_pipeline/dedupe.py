from __future__ import annotations

import re

from provider_pipeline.models import ProviderRecord


def dedupe_records(records: list[ProviderRecord]) -> list[ProviderRecord]:
    merged: dict[str, ProviderRecord] = {}

    for record in records:
        key = _dedupe_key(record)
        existing = merged.get(key)
        if existing is None:
            merged[key] = record
            continue

        existing.target_categories = sorted(
            set(existing.target_categories + record.target_categories)
        )
        existing.source_queries = sorted(set(existing.source_queries + record.source_queries))
        existing.place_types = sorted(set(existing.place_types + record.place_types))
        existing.phone = existing.phone or record.phone
        existing.website = existing.website or record.website
        existing.google_maps_url = existing.google_maps_url or record.google_maps_url
        existing.formatted_address = existing.formatted_address or record.formatted_address

    return list(merged.values())


def normalize_company_name(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(
        r"\b(inc|ltd|limited|corp|corporation|co|company|llc|ulc|canada|bc)\b",
        "",
        value,
    )
    return re.sub(r"\s+", " ", value).strip()


def _dedupe_key(record: ProviderRecord) -> str:
    if record.place_id:
        return f"place:{record.place_id}"
    city = (record.city or "").lower().strip()
    return f"name:{normalize_company_name(record.company)}:{city}"
