from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from provider_pipeline.models import EvidenceItem, ProviderRecord


RAW_FIELDS = [
    "company",
    "target_categories",
    "source_queries",
    "city",
    "province",
    "formatted_address",
    "phone",
    "website",
    "google_maps_url",
    "place_id",
    "place_types",
]

ENRICHED_FIELDS = [
    "company",
    "normalized_company",
    "provider_category",
    "confidence",
    "city",
    "province",
    "phone",
    "website",
    "formatted_address",
    "services",
    "has_bonded",
    "has_sufferance",
    "has_cross_border",
    "has_cold_storage",
    "has_food_grade",
    "has_reefer_transport",
    "recommended_call_angle",
    "matched_keywords",
    "evidence_notes",
    "evidence_urls",
    "evidence_items",
    "verification_status",
    "verification_notes",
    "rejection_reasons",
    "audit_status",
    "audit_model",
    "audit_notes",
    "target_categories",
    "source_queries",
    "google_maps_url",
    "place_id",
]

VERIFIED_FIELDS = [
    "company",
    "normalized_company",
    "provider_category",
    "verification_status",
    "verification_notes",
    "rejection_reasons",
    "audit_status",
    "audit_model",
    "audit_notes",
    "confidence",
    "city",
    "province",
    "phone",
    "website",
    "formatted_address",
    "services",
    "has_bonded",
    "has_sufferance",
    "has_cross_border",
    "has_cold_storage",
    "has_food_grade",
    "has_reefer_transport",
    "recommended_call_angle",
    "matched_keywords",
    "evidence_notes",
    "evidence_urls",
    "evidence_items",
    "target_categories",
    "source_queries",
    "google_maps_url",
    "place_id",
]

SCORED_FIELDS = [
    "company",
    "normalized_company",
    "provider_category",
    "priority",
    "lead_fit_score",
    "verification_status",
    "verification_notes",
    "rejection_reasons",
    "audit_status",
    "audit_model",
    "audit_notes",
    "confidence",
    "city",
    "province",
    "phone",
    "website",
    "formatted_address",
    "services",
    "has_bonded",
    "has_sufferance",
    "has_cross_border",
    "has_cold_storage",
    "has_food_grade",
    "has_reefer_transport",
    "recommended_call_angle",
    "matched_keywords",
    "evidence_notes",
    "evidence_urls",
    "evidence_items",
    "target_categories",
    "source_queries",
    "google_maps_url",
    "place_id",
]

CALL_SHEET_FIELDS = [
    "priority",
    "lead_fit_score",
    "company",
    "provider_category",
    "city",
    "phone",
    "website",
    "services",
    "recommended_call_angle",
    "call_opener",
    "audit_status",
    "audit_notes",
    "evidence_urls",
]


def write_raw_csv(path: Path, records: Iterable[ProviderRecord]) -> None:
    rows = [_row(record, RAW_FIELDS) for record in records]
    _write_csv(path, RAW_FIELDS, rows)


def write_enriched_csv(path: Path, records: Iterable[ProviderRecord]) -> None:
    rows = [_row(record, ENRICHED_FIELDS) for record in records]
    _write_csv(path, ENRICHED_FIELDS, rows)


def write_verified_csv(path: Path, records: Iterable[ProviderRecord]) -> None:
    rows = [_row(record, VERIFIED_FIELDS) for record in records]
    _write_csv(path, VERIFIED_FIELDS, rows)


def write_audited_csv(path: Path, records: Iterable[ProviderRecord]) -> None:
    rows = [_row(record, VERIFIED_FIELDS) for record in records]
    _write_csv(path, VERIFIED_FIELDS, rows)


def write_scored_csv(path: Path, records: Iterable[ProviderRecord]) -> None:
    rows = [_row(record, SCORED_FIELDS) for record in records]
    _write_csv(path, SCORED_FIELDS, rows)


def write_records_jsonl(path: Path, records: Iterable[ProviderRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=True))
            handle.write("\n")


