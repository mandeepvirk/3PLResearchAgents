from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from provider_pipeline.config import load_config
from provider_pipeline.csv_store import (
    read_contact_records_jsonl,
    read_records_jsonl,
    write_contact_records_jsonl,
    write_contacts_csv,
    write_audited_csv,
    write_enriched_csv,
    write_provider_contact_call_sheet_csv,
    write_raw_csv,
    write_records_jsonl,
    write_verified_csv,
    write_scored_csv,
    write_call_sheet_csv,
)
from provider_pipeline.contact_enrichment import enrich_contacts_if_enabled
from provider_pipeline.contacts import (
    contacts_from_provider,
    dedupe_contacts,
    keep_best_contacts_per_company,
    score_contact,
)
from provider_pipeline.dedupe import dedupe_records
from provider_pipeline.google_places import GooglePlacesClient
from provider_pipeline.models import ProviderRecord
from provider_pipeline.openai_auditor import OpenAIProviderAuditor
from provider_pipeline.openai_enricher import OpenAIProviderEnricher
from provider_pipeline.sample_data import sample_provider_records
from provider_pipeline.scoring import enrich_with_keywords, score_record
from provider_pipeline.verification import verify_records


@dataclass(frozen=True)
class PipelinePaths:
    output_dir: Path

    @property
    def raw_csv(self) -> Path:
        return self.output_dir / "providers_raw.csv"

    @property
    def raw_jsonl(self) -> Path:
        return self.output_dir / "providers_raw.jsonl"

    @property
    def enriched_csv(self) -> Path:
        return self.output_dir / "providers_enriched.csv"

    @property
    def enriched_jsonl(self) -> Path:
        return self.output_dir / "providers_enriched.jsonl"

    @property
    def scored_csv(self) -> Path:
        return self.output_dir / "providers_scored.csv"

    @property
    def scored_jsonl(self) -> Path:
        return self.output_dir / "providers_scored.jsonl"

    @property
    def verified_csv(self) -> Path:
        return self.output_dir / "providers_verified.csv"

    @property
    def verified_jsonl(self) -> Path:
        return self.output_dir / "providers_verified.jsonl"

    @property
    def audited_csv(self) -> Path:
        return self.output_dir / "providers_audited.csv"

    @property
    def audited_jsonl(self) -> Path:
        return self.output_dir / "providers_audited.jsonl"

    @property
    def call_sheet_csv(self) -> Path:
        return self.output_dir / "call_sheet.csv"

    @property
    def contacts_raw_csv(self) -> Path:
        return self.output_dir / "provider_contacts_raw.csv"

    @property
    def contacts_raw_jsonl(self) -> Path:
        return self.output_dir / "provider_contacts_raw.jsonl"

    @property
    def contacts_scored_csv(self) -> Path:
        return self.output_dir / "provider_contacts_scored.csv"

    @property
    def contacts_scored_jsonl(self) -> Path:
        return self.output_dir / "provider_contacts_scored.jsonl"

    @property
    def provider_contact_call_sheet_csv(self) -> Path:
        return self.output_dir / "provider_contact_call_sheet.csv"


@dataclass(frozen=True)
class FinderResult:
    raw_count: int
    deduped_count: int
    csv_path: str
    jsonl_path: str


@dataclass(frozen=True)
class RecordsStageResult:
    record_count: int
    csv_path: str
    jsonl_path: str


@dataclass(frozen=True)
class CallSheetResult:
    call_sheet_count: int
    csv_path: str


@dataclass(frozen=True)
class ContactStageResult:
    contact_count: int
    csv_path: str
    jsonl_path: str


class ProviderFinderAgent:
    def __init__(
        self,
        config_path: Path,
        output_dir: Path,
        max_results_per_query: int,
        use_sample: bool,
    ) -> None:
        self.config_path = config_path
        self.max_results_per_query = max_results_per_query
        self.use_sample = use_sample
        self.paths = PipelinePaths(output_dir)

    def run(self) -> FinderResult:
        config = load_config(self.config_path)
        raw_records = self._collect_records(config.queries)
        deduped_records = dedupe_records(raw_records)

        write_raw_csv(self.paths.raw_csv, deduped_records)
        write_records_jsonl(self.paths.raw_jsonl, deduped_records)

        return FinderResult(
            raw_count=len(raw_records),
            deduped_count=len(deduped_records),
            csv_path=str(self.paths.raw_csv.resolve()),
            jsonl_path=str(self.paths.raw_jsonl.resolve()),
        )

    def _collect_records(self, queries) -> list[ProviderRecord]:
        if self.use_sample:
            print("Using bundled sample provider records.")
            return sample_provider_records()

        api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
        client = GooglePlacesClient(api_key)

        records: list[ProviderRecord] = []
        for query_spec in queries:
            print(f"Searching: {query_spec.query}")
            results = client.search(query_spec, self.max_results_per_query)
            print(f"  found {len(results)}")
            records.extend(results)
        return records


