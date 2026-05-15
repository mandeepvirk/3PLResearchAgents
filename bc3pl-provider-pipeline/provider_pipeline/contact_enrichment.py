from __future__ import annotations

import csv
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from provider_pipeline.models import ContactRecord, ProviderRecord
from provider_pipeline.contacts import normalize_whitespace


HUNTER_ENDPOINT = "https://api.hunter.io/v2/domain-search"
APOLLO_SEARCH_ENDPOINT = "https://api.apollo.io/v1/mixed_people/search"
APOLLO_MATCH_ENDPOINT = "https://api.apollo.io/api/v1/people/match"
APOLLO_BULK_MATCH_ENDPOINT = "https://api.apollo.io/api/v1/people/bulk_match"
HUNTER_DEFAULT_LIMIT = 10
APOLLO_SEARCH_LIMIT = 3
APOLLO_ENRICHMENT_PER_DOMAIN = 1
APOLLO_BULK_MATCH_LIMIT = 10
ERROR_MESSAGE_LIMIT = 180
TARGET_TITLES = [
    "owner",
    "founder",
    "president",
    "ceo",
    "general manager",
    "branch manager",
    "managing director",
    "vp sales",
    "sales director",
    "business development",
    "commercial manager",
    "account executive",
    "sales manager",
    "warehouse manager",
    "operations manager",
    "logistics manager",
    "customs manager",
    "cross-border manager",
    "cold storage manager",
]


ENRICHMENT_DIAGNOSTIC_FIELDS = [
    "run_id",
    "provider_company",
    "provider_domain",
    "provider_place_id",
    "hunter_attempted",
    "hunter_status",
    "hunter_http_status",
    "hunter_error_code",
    "hunter_error_message_short",
    "hunter_results_count",
    "hunter_contacts_added",
    "hunter_cache_hit",
    "apollo_search_attempted",
    "apollo_search_status",
    "apollo_search_http_status",
    "apollo_search_error_code",
    "apollo_search_error_message_short",
    "apollo_search_results_count",
    "apollo_enrichment_attempted",
    "apollo_enrichment_status",
    "apollo_enrichment_http_status",
    "apollo_enrichment_error_code",
    "apollo_enrichment_error_message_short",
    "apollo_enrichment_results_count",
    "apollo_contacts_updated",
    "apollo_contacts_added",
    "apollo_cache_hit",
    "final_contacts_added",
    "notes",
]


@dataclass
class EnrichmentBudget:
    use_hunter: bool = False
    use_apollo: bool = False
    use_apollo_enrich: bool = False
    dry_run: bool = False
    enrichment_limit: int = 0
    apollo_enriched: int = 0
    run_id: str = ""
    domains: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    hunter_calls: int = 0
    apollo_search_calls: int = 0
    apollo_enrichment_calls: int = 0
    apollo_enrichment_candidates: int = 0
    hunter_cached_domains: list[str] = field(default_factory=list)
    apollo_cached_domains: list[str] = field(default_factory=list)
    hunter_planned_domains: list[str] = field(default_factory=list)
    apollo_planned_domains: list[str] = field(default_factory=list)
    hunter_key_visible: bool = False
    apollo_key_visible: bool = False
    diagnostics: list["EnrichmentDiagnostic"] = field(default_factory=list)

    def add_domain(self, domain: str) -> None:
        if domain and domain not in self.domains:
            self.domains.append(domain)

    def load_key_visibility(self) -> None:
        visibility = paid_key_visibility()
        self.hunter_key_visible = visibility["hunter"]
        self.apollo_key_visible = visibility["apollo"]

    def domain_allowed(self, domain: str) -> bool:
        if not domain:
            return False
        if domain in self.allowed_domains:
            return True
        if self.enrichment_limit > 0 and len(self.allowed_domains) >= self.enrichment_limit:
            return False
        self.allowed_domains.append(domain)
        return True

    def summary_counts(self) -> dict[str, int]:
        return enrichment_summary_counts(self.diagnostics)

    def uses_paid_enrichment(self) -> bool:
        return self.use_hunter or self.use_apollo or self.use_apollo_enrich

    def uses_domain_enrichment(self) -> bool:
        return self.use_hunter or self.use_apollo

    def apollo_enrichment_slots_remaining(self) -> int:
        if self.enrichment_limit <= 0:
            return APOLLO_BULK_MATCH_LIMIT
        return max(0, self.enrichment_limit - self.apollo_enrichment_candidates)


