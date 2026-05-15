from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from provider_pipeline.csv_store import write_call_sheet_csv
from provider_pipeline.evidence import EvidenceDiagnostics, enrich_provider_record
from provider_pipeline.models import EvidenceItem, ProviderRecord
from provider_pipeline.verification import verify_records


def make_record(**overrides) -> ProviderRecord:
    record = ProviderRecord(
        company="Example Provider",
        target_categories=["general_3pl"],
        source_queries=["3PL warehouse Surrey BC"],
        city="Surrey",
        province="BC",
        formatted_address="123 Example St, Surrey, BC",
        phone="(604) 555-0100",
        website="https://example.com/",
        normalized_company="example provider",
        provider_category="general_3pl",
        services=["general warehousing / 3PL"],
        matched_keywords=["3pl", "warehouse", "warehousing"],
        confidence=80,
        evidence_items=[
            EvidenceItem(
                source_url="https://example.com/",
                source_type="website_page",
                matched_keywords=["3pl", "warehouse", "warehousing"],
                evidence_text="3PL warehousing services",
                confidence=75,
            )
        ],
    )
    for key, value in overrides.items():
        setattr(record, key, value)
    return record


class VerificationTests(unittest.TestCase):
    def test_association_directory_records_are_rejected(self) -> None:
        record = make_record(
            company="Canadian Warehousing Logistics Affiliated",
            normalized_company="canadian warehousing logistics affiliated",
            website="https://www.cwla.ca/",
            evidence_items=[
                EvidenceItem(
                    source_url="https://www.cwla.ca/",
                    source_type="website_page",
                    matched_keywords=["3pl", "warehouse", "warehousing", "distribution"],
                    evidence_text="Warehouse finder member directory for affiliated warehouses.",
                    confidence=75,
                )
            ],
            evidence_notes="Member directory and warehouse finder.",
        )

        verify_records([record])

        self.assertEqual(record.verification_status, "rejected")
        self.assertIn("association or directory listing", record.rejection_reasons)

    def test_provider_url_with_directory_tracking_param_is_not_rejected_as_directory(self) -> None:
        record = make_record(
            company="Evolution Fulfillment",
            normalized_company="evolution fulfillment",
            website="https://www.evolutionfulfillment.com/?utm_source=gb&utm_medium=directory",
            evidence_items=[
                EvidenceItem(
                    source_url="https://www.evolutionfulfillment.com/",
                    source_type="website_page",
                    matched_keywords=["3pl", "fulfillment", "warehouse", "warehousing"],
                    evidence_text="Canadian 3PL fulfillment center and warehousing partner.",
                    confidence=75,
                )
            ],
            evidence_notes="Evidence collected from website_page with matched keywords: 3pl, fulfillment, warehouse.",
        )

        verify_records([record])

        self.assertNotEqual(record.verification_status, "rejected")
        self.assertNotIn("association or directory listing", record.rejection_reasons)

    def test_government_mail_centre_records_are_rejected(self) -> None:
        record = make_record(
            company="Vancouver Customs Mail Centre",
            normalized_company="vancouver customs mail centre",
            website="https://www.cbsa-asfc.gc.ca/do-rb/offices-bureaux/314-eng.html",
            place_types=["government_office"],
            provider_category="bonded_cross_border",
            has_bonded=True,
            has_cross_border=True,
            services=["bonded warehousing", "cross-border logistics"],
            matched_keywords=["customs bonded", "bonded warehouse", "warehouse"],
            evidence_items=[
                EvidenceItem(
                    source_url="https://www.cbsa-asfc.gc.ca/contact",
                    source_type="website_page",
                    matched_keywords=["customs bonded", "warehouse"],
                    evidence_text="Government office customs mail centre contact page.",
                    confidence=75,
                )
            ],
        )

        verify_records([record])

        self.assertEqual(record.verification_status, "rejected")
        self.assertIn("government or customs facility", record.rejection_reasons)

    def test_internal_distribution_centres_without_3pl_evidence_are_rejected(self) -> None:
        record = make_record(
            company="Loblaw Distribution Centre",
            normalized_company="loblaw distribution centre",
            website="",
            phone="(604) 322-3600",
            provider_category="backup_or_unknown",
            services=[],
            matched_keywords=["distribution", "warehouse"],
            evidence_items=[
                EvidenceItem(
                    source_url="https://maps.google.com/?cid=loblaw",
                    source_type="listing_context",
                    matched_keywords=["distribution", "warehouse"],
                    evidence_text="Loblaw Distribution Centre warehouse listing.",
                    confidence=45,
                )
            ],
            confidence=55,
        )

        verify_records([record])

        self.assertEqual(record.verification_status, "rejected")
        self.assertIn("internal distribution centre", record.rejection_reasons)

    def test_listing_only_no_website_without_specialized_evidence_is_rejected(self) -> None:
        record = make_record(
            company="AIRCO Warehouse",
            normalized_company="airco warehouse",
            website="",
            provider_category="backup_or_unknown",
            services=[],
            matched_keywords=["storage", "warehouse"],
            evidence_items=[
                EvidenceItem(
                    source_url="https://maps.google.com/?cid=airco",
                    source_type="listing_context",
                    matched_keywords=["storage", "warehouse"],
                    evidence_text="Warehouse listing without provider website.",
                    confidence=45,
                )
            ],
            confidence=55,
        )

        verify_records([record])

        self.assertEqual(record.verification_status, "rejected")
        self.assertIn("listing-only evidence without provider website", record.rejection_reasons)

    def test_listing_only_specialized_record_without_website_stays_out_of_approved(self) -> None:
        record = make_record(
            company="Metro Cold Storage",
            normalized_company="metro cold storage",
            website="",
            provider_category="cold_food_grade",
            has_cold_storage=True,
            services=["cold storage"],
            matched_keywords=["cold storage", "storage", "warehouse"],
            evidence_items=[
                EvidenceItem(
                    source_url="https://maps.google.com/?cid=metro",
                    source_type="listing_context",
                    matched_keywords=["cold storage", "storage", "warehouse"],
                    evidence_text="Cold storage listing.",
                    confidence=45,
                )
            ],
            confidence=55,
        )

        verify_records([record])

        self.assertEqual(record.verification_status, "review")
        self.assertNotIn("listing-only evidence without provider website", record.rejection_reasons)