class ProviderEnrichmentAgent:
    def __init__(self, output_dir: Path, use_openai: bool) -> None:
        self.use_openai = use_openai
        self.paths = PipelinePaths(output_dir)

    def run(self) -> RecordsStageResult:
        records = read_records_jsonl(_require_input(self.paths.raw_jsonl, "find-providers"))
        enriched_records = self._enrich_records(records)

        write_enriched_csv(self.paths.enriched_csv, enriched_records)
        write_records_jsonl(self.paths.enriched_jsonl, enriched_records)

        return RecordsStageResult(
            record_count=len(enriched_records),
            csv_path=str(self.paths.enriched_csv.resolve()),
            jsonl_path=str(self.paths.enriched_jsonl.resolve()),
        )

    def _enrich_records(self, records: list[ProviderRecord]) -> list[ProviderRecord]:
        if not self.use_openai:
            print("Using local evidence-based enrichment only.")
            return [enrich_with_keywords(record) for record in records]

        try:
            enricher = OpenAIProviderEnricher()
        except (RuntimeError, ValueError) as error:
            print(f"OpenAI enrichment unavailable: {error}")
            print("Falling back to keyword enrichment.")
            return [enrich_with_keywords(record) for record in records]

        enriched: list[ProviderRecord] = []
        for index, record in enumerate(records, start=1):
            print(f"Enriching {index}/{len(records)}: {record.company}")
            locally_enriched = enrich_with_keywords(record)
            try:
                enriched.append(enricher.enrich(locally_enriched))
            except Exception as error:
                print(f"  OpenAI enrichment failed for {record.company}: {error}")
                enriched.append(locally_enriched)
        return enriched


class ProviderScoringAgent:
    def __init__(self, output_dir: Path) -> None:
        self.paths = PipelinePaths(output_dir)

    def run(self) -> RecordsStageResult:
        records = read_records_jsonl(self._input_path())
        for record in records:
            score_record(record)

        records.sort(key=lambda record: record.lead_fit_score, reverse=True)
        write_scored_csv(self.paths.scored_csv, records)
        write_records_jsonl(self.paths.scored_jsonl, records)

        return RecordsStageResult(
            record_count=len(records),
            csv_path=str(self.paths.scored_csv.resolve()),
            jsonl_path=str(self.paths.scored_jsonl.resolve()),
        )

    def _input_path(self) -> Path:
        if self._fresh_audited_input_exists():
            return self.paths.audited_jsonl
        if self.paths.verified_jsonl.exists():
            return self.paths.verified_jsonl
        return _require_input(self.paths.enriched_jsonl, "enrich-providers")

    def _fresh_audited_input_exists(self) -> bool:
        if not self.paths.audited_jsonl.exists():
            return False
        if not self.paths.verified_jsonl.exists():
            return True
        return self.paths.audited_jsonl.stat().st_mtime >= self.paths.verified_jsonl.stat().st_mtime


class ProviderVerificationAgent:
    def __init__(self, output_dir: Path) -> None:
        self.paths = PipelinePaths(output_dir)

    def run(self) -> RecordsStageResult:
        records = read_records_jsonl(
            _require_input(self.paths.enriched_jsonl, "enrich-providers")
        )
        verified_records = verify_records(records)

        write_verified_csv(self.paths.verified_csv, verified_records)
        write_records_jsonl(self.paths.verified_jsonl, verified_records)

        return RecordsStageResult(
            record_count=len(verified_records),
            csv_path=str(self.paths.verified_csv.resolve()),
            jsonl_path=str(self.paths.verified_jsonl.resolve()),
        )


class ProviderAuditAgent:
    def __init__(self, output_dir: Path, limit: int) -> None:
        self.paths = PipelinePaths(output_dir)
        self.limit = limit

    def run(self) -> RecordsStageResult:
        records = read_records_jsonl(
            _require_input(self.paths.verified_jsonl, "verify-providers")
        )
        audited_records = self._audit_records(records)

        write_audited_csv(self.paths.audited_csv, audited_records)
        write_records_jsonl(self.paths.audited_jsonl, audited_records)

        return RecordsStageResult(
            record_count=len(audited_records),
            csv_path=str(self.paths.audited_csv.resolve()),
            jsonl_path=str(self.paths.audited_jsonl.resolve()),
        )

    def _audit_records(self, records: list[ProviderRecord]) -> list[ProviderRecord]:
        try:
            auditor = OpenAIProviderAuditor()
        except (RuntimeError, ValueError) as error:
            print(f"OpenAI audit unavailable: {error}")
            print("Writing verified records through without audit.")
            for record in records:
                record.audit_status = "audit_unavailable"
                record.audit_notes = str(error)
            return records

        candidates = [
            record
            for record in records
            if record.verification_status in {"approved", "review"}
        ]
        candidates.sort(key=_audit_rank, reverse=True)
        selected = {id(record) for record in candidates[: max(self.limit, 0)]}

        audited: list[ProviderRecord] = []
        selected_count = len(selected)
        audited_count = 0
        for index, record in enumerate(records, start=1):
            if id(record) not in selected:
                if record.verification_status == "rejected":
                    record.audit_status = "skipped_rejected"
                elif self.limit <= 0:
                    record.audit_status = "skipped_limit_zero"
                else:
                    record.audit_status = "skipped_below_limit"
                audited.append(record)
                continue

            audited_count += 1
            print(f"Auditing {audited_count}/{selected_count}: {record.company}")
            try:
                audited.append(auditor.audit(record))
            except Exception as error:
                print(f"  OpenAI audit failed for {record.company}: {error}")
                record.audit_status = "audit_failed"
                record.audit_model = auditor.model
                record.audit_notes = f"Audit failed: {error}"
                audited.append(record)
        return audited


