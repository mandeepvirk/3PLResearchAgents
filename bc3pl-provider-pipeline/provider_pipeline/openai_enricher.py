from __future__ import annotations

import json
import os
from typing import Any

from provider_pipeline.dedupe import normalize_company_name
from provider_pipeline.model_config import fast_model
from provider_pipeline.models import ProviderRecord


ENRICHMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "services": {
            "type": "array",
            "items": {"type": "string"},
        },
        "evidence_notes": {"type": "string"},
        "recommended_call_angle": {"type": "string"},
        "confidence": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },
    },
    "required": [
        "services",
        "evidence_notes",
        "recommended_call_angle",
        "confidence",
    ],
}


SYSTEM_PROMPT = """
You enrich logistics provider records for a BC RFQ lead business.

Use the provided local evidence and do not invent unsupported capabilities.
Keep the existing deterministic flags and evidence as authoritative.
Return practical services, evidence notes, a call angle, and a conservative confidence score.
""".strip()


class OpenAIProviderEnricher:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or fast_model()
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI enrichment.")

        try:
            from openai import OpenAI
        except ImportError as error:
            raise RuntimeError("Install dependencies first: pip install -r requirements.txt") from error

        self.client = OpenAI(api_key=self.api_key)

    def enrich(self, record: ProviderRecord) -> ProviderRecord:
        context = {
            "company": record.company,
            "city": record.city,
            "province": record.province,
            "address": record.formatted_address,
            "phone": record.phone,
            "website": record.website,
            "google_maps_url": record.google_maps_url,
            "place_types": record.place_types,
            "target_categories_from_search": record.target_categories,
            "source_queries": record.source_queries,
            "provider_category_from_local_evidence": record.provider_category,
            "services_from_local_evidence": record.services,
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
        }

        response = self.client.responses.create(
            model=self.model,
            tools=[{"type": "web_search"}],
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Enrich this provider record. Return only JSON matching the schema.\n"
                        + json.dumps(context, ensure_ascii=True)
                    ),
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "provider_enrichment",
                    "schema": ENRICHMENT_SCHEMA,
                    "strict": True,
                }
            },
        )
        payload = json.loads(response.output_text)
        apply_enrichment(record, payload)
        return record


def apply_enrichment(record: ProviderRecord, payload: dict[str, Any]) -> None:
    record.normalized_company = record.normalized_company or normalize_company_name(record.company)
    services = payload.get("services", [])
    if services:
        record.services = services
    if payload.get("evidence_notes"):
        record.evidence_notes = payload["evidence_notes"]
    if payload.get("recommended_call_angle"):
        record.recommended_call_angle = payload["recommended_call_angle"]
    try:
        record.confidence = max(record.confidence, int(payload.get("confidence", record.confidence)))
    except (TypeError, ValueError):
        pass