def read_records_jsonl(path: Path) -> list[ProviderRecord]:
    records: list[ProviderRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(_record_from_dict(json.loads(line)))
    return records


def write_call_sheet_csv(path: Path, records: Iterable[ProviderRecord]) -> int:
    call_records = [
        record
        for record in records
        if record.priority in {"A", "B"}
        and record.verification_status == "approved"
        and bool((record.phone or "").strip())
        and bool((record.website or "").strip())
    ]
    call_records.sort(key=lambda record: record.lead_fit_score, reverse=True)

    rows = []
    for record in call_records:
        row = _row(record, CALL_SHEET_FIELDS)
        row["call_opener"] = build_call_opener(record)
        rows.append(row)

    _write_csv(path, CALL_SHEET_FIELDS, rows)
    return len(rows)


def build_call_opener(record: ProviderRecord) -> str:
    lead_types = []
    if record.provider_category == "bonded_cross_border":
        lead_types.append("bonded, sufferance, or cross-border warehousing")
    if record.has_cold_storage or record.has_food_grade:
        lead_types.append("cold storage or food-grade warehousing")
    if not lead_types:
        lead_types.append("3PL warehousing")

    lead_type_text = " and ".join(lead_types)
    return (
        "I'm building a BC logistics RFQ network. You only pay if the RFQ is "
        f"qualified and you accept it. If I bring you verified shippers looking for "
        f"{lead_type_text}, with company name, contact, volume, timeline, commodity, "
        "and permission to be contacted, would you pay $1,500 for the opportunity "
        "or $3,500 if it's exclusive?"
    )


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _row(record: ProviderRecord, fields: list[str]) -> dict[str, str]:
    data = asdict(record)
    row = {}
    for field in fields:
        value = data.get(field, "")
        if field == "evidence_items":
            row[field] = json.dumps(value, ensure_ascii=True)
            continue
        if isinstance(value, list):
            row[field] = "; ".join(str(item) for item in value if item)
        else:
            row[field] = "" if value is None else str(value)
    return row


def _record_from_dict(data: dict[str, Any]) -> ProviderRecord:
    return ProviderRecord(
        company=_string(data.get("company")),
        target_categories=_list(data.get("target_categories")),
        source_queries=_list(data.get("source_queries")),
        city=_string(data.get("city")),
        province=_string(data.get("province")),
        formatted_address=_string(data.get("formatted_address")),
        phone=_string(data.get("phone")),
        website=_string(data.get("website")),
        google_maps_url=_string(data.get("google_maps_url")),
        place_id=_string(data.get("place_id")),
        place_types=_list(data.get("place_types")),
        normalized_company=_string(data.get("normalized_company")),
        provider_category=_string(data.get("provider_category")) or "backup_or_unknown",
        services=_list(data.get("services")),
        has_bonded=_bool(data.get("has_bonded")),
        has_sufferance=_bool(data.get("has_sufferance")),
        has_cross_border=_bool(data.get("has_cross_border")),
        has_cold_storage=_bool(data.get("has_cold_storage")),
        has_food_grade=_bool(data.get("has_food_grade")),
        has_reefer_transport=_bool(data.get("has_reefer_transport")),
        evidence_items=_evidence_items(data.get("evidence_items")),
        matched_keywords=_list(data.get("matched_keywords")),
        evidence_urls=_list(data.get("evidence_urls")),
        evidence_notes=_string(data.get("evidence_notes")),
        verification_status=_string(data.get("verification_status")) or "review",
        verification_notes=_string(data.get("verification_notes")),
        rejection_reasons=_list(data.get("rejection_reasons")),
        audit_status=_string(data.get("audit_status")) or "not_run",
        audit_model=_string(data.get("audit_model")),
        audit_notes=_string(data.get("audit_notes")),
        recommended_call_angle=_string(data.get("recommended_call_angle")),
        confidence=_int(data.get("confidence"), default=30),
        lead_fit_score=_int(data.get("lead_fit_score"), default=0),
        priority=_string(data.get("priority")) or "C",
    )


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        if not value.strip():
            return []
        return [item.strip() for item in value.split(";") if item.strip()]
    return [str(value)]


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _evidence_items(value: Any) -> list[EvidenceItem]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []

    evidence_items: list[EvidenceItem] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        evidence_items.append(
            EvidenceItem(
                source_url=_string(item.get("source_url")),
                source_type=_string(item.get("source_type")),
                matched_keywords=_list(item.get("matched_keywords")),
                evidence_text=_string(item.get("evidence_text")),
                confidence=_int(item.get("confidence"), default=0),
            )
        )
    return evidence_items
