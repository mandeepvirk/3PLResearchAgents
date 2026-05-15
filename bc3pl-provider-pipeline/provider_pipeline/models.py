from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EvidenceItem:
    source_url: str = ""
    source_type: str = ""
    matched_keywords: list[str] = field(default_factory=list)
    evidence_text: str = ""
    confidence: int = 0


@dataclass
class ProviderRecord:
    company: str
    target_categories: list[str] = field(default_factory=list)
    source_queries: list[str] = field(default_factory=list)
    city: str = ""
    province: str = ""
    formatted_address: str = ""
    phone: str = ""
    website: str = ""
    google_maps_url: str = ""
    place_id: str = ""
    place_types: list[str] = field(default_factory=list)

    normalized_company: str = ""
    provider_category: str = "backup_or_unknown"
    services: list[str] = field(default_factory=list)
    has_bonded: bool = False
    has_sufferance: bool = False
    has_cross_border: bool = False
    has_cold_storage: bool = False
    has_food_grade: bool = False
    has_reefer_transport: bool = False
    evidence_items: list[EvidenceItem] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    evidence_urls: list[str] = field(default_factory=list)
    evidence_notes: str = ""
    verification_status: str = "review"
    verification_notes: str = ""
    rejection_reasons: list[str] = field(default_factory=list)
    audit_status: str = "not_run"
    audit_model: str = ""
    audit_notes: str = ""
    recommended_call_angle: str = ""
    confidence: int = 30
    lead_fit_score: int = 0
    priority: str = "C"


@dataclass
class ContactRecord:
    company: str
    provider_place_id: str = ""
    provider_category: str = ""
    provider_priority: str = ""
    provider_score: int = 0
    city: str = ""
    company_phone: str = ""
    company_website: str = ""
    contact_name: str = ""
    contact_title: str = ""
    department: str = ""
    email: str = ""
    email_status: str = ""
    direct_phone: str = ""
    linkedin_url: str = ""
    source_url: str = ""
    source_type: str = ""
    contact_quality: str = ""
    seniority_score: int = 0
    role_fit_score: int = 0
    confidence: int = 0
    recommended_channel: str = ""
    call_priority: str = "C"
    call_opener: str = ""
    notes: str = ""


@dataclass
class CallLogRecord:
    call_id: str
    run_id: str = ""
    company: str = ""
    provider_place_id: str = ""
    provider_category: str = ""
    provider_priority: str = ""
    provider_score: int = 0
    contact_name: str = ""
    contact_title: str = ""
    phone_called: str = ""
    email: str = ""
    contact_quality: str = ""
    source_type: str = ""
    email_status: str = ""
    linkedin_url: str = ""
    department: str = ""
    confidence: int = 0
    call_datetime: str = ""
    call_status: str = "not_called"
    outcome: str = ""
    accepted_standard_price: str = ""
    accepted_exclusive_price: str = ""
    follow_up_date: str = ""
    objections: str = ""
    notes: str = ""
    recording_url: str = ""
    transcript_path: str = ""
    summary: str = ""
    next_action: str = ""


@dataclass(frozen=True)
class PipelineResult:
    raw_count: int
    deduped_count: int
    call_sheet_count: int
    output_dir: str