class ClassificationTests(unittest.TestCase):
    def test_generic_cross_border_fulfillment_stays_general_3pl(self) -> None:
        record = make_record(
            company="Generic Fulfillment Co",
            normalized_company="generic fulfillment",
            source_queries=["cross border warehousing Vancouver BC"],
            target_categories=["bonded_cross_border"],
            services=[],
            matched_keywords=[],
            evidence_items=[],
        )
        evidence_items = [
            EvidenceItem(
                source_url="https://generic.example/",
                source_type="website_page",
                matched_keywords=["3pl", "fulfillment", "cross-border", "warehouse", "warehousing"],
                evidence_text="3PL fulfillment and cross-border shipping support.",
                confidence=75,
            )
        ]

        with patch(
            "provider_pipeline.evidence.collect_evidence",
            return_value=(evidence_items, EvidenceDiagnostics(fetched_pages=1, pages_with_matches=1)),
        ):
            enriched = enrich_provider_record(record)

        self.assertTrue(enriched.has_cross_border)
        self.assertEqual(enriched.provider_category, "general_3pl")

    def test_explicit_bonded_evidence_keeps_bonded_cross_border(self) -> None:
        record = make_record(
            company="Pacific Customs Warehousing",
            normalized_company="pacific customs warehousing",
            target_categories=["bonded_cross_border"],
            services=[],
            matched_keywords=[],
            evidence_items=[],
        )
        evidence_items = [
            EvidenceItem(
                source_url="https://bonded.example/",
                source_type="website_page",
                matched_keywords=["bonded warehouse", "storage", "warehouse"],
                evidence_text="Customs bonded warehouse services.",
                confidence=75,
            )
        ]

        with patch(
            "provider_pipeline.evidence.collect_evidence",
            return_value=(evidence_items, EvidenceDiagnostics(fetched_pages=1, pages_with_matches=1)),
        ):
            enriched = enrich_provider_record(record)

        self.assertTrue(enriched.has_bonded)
        self.assertEqual(enriched.provider_category, "bonded_cross_border")


class CallSheetTests(unittest.TestCase):
    def test_call_sheet_includes_only_approved_with_phone_and_website(self) -> None:
        approved = make_record(
            company="Approved Provider",
            normalized_company="approved provider",
            verification_status="approved",
            priority="A",
            lead_fit_score=90,
        )
        review = make_record(
            company="Review Provider",
            normalized_company="review provider",
            verification_status="review",
            priority="A",
            lead_fit_score=88,
        )
        no_website = make_record(
            company="No Website Provider",
            normalized_company="no website provider",
            verification_status="approved",
            priority="A",
            lead_fit_score=87,
            website="",
        )
        rejected = make_record(
            company="Rejected Provider",
            normalized_company="rejected provider",
            verification_status="rejected",
            priority="A",
            lead_fit_score=86,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "call_sheet.csv"
            count = write_call_sheet_csv(path, [approved, review, no_website, rejected])

            self.assertEqual(count, 1)
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "Approved Provider")

    def test_cold_food_category_uses_cold_storage_call_opener(self) -> None:
        record = make_record(
            company="BC Freight Solutions Ltd.",
            normalized_company="bc freight solutions",
            verification_status="approved",
            priority="B",
            lead_fit_score=55,
            provider_category="cold_food_grade",
            has_reefer_transport=True,
            services=["reefer logistics"],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "call_sheet.csv"
            write_call_sheet_csv(path, [record])
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        self.assertIn("cold storage or food-grade warehousing", rows[0]["call_opener"])


if __name__ == "__main__":
    unittest.main()
