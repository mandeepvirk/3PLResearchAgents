from __future__ import annotations

from provider_pipeline.models import ProviderRecord


def sample_provider_records() -> list[ProviderRecord]:
    return [
        ProviderRecord(
            company="Pacific Customs Warehousing",
            target_categories=["bonded_cross_border"],
            source_queries=["bonded warehouse Vancouver BC"],
            city="Richmond",
            province="BC",
            formatted_address="Richmond, BC, Canada",
            phone="+1 604-555-0101",
            website="https://example.com/pacific-customs",
            google_maps_url="https://maps.google.com/?cid=sample1",
            place_id="sample-place-1",
            place_types=["storage", "point_of_interest"],
        ),
        ProviderRecord(
            company="Lower Mainland Cold Logistics",
            target_categories=["cold_food_grade"],
            source_queries=["cold storage Vancouver BC"],
            city="Delta",
            province="BC",
            formatted_address="Delta, BC, Canada",
            phone="+1 604-555-0102",
            website="https://example.com/lower-mainland-cold",
            google_maps_url="https://maps.google.com/?cid=sample2",
            place_id="sample-place-2",
            place_types=["storage", "food"],
        ),
        ProviderRecord(
            company="Fraser Valley Fulfillment Ltd.",
            target_categories=["general_3pl"],
            source_queries=["3PL warehouse Surrey BC"],
            city="Surrey",
            province="BC",
            formatted_address="Surrey, BC, Canada",
            phone="+1 604-555-0103",
            website="https://example.com/fraser-valley-fulfillment",
            google_maps_url="https://maps.google.com/?cid=sample3",
            place_id="sample-place-3",
            place_types=["storage"],
        ),
    ]
