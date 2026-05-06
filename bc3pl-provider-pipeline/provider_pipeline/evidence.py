from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from provider_pipeline.http_client import ssl_context
from provider_pipeline.models import EvidenceItem, ProviderRecord


LIKELY_PAGE_PATHS = [
    "/",
    "/services",
    "/warehousing",
    "/cold-storage",
    "/cold-storage-warehousing",
    "/food-grade",
    "/food-grade-warehousing",
    "/bonded-warehouse",
    "/customs",
    "/cross-border",
    "/transborder",
    "/contact",
    "/about",
]

FLAG_KEYWORDS = {
    "has_bonded": [
        "bonded warehouse",
        "customs bonded",
        "cbsa bonded",
        "bonded storage",
        "customs warehouse",
    ],
    "has_sufferance": [
        "sufferance warehouse",
        "sufferance facility",
    ],
    "has_cross_border": [
        "cross-border",
        "cross border",
        "transborder",
        "customs brokerage",
        "freight forwarding",
        "import/export",
        "import export",
        "canada-us logistics",
        "canada us logistics",
    ],
    "has_cold_storage": [
        "cold storage",
        "refrigerated",
        "frozen",
        "chilled",
        "freezer",
        "cooler",
        "temperature controlled",
        "temperature-controlled",
    ],
    "has_food_grade": [
        "food-grade",
        "food grade",
        "haccp",
        "sqf",
        "cfia",
        "food storage",
        "food distribution",
        "food warehousing",
    ],
    "has_reefer_transport": [
        "reefer",
        "refrigerated trucking",
        "temperature-controlled transport",
        "temperature controlled transport",
    ],
}

GENERAL_3PL_KEYWORDS = [
    "3pl",
    "third party logistics",
    "warehouse",
    "warehousing",
    "fulfillment",
    "distribution",
    "logistics",
    "storage",
]

SPECIALIZED_CROSS_BORDER_KEYWORDS = {
    "customs brokerage",
    "freight forwarding",
}

CROSS_BORDER_CONTEXT_KEYWORDS = {
    "cross-border",
    "cross border",
    "transborder",
    "import/export",
    "import export",
    "canada-us logistics",
    "canada us logistics",
}

WAREHOUSING_SUPPORT_KEYWORDS = {
    "3pl",
    "third party logistics",
    "warehouse",
    "warehousing",
    "storage",
    "distribution",
    "fulfillment",
}

SERVICES_BY_FLAG = {
    "has_bonded": "bonded warehousing",
    "has_sufferance": "sufferance warehousing",
    "has_cross_border": "cross-border logistics",
    "has_cold_storage": "cold storage",
    "has_food_grade": "food-grade warehousing",
    "has_reefer_transport": "reefer logistics",
}

ALL_EVIDENCE_KEYWORDS = sorted(
    {
        keyword
        for keywords in FLAG_KEYWORDS.values()
        for keyword in keywords
    }
    | set(GENERAL_3PL_KEYWORDS)
)


@dataclass(frozen=True)
class EvidenceDiagnostics:
    fetched_pages: int = 0
    pages_with_matches: int = 0
    fetch_errors: int = 0


class VisibleTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_tags = {"script", "style", "noscript", "svg", "head"}
        self._ignore_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag in self._ignored_tags:
            self._ignore_depth += 1

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag in self._ignored_tags and self._ignore_depth > 0:
            self._ignore_depth -= 1

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._ignore_depth == 0:
            text = _normalize_whitespace(data)
            if text:
                self._parts.append(text)

    def text(self) -> str:
        return _normalize_whitespace(" ".join(self._parts))


