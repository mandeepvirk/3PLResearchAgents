from __future__ import annotations

from provider_pipeline.evidence import enrich_provider_record
from provider_pipeline.models import ProviderRecord


LOWER_MAINLAND_CITIES = {
    "vancouver",
    "richmond",
    "delta",
    "surrey",
    "burnaby",
    "new westminster",
    "coquitlam",
    "port coquitlam",
    "langley",
    "abbotsford",
    "chilliwack",
}


def enrich_with_keywords(record: ProviderRecord) -> ProviderRecord:
    return enrich_provider_record(record)


def score_record(record: ProviderRecord) -> ProviderRecord:
    score = 0

    target_set = set(record.target_categories)
    if record.provider_category in target_set:
        score += 25
    elif record.provider_category in {"bonded_cross_border", "cold_food_grade"}:
        score += 18
    elif record.provider_category == "general_3pl":
        score += 8

    if record.has_bonded:
        score += 13
    if record.has_sufferance:
        score += 13
    if record.has_cross_border:
        score += 10
    if record.has_cold_storage:
        score += 14
    if record.has_food_grade:
        score += 12
    if record.has_reefer_transport:
        score += 8

    if record.phone:
        score += 8
    if record.website:
        score += 7
    if record.city.lower().strip() in LOWER_MAINLAND_CITIES:
        score += 5

    score += min(max(record.confidence, 0), 100) // 10
    record.lead_fit_score = min(score, 100)

    if record.lead_fit_score >= 70:
        record.priority = "A"
    elif record.lead_fit_score >= 50:
        record.priority = "B"
    else:
        record.priority = "C"
    return record