@dataclass
class EnrichmentDiagnostic:
    run_id: str = ""
    provider_company: str = ""
    provider_domain: str = ""
    provider_place_id: str = ""
    hunter_attempted: bool = False
    hunter_status: str = "not_attempted"
    hunter_http_status: str = ""
    hunter_error_code: str = ""
    hunter_error_message_short: str = ""
    hunter_results_count: int = 0
    hunter_contacts_added: int = 0
    hunter_cache_hit: bool = False
    apollo_search_attempted: bool = False
    apollo_search_status: str = "not_attempted"
    apollo_search_http_status: str = ""
    apollo_search_error_code: str = ""
    apollo_search_error_message_short: str = ""
    apollo_search_results_count: int = 0
    apollo_enrichment_attempted: bool = False
    apollo_enrichment_status: str = "not_attempted"
    apollo_enrichment_http_status: str = ""
    apollo_enrichment_error_code: str = ""
    apollo_enrichment_error_message_short: str = ""
    apollo_enrichment_results_count: int = 0
    apollo_contacts_updated: int = 0
    apollo_contacts_added: int = 0
    apollo_cache_hit: bool = False
    final_contacts_added: int = 0
    notes: str = ""


@dataclass(frozen=True)
class JsonFetchResult:
    payload: dict
    http_status: str = ""
    error_code: str = ""
    error_message_short: str = ""
    cache_hit: bool = False

    @property
    def ok(self) -> bool:
        return not self.error_code and not self.error_message_short


def paid_key_visibility() -> dict[str, bool]:
    return {
        "hunter": bool(os.getenv("HUNTER_API_KEY", "").strip()),
        "apollo": bool(os.getenv("APOLLO_API_KEY", "").strip()),
    }


def plan_enrichment_for_provider(provider: ProviderRecord, *, budget: EnrichmentBudget, cache_dir: Path) -> None:
    if not budget.uses_domain_enrichment():
        return
    domain = _domain_from_website(provider.website)
    if not domain:
        return
    if not budget.domain_allowed(domain):
        return
    budget.add_domain(domain)
    _record_dry_run(domain, budget, cache_dir)


def enrich_contacts_if_enabled(
    provider: ProviderRecord,
    contacts: list[ContactRecord],
    *,
    budget: EnrichmentBudget | None = None,
    cache_dir: Path | None = None,
) -> list[ContactRecord]:
    if budget is None or not budget.uses_paid_enrichment():
        return contacts
    budget.load_key_visibility()

    domain = _domain_from_website(provider.website)
    diagnostic = _base_diagnostic(provider, domain, budget.run_id)
    if not domain:
        diagnostic.notes = "paid enrichment skipped: no clean website domain"
        budget.diagnostics.append(diagnostic)
        return contacts
    budget.add_domain(domain)

    if budget.uses_domain_enrichment() and not budget.domain_allowed(domain):
        diagnostic.notes = "paid enrichment skipped: enrichment limit reached"
        budget.diagnostics.append(diagnostic)
        return contacts

    cache_base = cache_dir or Path("output") / "contact_enrichment_cache"
    if budget.dry_run:
        if budget.uses_domain_enrichment():
            _record_dry_run(domain, budget, cache_base)
        if budget.use_apollo_enrich:
            _record_apollo_enrichment_dry_run(provider, contacts, domain, budget, cache_base)
        diagnostic.notes = "dry run only; no paid endpoints called"
        budget.diagnostics.append(diagnostic)
        return contacts

    enriched_contacts = list(contacts)
    paid_contacts_added = 0
    if budget.use_hunter:
        api_key = os.getenv("HUNTER_API_KEY", "")
        if not api_key:
            diagnostic.hunter_status = "missing_key"
            diagnostic.notes = _append_note(diagnostic.notes, "Hunter skipped: missing HUNTER_API_KEY")
        else:
            diagnostic.hunter_attempted = True
            result = _load_or_fetch_hunter(domain, api_key, cache_base / "hunter")
            diagnostic.hunter_cache_hit = result.cache_hit
            diagnostic.hunter_http_status = result.http_status
            if not result.ok:
                diagnostic.hunter_error_code = result.error_code
                diagnostic.hunter_error_message_short = result.error_message_short
                diagnostic.hunter_status = _hunter_failure_status(result)
                diagnostic.notes = _append_note(diagnostic.notes, f"Hunter {diagnostic.hunter_status}")
            else:
                payload = result.payload
                hunter_contacts = _contacts_from_hunter(provider, domain, payload)
                diagnostic.hunter_status = "success"
                diagnostic.hunter_results_count = _hunter_results_count(payload)
                diagnostic.hunter_contacts_added = len(hunter_contacts)
                paid_contacts_added += len(hunter_contacts)
                enriched_contacts.extend(hunter_contacts)

    if budget.use_apollo:
        api_key = os.getenv("APOLLO_API_KEY", "")
        if not api_key:
            diagnostic.apollo_search_status = "missing_key"
            diagnostic.apollo_enrichment_status = "missing_key"
            diagnostic.notes = _append_note(diagnostic.notes, "Apollo skipped: missing APOLLO_API_KEY")
        else:
            apollo_contacts = _run_apollo_enrichment(provider, domain, api_key, cache_base, diagnostic)
            budget.apollo_enriched += len(apollo_contacts)
            diagnostic.apollo_contacts_added = len(apollo_contacts)
            paid_contacts_added += len(apollo_contacts)
            if apollo_contacts:
                enriched_contacts.extend(apollo_contacts)

    if budget.use_apollo_enrich:
        api_key = os.getenv("APOLLO_API_KEY", "")
        if not api_key:
            diagnostic.apollo_enrichment_status = "missing_key"
            diagnostic.notes = _append_note(diagnostic.notes, "Apollo enrichment skipped: missing APOLLO_API_KEY")
        else:
            apollo_contacts, updated_count, added_count = _run_apollo_enrichment_only(
                provider,
                contacts,
                domain,
                api_key,
                cache_base,
                budget,
                diagnostic,
            )
            diagnostic.apollo_contacts_updated = updated_count
            diagnostic.apollo_contacts_added += added_count
            paid_contacts_added += added_count
            if apollo_contacts:
                enriched_contacts.extend(apollo_contacts)

    diagnostic.final_contacts_added = paid_contacts_added
    budget.diagnostics.append(diagnostic)
    return enriched_contacts