def enrich_provider_record(record: ProviderRecord) -> ProviderRecord:
    evidence_items, diagnostics = collect_evidence(record)

    record.evidence_items = evidence_items
    record.matched_keywords = sorted(
        {keyword for item in evidence_items for keyword in item.matched_keywords}
    )
    record.evidence_urls = []
    record.normalized_company = _normalize_company(record.company)

    support = {
        flag: _supporting_items(evidence_items, keywords)
        for flag, keywords in FLAG_KEYWORDS.items()
    }

    record.has_bonded = bool(support["has_bonded"])
    record.has_sufferance = bool(support["has_sufferance"])
    record.has_cross_border = bool(support["has_cross_border"])
    record.has_cold_storage = bool(support["has_cold_storage"])
    record.has_food_grade = bool(support["has_food_grade"])
    record.has_reefer_transport = bool(support["has_reefer_transport"])

    record.services = [
        service for flag, service in SERVICES_BY_FLAG.items() if getattr(record, flag)
    ]

    general_support = _supporting_items(evidence_items, GENERAL_3PL_KEYWORDS)
    strong_cross_border_support = _has_strong_cross_border_warehousing_support(evidence_items)
    if record.has_bonded or record.has_sufferance or strong_cross_border_support:
        record.provider_category = "bonded_cross_border"
        record.recommended_call_angle = "Ask whether they want verified bonded or cross-border RFQs."
    elif record.has_cold_storage or record.has_food_grade or record.has_reefer_transport:
        record.provider_category = "cold_food_grade"
        record.recommended_call_angle = (
            "Ask whether they want verified cold storage or food-grade warehousing RFQs."
        )
    elif general_support:
        record.provider_category = "general_3pl"
        if not record.services:
            record.services = ["general warehousing / 3PL"]
        record.recommended_call_angle = "Use as a backup 3PL buyer-panel target."
    else:
        record.provider_category = "backup_or_unknown"
        record.recommended_call_angle = (
            "Verify whether they actually handle warehousing or logistics opportunities."
        )

    record.evidence_urls = _dedupe(
        [
            item.source_url
            for item in evidence_items
            if item.source_url and item.matched_keywords
        ]
    )
    record.confidence = _build_confidence(record, evidence_items)
    record.evidence_notes = _build_evidence_notes(record, evidence_items, diagnostics)
    return record


def collect_evidence(record: ProviderRecord) -> tuple[list[EvidenceItem], EvidenceDiagnostics]:
    evidence_items: list[EvidenceItem] = []
    diagnostics = EvidenceDiagnostics()

    listing_item = _listing_evidence(record)
    if listing_item is not None:
        evidence_items.append(listing_item)

    website = _canonical_website(record.website)
    if not website:
        return evidence_items, diagnostics

    seen_urls: set[str] = set()
    fetched_pages = 0
    pages_with_matches = 0
    fetch_errors = 0

    for path in LIKELY_PAGE_PATHS:
        page_url = urljoin(website, path)
        if page_url in seen_urls:
            continue
        seen_urls.add(page_url)

        text = _fetch_visible_text(page_url)
        if text is None:
            fetch_errors += 1
            continue

        fetched_pages += 1
        matched_keywords = _matched_keywords(text, ALL_EVIDENCE_KEYWORDS)
        if not matched_keywords:
            continue

        pages_with_matches += 1
        evidence_items.append(
            EvidenceItem(
                source_url=page_url,
                source_type="website_page",
                matched_keywords=matched_keywords,
                evidence_text=_extract_snippet(text, matched_keywords),
                confidence=75,
            )
        )

    return evidence_items, EvidenceDiagnostics(
        fetched_pages=fetched_pages,
        pages_with_matches=pages_with_matches,
        fetch_errors=fetch_errors,
    )


def _listing_evidence(record: ProviderRecord) -> EvidenceItem | None:
    parts = [
        record.company,
        record.formatted_address,
        record.website,
        " ".join(record.place_types),
    ]
    text = _normalize_whitespace(" ".join(part for part in parts if part))
    matched_keywords = _matched_keywords(text, ALL_EVIDENCE_KEYWORDS)
    if not matched_keywords:
        return None

    return EvidenceItem(
        source_url=record.google_maps_url or record.website,
        source_type="listing_context",
        matched_keywords=matched_keywords,
        evidence_text=_extract_snippet(text, matched_keywords),
        confidence=55 if record.place_id.startswith("sample-place-") else 45,
    )


