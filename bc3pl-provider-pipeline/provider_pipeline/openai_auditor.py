from __future__ import annotations

import json
import os
from typing import Any

from provider_pipeline.model_config import review_model
from provider_pipeline.models import ProviderRecord


AUDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verification_status": {
            "type": "string",
            "enum": ["approved", "review", "rejected"],
        },
        "audit_notes": {"type": "string"},
        "verification_notes": {"type": "string"},
        "recommended_call_angle": {"type": "string"},
        "confidence": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },
        "rejection_reasons": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "verification_status",
        "audit_notes",
        "verification_notes",
        "recommended_call_angle",
        "confidence",
        "rejection_reasons",
    ],
}


SYSTEM_PROMPT = """
You are the high-value verification reviewer for a BC logistics RFQ buyer-panel pipeline.

Your job is to review already-collected evidence for logistics providers and decide whether the provider is worth calling.
Use web search only to clarify the company and service evidence. Do not invent capabilities.

Important rules:
- Deterministic service flags are authoritative unless the evidence clearly contradicts them.
- Do not mark unsupported bonded, sufferance, cold storage, food-grade, reefer, or warehousing claims as true.
- You may keep an approved/review status, downgrade approved to review, or reject obvious junk.
- Upgrade review to approved only when explicit evidence supports target warehousing services and contactability.
- Reject self-storage, consumer moving/storage, parcel/courier-only, outside-BC, and companies with no useful logistics-provider evidence.
- Keep notes short and practical for a human caller.
""".strip()


class OpenAIProviderAuditor:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or review_model()
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for provider audit.")

        try:
            from openai import OpenAI
        except ImportError as error:
            raise RuntimeError("Install dependencies first: pip install -r requirements.txt") from error

        self.client = OpenAI(api_key=self.api_key)

    def audit(self, record: ProviderRecord) -> ProviderRecord:
        context = {
            "company": record.company,
            "city": record.city,
            "province": record.province,
            "address": record.formatted_address,
            "phone": record.phone,
            "website": record.website,
            "google_maps_url": record.google_maps_url,
            "target_categories": record.target_categories,
            "source_queries": record.source_queries,
            "provider_category": record.provider_category,
            "services": record.services,
            "flags": {
                "has_bonded": record.has_bonded,
                "has_sufferance": record.has_sufferance,
                "has_cross_border": record.has_cross_border,
                "has_cold_storage": record.has_cold_storage,
                "has_food_grade": record.has_food_grade,
                "has_reefer_transport": record.has_reefer_transport,
            },
            "matched_keywords": record.matched_keywords,
            "evidence_urls": record.evidence_urls,
            "evidence_notes": record.evidence_notes,
            "evidence_items": [
                {
                    "source_url": item.source_url,
                    "source_type": item.source_type,
                    "matched_keywords": item.matched_keywords,
                    "evidence_text": item.evidence_text,
                    "confidence": item.confidence,
                }
                for item in record.evidence_items
            ],
            "current_verification_status": record.verification_status,
            "current_verification_notes": record.verification_notes,
            "current_rejection_reasons": record.rejection_reasons,
            "current_confidence": record.confidence,
            "current_call_angle": record.recommended_call_angle,
        }

        response = self.client.responses.create(
            model=self.model,
            tools=[{"type": "web_search"}],
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Audit this provider for call-list quality. Return only JSON matching the schema.\n"
                        + json.dumps(context, ensure_ascii=True)
                    ),
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "provider_audit",
                    "schema": AUDIT_SCHEMA,
                    "strict": True,
                }
            },
        )
        payload = json.loads(response.output_text)
        return apply_audit(record, payload, self.model)


def apply_audit(record: ProviderRecord, payload: dict[str, Any], model: str) -> ProviderRecord:
    status = payload.get("verification_status")
    if status in {"approved", "review", "rejected"}:
        record.verification_status = status

    notes = payload.get("verification_notes")
    if notes:
        record.verification_notes = notes

    audit_notes = payload.get("audit_notes")
    if audit_notes:
        record.audit_notes = audit_notes

    call_angle = payload.get("recommended_call_angle")
    if call_angle:
        record.recommended_call_angle = call_angle

    rejection_reasons = payload.get("rejection_reasons")
    if isinstance(rejection_reasons, list):
        record.rejection_reasons = [str(reason) for reason in rejection_reasons if str(reason)]

    try:
        record.confidence = max(0, min(100, int(payload.get("confidence", record.confidence))))
    except (TypeError, ValueError):
        pass

    record.audit_status = "audited"
    record.audit_model = model
    return record