class CallSheetAgent:
    def __init__(self, output_dir: Path) -> None:
        self.paths = PipelinePaths(output_dir)

    def run(self) -> CallSheetResult:
        records = read_records_jsonl(
            _require_input(self.paths.scored_jsonl, "score-providers")
        )
        call_sheet_count = write_call_sheet_csv(self.paths.call_sheet_csv, records)
        return CallSheetResult(
            call_sheet_count=call_sheet_count,
            csv_path=str(self.paths.call_sheet_csv.resolve()),
        )


class ContactFinderAgent:
    def __init__(self, output_dir: Path) -> None:
        self.paths = PipelinePaths(output_dir)

    def run(self) -> ContactStageResult:
        providers = read_records_jsonl(
            _require_input(self.paths.scored_jsonl, "score-providers")
        )
        providers = [
            provider
            for provider in providers
            if provider.verification_status != "rejected"
        ]
        providers.sort(key=_provider_contact_rank)

        contacts = []
        for index, provider in enumerate(providers, start=1):
            print(f"Finding contacts {index}/{len(providers)}: {provider.company}")
            found_contacts = contacts_from_provider(provider)
            contacts.extend(enrich_contacts_if_enabled(provider, found_contacts, enabled=False))

        write_contacts_csv(self.paths.contacts_raw_csv, contacts)
        write_contact_records_jsonl(self.paths.contacts_raw_jsonl, contacts)
        return ContactStageResult(
            contact_count=len(contacts),
            csv_path=str(self.paths.contacts_raw_csv.resolve()),
            jsonl_path=str(self.paths.contacts_raw_jsonl.resolve()),
        )


class ContactRoleScoringAgent:
    def __init__(self, output_dir: Path) -> None:
        self.paths = PipelinePaths(output_dir)

    def run(self) -> ContactStageResult:
        contacts = read_contact_records_jsonl(
            _require_input(self.paths.contacts_raw_jsonl, "find-provider-contacts")
        )
        scored_contacts = [score_contact(contact) for contact in contacts]
        deduped_contacts = dedupe_contacts(scored_contacts)
        kept_contacts = keep_best_contacts_per_company(deduped_contacts, limit=3)
        kept_contacts.sort(key=_contact_output_rank)

        write_contacts_csv(self.paths.contacts_scored_csv, kept_contacts)
        write_contact_records_jsonl(self.paths.contacts_scored_jsonl, kept_contacts)
        return ContactStageResult(
            contact_count=len(kept_contacts),
            csv_path=str(self.paths.contacts_scored_csv.resolve()),
            jsonl_path=str(self.paths.contacts_scored_jsonl.resolve()),
        )


class ProviderContactCallSheetAgent:
    def __init__(self, output_dir: Path) -> None:
        self.paths = PipelinePaths(output_dir)

    def run(self) -> CallSheetResult:
        contacts = read_contact_records_jsonl(
            _require_input(self.paths.contacts_scored_jsonl, "score-provider-contacts")
        )
        call_sheet_count = write_provider_contact_call_sheet_csv(
            self.paths.provider_contact_call_sheet_csv,
            contacts,
        )
        return CallSheetResult(
            call_sheet_count=call_sheet_count,
            csv_path=str(self.paths.provider_contact_call_sheet_csv.resolve()),
        )


def _require_input(path: Path, command_name: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(
            f"Required input file is missing: {path}. Run `python -m provider_pipeline "
            f"{command_name}` first."
        )
    return path


def _provider_contact_rank(record: ProviderRecord) -> tuple[int, int]:
    priority = {"A": 0, "B": 1, "C": 2}.get(record.priority, 3)
    return (priority, -record.lead_fit_score)


def _contact_output_rank(contact) -> tuple[int, int, int, int]:
    provider_priority = {"A": 0, "B": 1, "C": 2}.get(contact.provider_priority, 3)
    contact_priority = {"A": 0, "B": 1, "C": 2}.get(contact.call_priority, 3)
    return (
        provider_priority,
        -contact.provider_score,
        contact_priority,
        -contact.role_fit_score,
    )


def _audit_rank(record: ProviderRecord) -> int:
    score = 0
    if record.verification_status == "approved":
        score += 40
    elif record.verification_status == "review":
        score += 20

    if record.has_bonded or record.has_sufferance:
        score += 25
    if record.has_cold_storage or record.has_food_grade:
        score += 22
    if record.has_cross_border:
        score += 12
    if record.has_reefer_transport:
        score += 8
    if any(item.source_type == "website_page" for item in record.evidence_items):
        score += 15
    if record.phone:
        score += 6
    if record.website:
        score += 4

    score += max(0, min(record.confidence, 100)) // 10
    return score