def _record_dry_run(domain: str, budget: EnrichmentBudget, cache_dir: Path) -> None:
    budget.load_key_visibility()
    if budget.use_hunter and budget.hunter_key_visible:
        if domain not in budget.hunter_cached_domains and domain not in budget.hunter_planned_domains:
            hunter_cache = cache_dir / "hunter" / f"{domain}.json"
            if hunter_cache.exists():
                budget.hunter_cached_domains.append(domain)
            else:
                budget.hunter_planned_domains.append(domain)
                budget.hunter_calls += 1
    if budget.use_apollo and budget.apollo_key_visible:
        if domain not in budget.apollo_cached_domains and domain not in budget.apollo_planned_domains:
            apollo_cache = cache_dir / "apollo" / f"{domain}.json"
            if apollo_cache.exists():
                budget.apollo_cached_domains.append(domain)
            else:
                budget.apollo_planned_domains.append(domain)
                budget.apollo_search_calls += 1
                budget.apollo_enrichment_calls += APOLLO_ENRICHMENT_PER_DOMAIN


def _record_apollo_enrichment_dry_run(
    provider: ProviderRecord,
    contacts: list[ContactRecord],
    domain: str,
    budget: EnrichmentBudget,
    cache_dir: Path,
) -> None:
    budget.load_key_visibility()
    if not budget.apollo_key_visible:
        return
    payloads = apollo_enrichment_payloads(provider, contacts, domain)
    remaining = budget.apollo_enrichment_slots_remaining()
    selected = payloads[:remaining] if budget.enrichment_limit > 0 else payloads
    if not selected:
        return
    budget.apollo_enrichment_candidates += len(selected)
    batches = apollo_enrichment_batches(selected)
    for batch in batches:
        cache_path = _apollo_bulk_cache_path(cache_dir / "apollo_enrichment", batch)
        if cache_path.exists():
            budget.apollo_cached_domains.append(domain)
        else:
            budget.apollo_enrichment_calls += 1


def _run_apollo_enrichment_only(
    provider: ProviderRecord,
    contacts: list[ContactRecord],
    domain: str,
    api_key: str,
    cache_base: Path,
    budget: EnrichmentBudget,
    diagnostic: EnrichmentDiagnostic,
) -> tuple[list[ContactRecord], int, int]:
    payloads = apollo_enrichment_payloads(provider, contacts, domain)
    remaining = budget.apollo_enrichment_slots_remaining()
    selected = payloads[:remaining] if budget.enrichment_limit > 0 else payloads
    if not selected:
        diagnostic.apollo_enrichment_status = "not_attempted"
        diagnostic.notes = _append_note(diagnostic.notes, "Apollo enrichment skipped: no candidate slots remaining")
        return [], 0, 0

    budget.apollo_enrichment_candidates += len(selected)
    diagnostic.apollo_enrichment_attempted = True
    contacts_by_key = _candidate_key_set(selected)
    enriched_contacts: list[ContactRecord] = []
    updated_count = 0
    added_count = 0
    success_count = 0
    failed_count = 0
    cache_hit = False

    for batch in apollo_enrichment_batches(selected):
        result = _load_or_fetch_apollo_bulk_match(
            api_key,
            cache_base / "apollo_enrichment",
            batch,
        )
        cache_hit = cache_hit or result.cache_hit
        if result.http_status:
            diagnostic.apollo_enrichment_http_status = result.http_status
        if not result.ok:
            failed_count += 1
            diagnostic.apollo_enrichment_error_code = result.error_code
            diagnostic.apollo_enrichment_error_message_short = result.error_message_short
            continue

        success_count += 1
        people = _apollo_people(result.payload)
        diagnostic.apollo_enrichment_results_count += len(people)
        for person in people:
            contact = _contact_from_apollo_person(
                provider,
                domain,
                person,
                source_type="apollo_enrichment",
                notes="Apollo people enrichment",
            )
            if contact is None:
                continue
            enriched_contacts.append(contact)
            if _apollo_person_matches_candidate(person, contacts_by_key):
                updated_count += 1
            else:
                added_count += 1

    diagnostic.apollo_cache_hit = cache_hit
    if failed_count:
        diagnostic.apollo_enrichment_status = "failed"
        diagnostic.notes = _append_note(diagnostic.notes, "Apollo enrichment failed")
    elif success_count:
        diagnostic.apollo_enrichment_status = "success"
    else:
        diagnostic.apollo_enrichment_status = "failed"
        diagnostic.notes = _append_note(diagnostic.notes, "Apollo enrichment returned no usable response")
    return enriched_contacts, updated_count, added_count


def _run_apollo_enrichment(
    provider: ProviderRecord,
    domain: str,
    api_key: str,
    cache_base: Path,
    diagnostic: EnrichmentDiagnostic,
) -> list[ContactRecord]:
    contacts: list[ContactRecord] = []
    diagnostic.apollo_search_attempted = True
    search = _load_or_fetch_apollo_search(domain, api_key, cache_base / "apollo_search", limit=APOLLO_SEARCH_LIMIT)
    diagnostic.apollo_cache_hit = search.cache_hit
    diagnostic.apollo_search_http_status = search.http_status
    if not search.ok:
        diagnostic.apollo_search_error_code = search.error_code
        diagnostic.apollo_search_error_message_short = search.error_message_short
        diagnostic.apollo_search_status = _apollo_search_failure_status(search)
        diagnostic.notes = _append_note(diagnostic.notes, f"Apollo search {diagnostic.apollo_search_status}")
        return contacts

    diagnostic.apollo_search_status = "success"
    diagnostic.apollo_search_results_count = _apollo_people_count(search.payload)
    contacts.extend(_contacts_from_apollo(provider, domain, search.payload))

    enrichment_payloads = _apollo_enrichment_inputs(search.payload)
    if not enrichment_payloads:
        diagnostic.apollo_enrichment_status = "not_attempted"
        diagnostic.notes = _append_note(diagnostic.notes, "Apollo enrichment skipped: no search person ids")
        return contacts

    diagnostic.apollo_enrichment_attempted = True
    enrichment_success = 0
    enrichment_cache_hit = False
    for payload in enrichment_payloads[:APOLLO_ENRICHMENT_PER_DOMAIN]:
        result = _load_or_fetch_apollo_match(domain, api_key, cache_base / "apollo_enrichment", payload)
        enrichment_cache_hit = enrichment_cache_hit or result.cache_hit
        if result.http_status:
            diagnostic.apollo_enrichment_http_status = result.http_status
        if not result.ok:
            diagnostic.apollo_enrichment_error_code = result.error_code
            diagnostic.apollo_enrichment_error_message_short = result.error_message_short
            diagnostic.apollo_enrichment_status = "failed"
            diagnostic.notes = _append_note(diagnostic.notes, "Apollo enrichment failed")
            continue
        enrichment_success += 1
        diagnostic.apollo_enrichment_results_count += _apollo_people_count(result.payload)
        contacts.extend(_contacts_from_apollo(provider, domain, result.payload))

    diagnostic.apollo_cache_hit = diagnostic.apollo_cache_hit or enrichment_cache_hit
    if enrichment_success:
        diagnostic.apollo_enrichment_status = "success"
    elif diagnostic.apollo_enrichment_attempted and diagnostic.apollo_enrichment_status != "failed":
        diagnostic.apollo_enrichment_status = "failed"
    return contacts