def _build_confidence(record: ProviderRecord, evidence_items: list[EvidenceItem]) -> int:
    if not evidence_items:
        return 20

    top_confidence = max(item.confidence for item in evidence_items)
    website_support = any(item.source_type == "website_page" for item in evidence_items)
    matched_count = len(record.matched_keywords)

    confidence = top_confidence
    if website_support:
        confidence += 10
    if matched_count >= 3:
        confidence += 5
    if record.provider_category in {"bonded_cross_border", "cold_food_grade"}:
        confidence += 5

    return min(confidence, 95)


def _build_evidence_notes(
    record: ProviderRecord,
    evidence_items: list[EvidenceItem],
    diagnostics: EvidenceDiagnostics,
) -> str:
    if not evidence_items:
        note = "No supporting website or listing evidence found for the target service flags."
        if record.website and diagnostics.fetch_errors:
            note += " Website fetches failed or returned no usable HTML."
        return note

    source_types = sorted({item.source_type for item in evidence_items})
    matched_labels = ", ".join(record.matched_keywords[:8]) or "no matched keywords"
    note = (
        f"Evidence collected from {', '.join(source_types)} with matched keywords: "
        f"{matched_labels}."
    )
    if any(item.source_type == "listing_context" for item in evidence_items) and not any(
        item.source_type == "website_page" for item in evidence_items
    ):
        note += " Classification relies on listing or search context only; confirm on the provider website."
    if record.website and diagnostics.fetch_errors:
        note += " Some website pages could not be fetched."
    if diagnostics.fetched_pages and not diagnostics.pages_with_matches:
        note += " Website pages were fetched but did not add service evidence."
    return note


def _supporting_items(
    evidence_items: list[EvidenceItem],
    keywords: list[str],
) -> list[EvidenceItem]:
    wanted = {keyword.lower() for keyword in keywords}
    return [
        item
        for item in evidence_items
        if any(keyword.lower() in wanted for keyword in item.matched_keywords)
    ]


def _has_strong_cross_border_warehousing_support(evidence_items: list[EvidenceItem]) -> bool:
    for item in evidence_items:
        matched = {keyword.lower() for keyword in item.matched_keywords}
        has_warehousing_support = bool(matched & WAREHOUSING_SUPPORT_KEYWORDS)
        has_cross_border_context = bool(matched & CROSS_BORDER_CONTEXT_KEYWORDS)

        if "customs brokerage" in matched and has_warehousing_support:
            return True
        if "freight forwarding" in matched and has_cross_border_context and has_warehousing_support:
            return True
    return False


def _fetch_visible_text(url: str) -> str | None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; provider-pipeline/1.0)"},
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=10,
            context=ssl_context(),
        ) as response:
            content_type = response.headers.get_content_type()
            if content_type != "text/html":
                return None
            charset = response.headers.get_content_charset() or "utf-8"
            html = response.read().decode(charset, errors="replace")
            extractor = VisibleTextExtractor()
            extractor.feed(html)
            return extractor.text()
    except (RuntimeError, urllib.error.URLError, TimeoutError, ValueError):
        return None


def _matched_keywords(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    return [keyword for keyword in keywords if keyword.lower() in lowered]


def _extract_snippet(text: str, keywords: list[str], max_length: int = 280) -> str:
    lowered = text.lower()
    for keyword in keywords:
        start = lowered.find(keyword.lower())
        if start < 0:
            continue
        snippet_start = max(0, start - 80)
        snippet_end = min(len(text), start + len(keyword) + 160)
        return _normalize_whitespace(text[snippet_start:snippet_end])[:max_length]
    return _normalize_whitespace(text[:max_length])


def _canonical_website(website: str) -> str:
    value = (website or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return value if value.endswith("/") else value + "/"


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_company(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(
        r"\b(inc|ltd|limited|corp|corporation|co|company|llc|ulc|canada|bc)\b",
        "",
        value,
    )
    return re.sub(r"\s+", " ", value).strip()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped
