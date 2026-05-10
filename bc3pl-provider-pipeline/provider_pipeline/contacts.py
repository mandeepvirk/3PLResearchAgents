from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import replace
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

from provider_pipeline.http_client import ssl_context
from provider_pipeline.models import ContactRecord, ProviderRecord


CONTACT_PAGE_PATHS = [
    "/",
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/team",
    "/leadership",
    "/management",
    "/locations",
    "/sales",
    "/services",
]

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(
    r"(?:(?:\+?1[\s.\-()]*)?)\(?\d{3}\)?[\s.\-]*\d{3}[\s.\-]*\d{4}(?:\s*(?:x|ext\.?|extension)\s*\d{1,6})?",
    re.IGNORECASE,
)
LINKEDIN_RE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/[^\s\"'<>]+", re.IGNORECASE)

HIGH_PRIORITY_TITLES = [
    "owner",
    "founder",
    "president",
    "ceo",
    "chief executive",
    "general manager",
    "branch manager",
    "managing director",
]
SALES_TITLES = [
    "vp sales",
    "vice president sales",
    "sales director",
    "business development",
    "commercial manager",
    "account executive",
    "sales manager",
]
OPERATIONS_TITLES = [
    "warehouse manager",
    "operations manager",
    "logistics manager",
    "customs manager",
    "cross-border manager",
    "cross border manager",
    "cold storage manager",
]
LOW_PRIORITY_TITLES = [
    "admin",
    "administrator",
    "reception",
    "receptionist",
    "customer service",
    "info",
    "sales",
]
UNRELATED_TITLES = [
    "hr",
    "human resources",
    "accounting",
    "finance",
    "driver",
    "mechanic",
    "it ",
    "information technology",
]
TITLE_KEYWORDS = HIGH_PRIORITY_TITLES + SALES_TITLES + OPERATIONS_TITLES + LOW_PRIORITY_TITLES + UNRELATED_TITLES


class ContactPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_tags = {"script", "style", "noscript", "svg", "head"}
        self._ignore_depth = 0
        self._parts: list[str] = []
        self.mailtos: list[str] = []
        self.tels: list[str] = []
        self.linkedin_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag in self._ignored_tags:
            self._ignore_depth += 1
        for name, value in attrs:
            if name.lower() != "href" or not value:
                continue
            href = value.strip()
            if href.lower().startswith("mailto:"):
                self.mailtos.append(_clean_email(href[7:].split("?", 1)[0]))
            elif href.lower().startswith("tel:"):
                self.tels.append(normalize_phone(href[4:]))
            elif "linkedin.com/" in href.lower():
                self.linkedin_urls.append(href)

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag in self._ignored_tags and self._ignore_depth > 0:
            self._ignore_depth -= 1

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._ignore_depth == 0:
            text = normalize_whitespace(data)
            if text:
                self._parts.append(text)

    def text(self) -> str:
        return normalize_whitespace(" ".join(self._parts))


def contacts_from_provider(provider: ProviderRecord) -> list[ContactRecord]:
    website = canonical_website(provider.website)
    if not website:
        return [_fallback_contact(provider, "", "no_website")]

    raw_contacts: list[ContactRecord] = []
    seen_urls: set[str] = set()
    for path in CONTACT_PAGE_PATHS:
        page_url = urljoin(website, path)
        if page_url in seen_urls:
            continue
        seen_urls.add(page_url)
        page = fetch_contact_page(page_url)
        if page is None:
            continue
        raw_contacts.extend(extract_contacts_from_page(provider, page.text, page_url, page.mailtos, page.tels, page.linkedin_urls))

    if not raw_contacts:
        raw_contacts.append(_fallback_contact(provider, website, "company_phone_fallback"))
    return raw_contacts


def extract_contacts_from_page(
    provider: ProviderRecord,
    text: str,
    source_url: str,
    mailtos: list[str] | None = None,
    tels: list[str] | None = None,
    linkedin_urls: list[str] | None = None,
) -> list[ContactRecord]:
    clean_text = normalize_whitespace(text)
    emails = dedupe([_clean_email(value) for value in (mailtos or [])] + EMAIL_RE.findall(clean_text))
    phones = dedupe([normalize_phone(value) for value in (tels or [])] + [normalize_phone(value) for value in PHONE_RE.findall(clean_text)])
    linkedin = dedupe((linkedin_urls or []) + LINKEDIN_RE.findall(clean_text))
    name_title_pairs = _extract_name_title_pairs(clean_text)

    records: list[ContactRecord] = []
    for name, title in name_title_pairs:
        nearby_email = _nearest_email(clean_text, name, emails)
        nearby_phone = _nearest_phone(clean_text, name, phones)
        records.append(
            _base_contact(
                provider,
                source_url=source_url,
                source_type="website_person",
                contact_name=name,
                contact_title=title,
                email=nearby_email,
                direct_phone=nearby_phone,
                linkedin_url=linkedin[0] if linkedin else "",
            )
        )

    for email in emails:
        if any(record.email.lower() == email.lower() for record in records):
            continue
        records.append(
            _base_contact(
                provider,
                source_url=source_url,
                source_type="website_generic_email",
                email=email,
                email_status="visible",
                department=_department_from_email(email),
                contact_title=_department_from_email(email),
            )
        )

    if not records and phones:
        records.append(
            _base_contact(
                provider,
                source_url=source_url,
                source_type="website_company_phone",
                direct_phone=phones[0],
                contact_title="company phone",
                department="general",
            )
        )
    return records


class FetchedContactPage(tuple):
    __slots__ = ()

    def __new__(cls, text: str, mailtos: list[str], tels: list[str], linkedin_urls: list[str]):
        return tuple.__new__(cls, (text, mailtos, tels, linkedin_urls))

    @property
    def text(self) -> str:
        return self[0]

    @property
    def mailtos(self) -> list[str]:
        return self[1]

    @property
    def tels(self) -> list[str]:
        return self[2]

    @property
    def linkedin_urls(self) -> list[str]:
        return self[3]


def fetch_contact_page(url: str) -> FetchedContactPage | None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; provider-pipeline/1.0)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8, context=ssl_context()) as response:
            if response.headers.get_content_type() != "text/html":
                return None
            charset = response.headers.get_content_charset() or "utf-8"
            html = response.read().decode(charset, errors="replace")
    except (RuntimeError, urllib.error.URLError, TimeoutError, ValueError):
        return None

    parser = ContactPageParser()
    parser.feed(html)
    return FetchedContactPage(
        parser.text(),
        dedupe([email for email in parser.mailtos if email]),
        dedupe([phone for phone in parser.tels if phone]),
        dedupe([urljoin(url, value) if value.startswith("/") else value for value in parser.linkedin_urls]),
    )


def score_contact(contact: ContactRecord) -> ContactRecord:
    title = f" {normalize_whitespace(contact.contact_title).lower()} "
    email = contact.email.lower()
    source_type = contact.source_type.lower()

    seniority = 10
    role_fit = 10
    confidence = 25
    notes: list[str] = []
    unrelated_role = False

    if _contains_any(title, HIGH_PRIORITY_TITLES):
        seniority = 95
        role_fit = 90
        notes.append("decision-maker title")
    elif _contains_any(title, SALES_TITLES):
        seniority = 75
        role_fit = 85
        notes.append("sales or business development title")
    elif _contains_any(title, OPERATIONS_TITLES):
        seniority = 65
        role_fit = 72
        notes.append("operations title")
    elif "generic" in source_type or _is_generic_email(email) or _contains_any(title, LOW_PRIORITY_TITLES):
        seniority = 25
        role_fit = 42 if "sales" in email or "sales" in title else 30
        notes.append("generic route")

    if _contains_any(title, UNRELATED_TITLES):
        unrelated_role = True
        seniority = max(0, seniority - 35)
        role_fit = max(0, role_fit - 50)
        notes.append("unrelated role")

    if contact.contact_name:
        confidence += 25
    if contact.contact_title:
        confidence += 20
    if contact.email:
        confidence += 15
    if contact.direct_phone:
        confidence += 15
    if "fallback" in source_type:
        confidence = min(confidence, 35)
        notes.append("fallback contact")

    if unrelated_role:
        priority = "C"
    elif role_fit >= 78 and confidence >= 60:
        priority = "A"
    elif role_fit >= 50 or confidence >= 55:
        priority = "B"
    else:
        priority = "C"

    recommended_channel = "phone" if contact.direct_phone or contact.company_phone else "email"
    if contact.email and not (contact.direct_phone or contact.company_phone):
        recommended_channel = "email"
    elif contact.email and recommended_channel == "phone":
        recommended_channel = "phone/email"

    return replace(
        contact,
        email_status="visible" if contact.email else "",
        seniority_score=seniority,
        role_fit_score=role_fit,
        confidence=min(confidence, 95),
        recommended_channel=recommended_channel,
        call_priority=priority,
        call_opener=build_contact_call_opener(contact),
        notes="; ".join(dedupe([contact.notes, *notes])),
    )


def dedupe_contacts(contacts: list[ContactRecord]) -> list[ContactRecord]:
    best_by_key: dict[str, ContactRecord] = {}
    for contact in contacts:
        keys = _dedupe_keys(contact)
        key = keys[0]
        current = best_by_key.get(key)
        if current is None or _contact_rank(contact) > _contact_rank(current):
            for old_key, old_contact in list(best_by_key.items()):
                if old_contact is current:
                    del best_by_key[old_key]
            for contact_key in keys:
                best_by_key[contact_key] = contact

    unique = []
    seen_ids: set[int] = set()
    for contact in best_by_key.values():
        if id(contact) in seen_ids:
            continue
        seen_ids.add(id(contact))
        unique.append(contact)
    return unique


def keep_best_contacts_per_company(contacts: list[ContactRecord], limit: int = 3) -> list[ContactRecord]:
    by_company: dict[str, list[ContactRecord]] = {}
    for contact in contacts:
        by_company.setdefault(contact.company.lower(), []).append(contact)

    kept: list[ContactRecord] = []
    for company_contacts in by_company.values():
        company_contacts.sort(key=_contact_rank, reverse=True)
        kept.extend(company_contacts[:limit])
    return kept


def build_contact_call_opener(contact: ContactRecord) -> str:
    category = contact.provider_category
    if category == "bonded_cross_border":
        lead_type = "bonded, sufferance, or cross-border warehousing RFQs"
    elif category == "cold_food_grade":
        lead_type = "cold storage or food-grade warehousing RFQs"
    else:
        lead_type = "qualified logistics RFQs"
    name_part = f"{contact.contact_name}, " if contact.contact_name else ""
    return (
        f"{name_part}I'm building a BC logistics buyer panel where providers only pay "
        f"after they accept a qualified opportunity. Are you the right person to discuss "
        f"{lead_type} for {contact.company}?"
    )


def _extract_name_title_pairs(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    chunks = re.split(r"(?:\s{2,}|[|•·]| - | – | — )", text)
    for index, chunk in enumerate(chunks):
        title = _title_from_text(chunk)
        if not title:
            continue
        before = chunks[index - 1] if index > 0 else ""
        after = chunks[index + 1] if index + 1 < len(chunks) else ""
        name = _name_from_text(before) or _name_from_text(chunk) or _name_from_text(after)
        if name:
            pairs.append((name, title))
    return dedupe_pairs(pairs)


def _title_from_text(value: str) -> str:
    clean = normalize_whitespace(value)
    lowered = f" {clean.lower()} "
    for title in TITLE_KEYWORDS:
        if title in lowered:
            return clean[:80]
    return ""


def _name_from_text(value: str) -> str:
    clean = normalize_whitespace(value)
    matches = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b", clean)
    for match in matches:
        if match.lower() not in {"contact us", "about us", "sales manager", "general manager"}:
            return match
    return ""


def _nearest_email(text: str, name: str, emails: list[str]) -> str:
    if not emails:
        return ""
    position = text.lower().find(name.lower())
    if position < 0:
        return emails[0]
    return min(emails, key=lambda email: abs(text.lower().find(email.lower()) - position) if email.lower() in text.lower() else 99999)


def _nearest_phone(text: str, name: str, phones: list[str]) -> str:
    if not phones:
        return ""
    position = text.lower().find(name.lower())
    if position < 0:
        return phones[0]
    return min(phones, key=lambda phone: abs(text.find(phone) - position) if phone in text else 99999)


def _fallback_contact(provider: ProviderRecord, source_url: str, source_type: str) -> ContactRecord:
    return _base_contact(
        provider,
        source_url=source_url or provider.website,
        source_type=source_type,
        contact_title="company phone" if provider.phone else "company website",
        department="general",
        direct_phone=provider.phone,
        notes="No named website contact found.",
    )


def _base_contact(
    provider: ProviderRecord,
    *,
    source_url: str,
    source_type: str,
    contact_name: str = "",
    contact_title: str = "",
    department: str = "",
    email: str = "",
    email_status: str = "",
    direct_phone: str = "",
    linkedin_url: str = "",
    notes: str = "",
) -> ContactRecord:
    return ContactRecord(
        company=provider.company,
        provider_place_id=provider.place_id,
        provider_category=provider.provider_category,
        provider_priority=provider.priority,
        provider_score=provider.lead_fit_score,
        city=provider.city,
        company_phone=provider.phone,
        company_website=provider.website,
        contact_name=contact_name,
        contact_title=contact_title,
        department=department,
        email=_clean_email(email),
        email_status=email_status or ("visible" if email else ""),
        direct_phone=normalize_phone(direct_phone),
        linkedin_url=linkedin_url,
        source_url=source_url,
        source_type=source_type,
        notes=notes,
    )


def _department_from_email(email: str) -> str:
    local = email.split("@", 1)[0].lower()
    for value in ["sales", "info", "contact", "office", "admin", "support", "customerservice"]:
        if value in local:
            return "customer service" if value == "customerservice" else value
    return "general"


def _dedupe_keys(contact: ContactRecord) -> list[str]:
    company = contact.company.lower()
    keys: list[str] = []
    if contact.email:
        keys.append(f"{company}|email|{contact.email.lower()}")
    phone = normalize_phone(contact.direct_phone or contact.company_phone)
    if phone:
        keys.append(f"{company}|phone|{phone}")
    name_title = normalize_whitespace(f"{contact.contact_name} {contact.contact_title}").lower()
    if name_title:
        keys.append(f"{company}|name|{name_title}")
    return keys or [f"{company}|source|{contact.source_url}|{contact.source_type}"]


def _contact_rank(contact: ContactRecord) -> tuple[int, int, int, int]:
    priority_value = {"A": 3, "B": 2, "C": 1}.get(contact.call_priority, 0)
    return (
        priority_value,
        contact.role_fit_score,
        contact.confidence,
        contact.seniority_score,
    )


def _contains_any(value: str, needles: list[str]) -> bool:
    return any(needle in value for needle in needles)


def _is_generic_email(email: str) -> bool:
    local = email.split("@", 1)[0].lower()
    return local in {"info", "sales", "contact", "office", "admin", "hello"} or local.startswith("sales")


def canonical_website(website: str) -> str:
    value = (website or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return value if value.endswith("/") else value + "/"


def normalize_phone(value: str) -> str:
    value = unquote(value or "")
    value = re.sub(r"^(?:tel:)", "", value, flags=re.IGNORECASE)
    return normalize_whitespace(value)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _clean_email(value: str) -> str:
    return unquote(value or "").strip().strip(".,;:()[]<>").lower()


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if not value:
            continue
        marker = value.lower()
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(value)
    return deduped


def dedupe_pairs(values: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for name, title in values:
        marker = (name.lower(), title.lower())
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append((name, title))
    return deduped