def _load_or_fetch_hunter(domain: str, api_key: str, cache_dir: Path) -> JsonFetchResult:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{domain}.json"
    if cache_path.exists():
        return JsonFetchResult(
            payload=json.loads(cache_path.read_text(encoding="utf-8")),
            cache_hit=True,
        )

    params = urllib.parse.urlencode({"domain": domain, "api_key": api_key, "limit": HUNTER_DEFAULT_LIMIT})
    request = urllib.request.Request(
        f"{HUNTER_ENDPOINT}?{params}",
        headers={"User-Agent": "provider-pipeline/1.0"},
    )
    return _fetch_json(request, cache_path)


def _load_or_fetch_apollo_search(domain: str, api_key: str, cache_dir: Path, limit: int) -> JsonFetchResult:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{domain}.json"
    if cache_path.exists():
        return JsonFetchResult(
            payload=json.loads(cache_path.read_text(encoding="utf-8")),
            cache_hit=True,
        )

    payload = {
        "q_organization_domains": domain,
        "person_titles": TARGET_TITLES,
        "per_page": max(1, min(limit, 10)),
        "page": 1,
    }
    request = urllib.request.Request(
        APOLLO_SEARCH_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": api_key,
            "User-Agent": "provider-pipeline/1.0",
        },
        method="POST",
    )
    return _fetch_json(request, cache_path)


def _load_or_fetch_apollo_match(domain: str, api_key: str, cache_dir: Path, payload: dict) -> JsonFetchResult:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = _safe_cache_key(str(payload.get("id") or payload.get("email") or payload.get("name") or "unknown"))
    cache_path = cache_dir / f"{domain}_{cache_key}.json"
    if cache_path.exists():
        return JsonFetchResult(
            payload=json.loads(cache_path.read_text(encoding="utf-8")),
            cache_hit=True,
        )

    request_payload = _apollo_safe_payload(dict(payload))
    request = urllib.request.Request(
        _apollo_endpoint_with_safe_flags(APOLLO_MATCH_ENDPOINT),
        data=json.dumps(request_payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": api_key,
            "User-Agent": "provider-pipeline/1.0",
        },
        method="POST",
    )
    return _fetch_json(request, cache_path)


def _load_or_fetch_apollo_bulk_match(
    api_key: str,
    cache_dir: Path,
    payloads: list[dict],
) -> JsonFetchResult:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = _apollo_bulk_cache_path(cache_dir, payloads)
    if cache_path.exists():
        return JsonFetchResult(
            payload=json.loads(cache_path.read_text(encoding="utf-8")),
            cache_hit=True,
        )

    request = urllib.request.Request(
        _apollo_endpoint_with_safe_flags(APOLLO_BULK_MATCH_ENDPOINT),
        data=json.dumps({"details": [_apollo_safe_payload(payload) for payload in payloads]}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": api_key,
            "User-Agent": "provider-pipeline/1.0",
        },
        method="POST",
    )
    return _fetch_json(request, cache_path)


def _fetch_json(request: urllib.request.Request, cache_path: Path) -> JsonFetchResult:
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        code, message = _error_code_message(body)
        return JsonFetchResult(
            payload={},
            http_status=str(error.code),
            error_code=code or f"http_{error.code}",
            error_message_short=message or _short_error(body),
        )
    except urllib.error.URLError as error:
        return JsonFetchResult(
            payload={},
            error_code="url_error",
            error_message_short=_short_error(str(error.reason)),
        )
    except TimeoutError as error:
        return JsonFetchResult(
            payload={},
            error_code="timeout",
            error_message_short=_short_error(str(error)),
        )
    except json.JSONDecodeError as error:
        return JsonFetchResult(
            payload={},
            error_code="invalid_json",
            error_message_short=_short_error(str(error)),
        )

    cache_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return JsonFetchResult(payload=data)


def _contacts_from_hunter(provider: ProviderRecord, domain: str, payload: dict) -> list[ContactRecord]:
    emails = payload.get("data", {}).get("emails", [])
    contacts: list[ContactRecord] = []
    for item in emails:
        if not isinstance(item, dict):
            continue
        email = str(item.get("value") or "").strip().lower()
        if not email:
            continue
        first_name = str(item.get("first_name") or "").strip()
        last_name = str(item.get("last_name") or "").strip()
        full_name = normalize_whitespace(f"{first_name} {last_name}")
        title = normalize_whitespace(str(item.get("position") or ""))
        department = normalize_whitespace(str(item.get("department") or ""))
        seniority = normalize_whitespace(str(item.get("seniority") or ""))
        verification = item.get("verification") if isinstance(item.get("verification"), dict) else {}
        status = str(verification.get("status") or item.get("confidence") or "")
        linkedin_url = str(item.get("linkedin") or item.get("linkedin_url") or "")
        confidence = _int(item.get("confidence"), 60)
        if seniority and seniority not in title.lower():
            title = normalize_whitespace(f"{title} {seniority}") if title else seniority
        contacts.append(
            ContactRecord(
                company=provider.company,
                provider_place_id=provider.place_id,
                provider_category=provider.provider_category,
                provider_priority=provider.priority,
                provider_score=provider.lead_fit_score,
                city=provider.city,
                company_phone=provider.phone,
                company_website=provider.website,
                contact_name=full_name,
                contact_title=title,
                department=department,
                email=email,
                email_status=status,
                linkedin_url=linkedin_url,
                source_url=f"hunter:{domain}",
                source_type="hunter",
                contact_quality="named_person" if full_name else "personal_email_unknown_name",
                confidence=max(0, min(confidence, 95)),
                notes="Hunter domain search",
            )
        )
    return contacts


def _contacts_from_apollo(
    provider: ProviderRecord,
    domain: str,
    payload: dict,
    *,
    source_type: str = "apollo",
    notes: str = "Apollo people search",
) -> list[ContactRecord]:
    people = _apollo_people(payload)
    contacts: list[ContactRecord] = []
    for item in people:
        if not isinstance(item, dict):
            continue
        contact = _contact_from_apollo_person(provider, domain, item, source_type=source_type, notes=notes)
        if contact is not None:
            contacts.append(contact)
    return contacts


def _contact_from_apollo_person(
    provider: ProviderRecord,
    domain: str,
    item: dict,
    *,
    source_type: str,
    notes: str,
) -> ContactRecord | None:
    email = str(item.get("email") or "").strip().lower()
    name = normalize_whitespace(
        str(item.get("name") or normalize_whitespace(f"{item.get('first_name', '')} {item.get('last_name', '')}"))
    )
    title = normalize_whitespace(str(item.get("title") or item.get("headline") or ""))
    organization = item.get("organization") if isinstance(item.get("organization"), dict) else {}
    phone_numbers = item.get("phone_numbers") if isinstance(item.get("phone_numbers"), list) else []
    phone = str(item.get("phone") or organization.get("phone") or _first_phone(phone_numbers) or "")
    linkedin_url = str(item.get("linkedin_url") or "")
    departments = item.get("departments") if isinstance(item.get("departments"), list) else []
    department = normalize_whitespace(str(item.get("department") or ", ".join(str(value) for value in departments)))
    seniority = normalize_whitespace(str(item.get("seniority") or ""))
    if seniority and seniority not in title.lower():
        title = normalize_whitespace(f"{title} {seniority}") if title else seniority
    if not (name or email or phone or title):
        return None
    return ContactRecord(
        company=provider.company,
        provider_place_id=provider.place_id,
        provider_category=provider.provider_category,
        provider_priority=provider.priority,
        provider_score=provider.lead_fit_score,
        city=provider.city,
        company_phone=provider.phone,
        company_website=provider.website,
        contact_name=name,
        contact_title=title,
        department=department,
        email=email,
        email_status=str(item.get("email_status") or ""),
        direct_phone=phone,
        linkedin_url=linkedin_url,
        source_url=f"apollo:{domain}",
        source_type=source_type,
        contact_quality="named_person" if name else "personal_email_unknown_name",
        confidence=80 if email else 65,
        notes=notes,
    )


def write_enrichment_diagnostics_csv(path: Path, rows: list[EnrichmentDiagnostic]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ENRICHMENT_DIAGNOSTIC_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_diagnostic_row(row))


def write_enrichment_diagnostics_jsonl(path: Path, rows: list[EnrichmentDiagnostic]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(_diagnostic_row(row), ensure_ascii=True))
            handle.write("\n")


def enrichment_summary_counts(rows: list[EnrichmentDiagnostic]) -> dict[str, int]:
    return {
        "hunter_domains_attempted": _unique_domain_count(rows, "hunter_attempted"),
        "hunter_success": _unique_status_count(rows, "hunter_status", "success"),
        "hunter_limited": _unique_status_count(rows, "hunter_status", "limited"),
        "hunter_failed": _unique_status_count(rows, "hunter_status", "failed"),
        "hunter_contacts_added": sum(row.hunter_contacts_added for row in rows),
        "apollo_search_attempted": _unique_domain_count(rows, "apollo_search_attempted"),
        "apollo_search_success": _unique_status_count(rows, "apollo_search_status", "success"),
        "apollo_search_inaccessible": _unique_status_count(rows, "apollo_search_status", "inaccessible"),
        "apollo_search_failed": _unique_status_count(rows, "apollo_search_status", "failed"),
        "apollo_enrichment_attempted": _unique_domain_count(rows, "apollo_enrichment_attempted"),
        "apollo_enrichment_success": _unique_status_count(rows, "apollo_enrichment_status", "success"),
        "apollo_enrichment_failed": _unique_status_count(rows, "apollo_enrichment_status", "failed"),
        "apollo_contacts_updated": sum(row.apollo_contacts_updated for row in rows),
        "apollo_contacts_added": sum(row.apollo_contacts_added for row in rows),
    }


def _base_diagnostic(provider: ProviderRecord, domain: str, run_id: str) -> EnrichmentDiagnostic:
    return EnrichmentDiagnostic(
        run_id=run_id,
        provider_company=provider.company,
        provider_domain=domain,
        provider_place_id=provider.place_id,
    )


def _diagnostic_row(row: EnrichmentDiagnostic) -> dict[str, str]:
    data = asdict(row)
    return {field: _diagnostic_value(data.get(field, "")) for field in ENRICHMENT_DIAGNOSTIC_FIELDS}


def _diagnostic_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _unique_domain_count(rows: list[EnrichmentDiagnostic], bool_field: str) -> int:
    return len(
        {
            row.provider_domain
            for row in rows
            if row.provider_domain and bool(getattr(row, bool_field))
        }
    )


def _unique_status_count(rows: list[EnrichmentDiagnostic], status_field: str, status: str) -> int:
    return len(
        {
            row.provider_domain
            for row in rows
            if row.provider_domain and getattr(row, status_field) == status
        }
    )


def _hunter_results_count(payload: dict) -> int:
    emails = payload.get("data", {}).get("emails", [])
    return len(emails) if isinstance(emails, list) else 0


def _apollo_people(payload: dict) -> list:
    if isinstance(payload.get("people"), list):
        return payload["people"]
    if isinstance(payload.get("contacts"), list):
        return payload["contacts"]
    if isinstance(payload.get("matches"), list):
        people = []
        for match in payload["matches"]:
            if isinstance(match, dict):
                person = match.get("person") or match.get("contact")
                if isinstance(person, dict):
                    people.append(person)
        return people
    if isinstance(payload.get("person"), dict):
        return [payload["person"]]
    if isinstance(payload.get("contact"), dict):
        return [payload["contact"]]
    return []


def _apollo_people_count(payload: dict) -> int:
    return len(_apollo_people(payload))


def _apollo_enrichment_inputs(payload: dict) -> list[dict]:
    inputs = []
    for person in _apollo_people(payload):
        if not isinstance(person, dict):
            continue
        person_id = person.get("id") or person.get("person_id")
        if person_id:
            inputs.append({"id": str(person_id)})
    return inputs


def apollo_enrichment_payloads(
    provider: ProviderRecord,
    contacts: list[ContactRecord],
    domain: str,
) -> list[dict]:
    payloads: list[dict] = []
    seen: set[str] = set()
    for contact in contacts:
        payload = apollo_enrichment_payload(provider, contact, domain)
        if not payload:
            continue
        key = json.dumps(payload, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        payloads.append(payload)
    return payloads


def apollo_enrichment_payload(
    provider: ProviderRecord,
    contact: ContactRecord,
    domain: str,
) -> dict:
    payload: dict[str, str] = {}
    email = normalize_whitespace(contact.email).lower()
    name = normalize_whitespace(contact.contact_name)
    title = normalize_whitespace(contact.contact_title)
    if email:
        payload["email"] = email
    if name:
        payload["name"] = name
        parts = name.split()
        if len(parts) >= 2:
            payload["first_name"] = parts[0]
            payload["last_name"] = parts[-1]
    if domain:
        payload["domain"] = domain
    if provider.company:
        payload["organization_name"] = provider.company
    if title and title.lower() not in {"personal email", "company phone"}:
        payload["title"] = title
    if not (payload.get("email") or payload.get("name")):
        return {}
    return payload


def apollo_enrichment_batches(payloads: list[dict]) -> list[list[dict]]:
    return [
        payloads[index : index + APOLLO_BULK_MATCH_LIMIT]
        for index in range(0, len(payloads), APOLLO_BULK_MATCH_LIMIT)
    ]


def _candidate_key_set(payloads: list[dict]) -> set[str]:
    keys = set()
    for payload in payloads:
        email = normalize_whitespace(str(payload.get("email") or "")).lower()
        name = normalize_whitespace(str(payload.get("name") or "")).lower()
        if email:
            keys.add(f"email:{email}")
        if name:
            keys.add(f"name:{name}")
    return keys


def _apollo_person_matches_candidate(person: dict, keys: set[str]) -> bool:
    email = normalize_whitespace(str(person.get("email") or "")).lower()
    name = normalize_whitespace(str(person.get("name") or "")).lower()
    return bool((email and f"email:{email}" in keys) or (name and f"name:{name}" in keys))


def _apollo_safe_payload(payload: dict) -> dict:
    data = dict(payload)
    data["reveal_personal_emails"] = False
    data["reveal_phone_number"] = False
    data["run_waterfall_email"] = False
    data["run_waterfall_phone"] = False
    return data


def _apollo_endpoint_with_safe_flags(endpoint: str) -> str:
    query = urllib.parse.urlencode(
        {
            "reveal_personal_emails": "false",
            "reveal_phone_number": "false",
            "run_waterfall_email": "false",
            "run_waterfall_phone": "false",
        }
    )
    return f"{endpoint}?{query}"


def _apollo_bulk_cache_path(cache_dir: Path, payloads: list[dict]) -> Path:
    digest = hashlib.sha1(
        json.dumps([_apollo_safe_payload(payload) for payload in payloads], sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return cache_dir / f"bulk_{digest}.json"


def _hunter_failure_status(result: JsonFetchResult) -> str:
    text = f"{result.error_code} {result.error_message_short}".lower()
    if result.http_status == "429" or "limit" in text or "rate" in text:
        return "limited"
    return "failed"


def _apollo_search_failure_status(result: JsonFetchResult) -> str:
    if result.http_status in {"401", "402", "403"}:
        return "inaccessible"
    return "failed"


def _error_code_message(body: str) -> tuple[str, str]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return "", _short_error(body)
    if isinstance(data, dict):
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                code = first.get("code") or first.get("id") or first.get("type") or first.get("name") or ""
                message = first.get("message") or first.get("details") or first.get("detail") or ""
                return str(code), _short_error(str(message or body))
        error = data.get("error")
        if isinstance(error, dict):
            code = error.get("code") or error.get("type") or error.get("name") or ""
            message = error.get("message") or error.get("description") or ""
            return str(code), _short_error(str(message or body))
        if isinstance(error, str):
            return "", _short_error(error)
        message = data.get("message") or data.get("error_description") or ""
        if message:
            return "", _short_error(str(message))
    return "", _short_error(body)


def _short_error(value: str) -> str:
    return normalize_whitespace(value)[:ERROR_MESSAGE_LIMIT]


def _append_note(existing: str, note: str) -> str:
    if not existing:
        return note
    if note in existing.split("; "):
        return existing
    return f"{existing}; {note}"


def _safe_cache_key(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)
    return safe[:80] or "unknown"


def _domain_from_website(website: str) -> str:
    parsed = urlparse(website or "")
    if parsed.scheme not in {"http", "https"}:
        return ""
    host = parsed.netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return ""
    return host


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first_phone(phone_numbers: list) -> str:
    for item in phone_numbers:
        if isinstance(item, dict):
            value = item.get("raw_number") or item.get("sanitized_number") or item.get("number")
            if value:
                return str(value)
        elif item:
            return str(item)
    return ""
