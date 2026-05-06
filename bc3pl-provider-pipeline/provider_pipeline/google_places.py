from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from provider_pipeline.config import QuerySpec
from provider_pipeline.http_client import ssl_context
from provider_pipeline.models import ProviderRecord


TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.addressComponents",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.websiteUri",
        "places.googleMapsUri",
        "places.types",
        "places.businessStatus",
    ]
)


class GooglePlacesClient:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("GOOGLE_MAPS_API_KEY is required for live discovery.")
        self.api_key = api_key

    def search(self, query_spec: QuerySpec, max_results: int) -> list[ProviderRecord]:
        payload = {
            "textQuery": query_spec.query,
            "languageCode": "en",
            "regionCode": "CA",
            "pageSize": min(max_results, 20),
        }
        data = self._post_json(TEXT_SEARCH_URL, payload)
        places = data.get("places", [])[:max_results]
        return [self._to_record(query_spec, place) for place in places]

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": FIELD_MASK,
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=30,
                context=ssl_context(),
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Google Places request failed: {details}") from error

    def _to_record(self, query_spec: QuerySpec, place: dict[str, Any]) -> ProviderRecord:
        address_components = place.get("addressComponents", [])
        city = _extract_address_component(
            address_components,
            ["locality", "postal_town", "administrative_area_level_3"],
        )
        province = _extract_address_component(
            address_components,
            ["administrative_area_level_1"],
            prefer_short=True,
        )
        display_name = place.get("displayName") or {}
        phone = place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber") or ""

        return ProviderRecord(
            company=display_name.get("text", "").strip(),
            target_categories=[query_spec.target_category],
            source_queries=[query_spec.query],
            city=city,
            province=province,
            formatted_address=place.get("formattedAddress", ""),
            phone=phone,
            website=place.get("websiteUri", ""),
            google_maps_url=place.get("googleMapsUri", ""),
            place_id=place.get("id", ""),
            place_types=place.get("types", []),
        )


def _extract_address_component(
    components: list[dict[str, Any]],
    wanted_types: list[str],
    prefer_short: bool = False,
) -> str:
    for wanted_type in wanted_types:
        for component in components:
            if wanted_type in component.get("types", []):
                key = "shortText" if prefer_short else "longText"
                return component.get(key) or component.get("longText") or ""
    return ""
