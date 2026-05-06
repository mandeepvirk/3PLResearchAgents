from __future__ import annotations

from collections import Counter

from provider_pipeline.models import ProviderRecord


REJECTION_KEYWORDS = {
    "self-storage": "self-storage operator",
    "self storage": "self-storage operator",
    "mini storage": "mini storage operator",
    "consumer storage": "consumer storage operator",
    "moving company": "moving-only company",
    "moving services": "moving-only company",
    "movers": "moving-only company",
    "courier": "courier-only company",
    "parcel": "parcel-only company",
}

ASSOCIATION_DIRECTORY_KEYWORDS = {
    "association",
    "affiliated",
    "directory",
    "directories",
    "member directory",
    "members directory",
    "warehouse finder",
    "marketplace",
    "listing site",
}

GOVERNMENT_FACILITY_KEYWORDS = {
    "cbsa",
    "government office",
    "government_office",
    "mail centre",
    "mail center",
    "customs mail",
    "inspection centre",
    "inspection center",
    "customs office",
}

INTERNAL_DISTRIBUTION_KEYWORDS = {
    "distribution centre",
    "distribution center",
}

RETAIL_BRAND_KEYWORDS = {
    "loblaw",
    "walmart",
    "costco",
    "amazon",
    "sobeys",
    "save on foods",
    "superstore",
    "shoppers drug mart",
    "canadian tire",
    "home depot",
    "ikea",
}

THIRD_PARTY_SERVICE_KEYWORDS = {
    "3pl",
    "third party logistics",
    "fulfillment",
    "warehouse",
    "warehousing",
    "storage",
    "distribution",
}

WAREHOUSING_EVIDENCE_KEYWORDS = {
    "warehouse",
    "warehousing",
    "bonded warehouse",
    "sufferance warehouse",
    "cold storage",
    "food warehousing",
    "food storage",
    "storage",
    "3pl",
    "fulfillment",
    "distribution",
    "bonded storage",
    "customs warehouse",
}

GENERIC_LOGISTICS_KEYWORDS = {
    "logistics",
    "transport",
    "transportation",
    "freight",
    "shipping",
}

TRUCKING_ONLY_KEYWORDS = {
    "trucking",
    "truckload",
    "ltl",
    "drayage",
    "carrier",
}


def verify_records(records: list[ProviderRecord]) -> list[ProviderRecord]:
    duplicate_counts = Counter(_duplicate_key(record) for record in records)
    for record in records:
        verify_record(record, duplicate_counts)
    return records


def verify_record(record: ProviderRecord, duplicate_counts: Counter[str]) -> ProviderRecord:
    rejection_reasons: list[str] = []
    review_reasons: list[str] = []

    haystack = _record_text(record)
    matched = {keyword.lower() for keyword in record.matched_keywords}
    useful_evidence = bool(record.evidence_items)
    warehousing_evidence = bool(
        matched & WAREHOUSING_EVIDENCE_KEYWORDS
    )
    website_evidence = any(item.source_type == "website_page" for item in record.evidence_items)
    listing_evidence = any(item.source_type == "listing_context" for item in record.evidence_items)
    listing_only_evidence = useful_evidence and listing_evidence and not website_evidence
    strong_specialized = _has_strong_specialized_evidence(record)
    target_service_evidence = record.provider_category in {
        "bonded_cross_border",
        "cold_food_grade",
    } or (
        record.provider_category == "general_3pl" and warehousing_evidence
    )
    no_contact = not record.phone and not record.website

    for keyword, reason in REJECTION_KEYWORDS.items():
        if keyword in haystack:
            rejection_reasons.append(reason)
            break

    if not _is_bc_record(record):
        rejection_reasons.append("outside BC")

    if _is_association_or_directory(record, haystack):
        rejection_reasons.append("association or directory listing")

    if _is_government_or_customs_facility(record, haystack):
        rejection_reasons.append("government or customs facility")

    if _is_internal_distribution_centre(record, haystack, website_evidence):
        rejection_reasons.append("internal distribution centre")

    if not target_service_evidence and not record.services:
        rejection_reasons.append("no evidence for warehousing or target logistics services")

    if no_contact:
        rejection_reasons.append("no phone and no website")

    if listing_only_evidence and not record.website and not strong_specialized:
        rejection_reasons.append("listing-only evidence without provider website")

    if record.website and not website_evidence and not listing_evidence:
        rejection_reasons.append("website unreachable and no useful listing evidence")

    if rejection_reasons:
        record.verification_status = "rejected"
        record.rejection_reasons = _dedupe(rejection_reasons)
        record.verification_notes = (
            "Rejected: " + "; ".join(record.rejection_reasons) + "."
        )
        return record

    if record.confidence < 60 or (useful_evidence and not website_evidence):
        review_reasons.append("weak evidence")

    if not record.phone:
        review_reasons.append("no phone")

    if not record.website:
        review_reasons.append("no website")

    if _is_generic_logistics(record, warehousing_evidence):
        review_reasons.append("generic logistics company with unclear warehouse services")

    if _is_trucking_only_cross_border(record, warehousing_evidence):
        review_reasons.append("trucking-only record with cross-border mention")

    if duplicate_counts[_duplicate_key(record)] > 1:
        review_reasons.append("duplicate-looking record")

    if _is_approved(record, warehousing_evidence):
        if review_reasons:
            record.verification_status = "review"
            record.rejection_reasons = []
            record.verification_notes = "Review: " + "; ".join(_dedupe(review_reasons)) + "."
        else:
            record.verification_status = "approved"
            record.rejection_reasons = []
            record.verification_notes = _approval_note(record)
        return record

    review_reasons.append("insufficient approval evidence")
    record.verification_status = "review"
    record.rejection_reasons = []
    record.verification_notes = "Review: " + "; ".join(_dedupe(review_reasons)) + "."
    return record


def _is_approved(record: ProviderRecord, warehousing_evidence: bool) -> bool:
    if record.provider_category == "bonded_cross_border" and (
        record.has_bonded or record.has_sufferance or record.has_cross_border
    ):
        return True
    if record.provider_category == "cold_food_grade" and (
        record.has_cold_storage or record.has_food_grade or record.has_reefer_transport
    ):
        return True
    if record.provider_category == "general_3pl" and warehousing_evidence and (
        record.phone or record.website
    ):
        return True
    return False


def _approval_note(record: ProviderRecord) -> str:
    if record.provider_category == "bonded_cross_border":
        return "Approved: evidence supports bonded, sufferance, or cross-border warehousing."
    if record.provider_category == "cold_food_grade":
        return "Approved: evidence supports cold storage or food-grade warehousing."
    return "Approved: credible 3PL warehousing evidence with usable contact details."


def _is_bc_record(record: ProviderRecord) -> bool:
    province = (record.province or "").strip().lower()
    address = (record.formatted_address or "").lower()
    query_text = " ".join(record.source_queries).lower()
    return province in {"bc", "british columbia"} or " bc" in address or "british columbia" in address or " bc" in query_text


def _is_generic_logistics(record: ProviderRecord, warehousing_evidence: bool) -> bool:
    matched = {keyword.lower() for keyword in record.matched_keywords}
    return (
        record.provider_category in {"general_3pl", "backup_or_unknown"}
        and not warehousing_evidence
        and bool(matched & GENERIC_LOGISTICS_KEYWORDS)
    )


def _is_trucking_only_cross_border(record: ProviderRecord, warehousing_evidence: bool) -> bool:
    matched = {keyword.lower() for keyword in record.matched_keywords}
    return record.has_cross_border and not warehousing_evidence and bool(
        matched & TRUCKING_ONLY_KEYWORDS
    )


def _is_association_or_directory(record: ProviderRecord, haystack: str) -> bool:
    company = (record.company or "").lower()
    evidence_text = " ".join(
        item.evidence_text.lower()
        for item in record.evidence_items
        if item.source_type == "website_page"
    )
    combined = " ".join([company, evidence_text, (record.evidence_notes or "").lower()])
    return any(keyword in combined for keyword in ASSOCIATION_DIRECTORY_KEYWORDS)


def _is_government_or_customs_facility(record: ProviderRecord, haystack: str) -> bool:
    place_types = " ".join(record.place_types).lower()
    combined = " ".join([(record.company or "").lower(), (record.website or "").lower(), haystack, place_types])
    return any(keyword in combined for keyword in GOVERNMENT_FACILITY_KEYWORDS)


def _is_internal_distribution_centre(
    record: ProviderRecord,
    haystack: str,
    website_evidence: bool,
) -> bool:
    company = (record.company or "").lower()
    mentions_distribution_centre = any(
        keyword in company for keyword in INTERNAL_DISTRIBUTION_KEYWORDS
    )
    mentions_retail_brand = any(keyword in company for keyword in RETAIL_BRAND_KEYWORDS)
    if not mentions_distribution_centre and not mentions_retail_brand:
        return False
    if website_evidence and _has_provider_service_website_evidence(record):
        return False
    return mentions_distribution_centre or mentions_retail_brand


def _has_provider_service_website_evidence(record: ProviderRecord) -> bool:
    for item in record.evidence_items:
        if item.source_type != "website_page":
            continue
        matched = {keyword.lower() for keyword in item.matched_keywords}
        if matched & THIRD_PARTY_SERVICE_KEYWORDS:
            return True
    return False


def _has_strong_specialized_evidence(record: ProviderRecord) -> bool:
    return any(
        [
            record.has_bonded,
            record.has_sufferance,
            record.has_cold_storage,
            record.has_food_grade,
        ]
    )


def _record_text(record: ProviderRecord) -> str:
    parts = [
        record.company,
        record.formatted_address,
        record.website,
        " ".join(record.place_types),
        " ".join(record.services),
        " ".join(record.matched_keywords),
        record.evidence_notes,
    ]
    return " ".join(part for part in parts if part).lower()


def _duplicate_key(record: ProviderRecord) -> str:
    city = (record.city or "").strip().lower()
    return f"{record.normalized_company or record.company.lower()}::{city}"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped
