from __future__ import annotations

import io
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from provider_pipeline.contact_enrichment import (
    EnrichmentBudget,
    EnrichmentDiagnostic,
    _contacts_from_apollo,
    _contacts_from_hunter,
    apollo_enrichment_batches,
    apollo_enrichment_payload,
    enrich_contacts_if_enabled,
    enrichment_summary_counts,
    paid_key_visibility,
    plan_enrichment_for_provider,
    write_enrichment_diagnostics_csv,
    write_enrichment_diagnostics_jsonl,
)
from provider_pipeline.contacts import dedupe_contacts, keep_best_contacts_per_company, score_contact
from provider_pipeline.env import load_env_file
from provider_pipeline.models import ContactRecord, ProviderRecord


def make_provider(**overrides) -> ProviderRecord:
    provider = ProviderRecord(
        company="Example Logistics",
        city="Surrey",
        phone="(604) 555-0100",
        website="https://example.com/",
        place_id="place-1",
        provider_category="bonded_cross_border",
        priority="A",
        lead_fit_score=90,
        verification_status="approved",
    )
    for key, value in overrides.items():
        setattr(provider, key, value)
    return provider


class ContactEnrichmentTests(unittest.TestCase):
    def test_env_loader_exposes_paid_keys_without_using_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "HUNTER_API_KEY=hunter-secret\nAPOLLO_API_KEY=apollo-secret\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                load_env_file(env_path)

                self.assertEqual(paid_key_visibility(), {"hunter": True, "apollo": True})

    def test_apollo_response_mapping(self) -> None:
        provider = make_provider()

        contacts = _contacts_from_apollo(
            provider,
            "example.com",
            {
                "people": [
                    {
                        "name": "Jane Smith",
                        "title": "Operations Manager",
                        "email": "Jane.Smith@Example.com",
                        "email_status": "verified",
                        "linkedin_url": "https://linkedin.com/in/jane-smith",
                        "departments": ["operations"],
                        "seniority": "manager",
                        "phone_numbers": [{"raw_number": "(604) 555-0199"}],
                    }
                ]
            },
        )

        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0].source_type, "apollo")
        self.assertEqual(contacts[0].contact_quality, "named_person")
        self.assertEqual(contacts[0].email, "jane.smith@example.com")
        self.assertEqual(contacts[0].email_status, "verified")
        self.assertEqual(contacts[0].department, "operations")
        self.assertEqual(contacts[0].direct_phone, "(604) 555-0199")

    def test_apollo_enrichment_payload_from_email_domain_and_name(self) -> None:
        provider = make_provider(company="Example Logistics")
        payload = apollo_enrichment_payload(
            provider,
            ContactRecord(
                company=provider.company,
                contact_name="Jane Smith",
                contact_title="Sales Manager",
                email="Jane@Example.com",
            ),
            "example.com",
        )

        self.assertEqual(payload["email"], "jane@example.com")
        self.assertEqual(payload["name"], "Jane Smith")
        self.assertEqual(payload["first_name"], "Jane")
        self.assertEqual(payload["last_name"], "Smith")
        self.assertEqual(payload["domain"], "example.com")
        self.assertEqual(payload["organization_name"], "Example Logistics")
        self.assertEqual(payload["title"], "Sales Manager")

    def test_apollo_bulk_batches_max_ten(self) -> None:
        batches = apollo_enrichment_batches([{"email": f"p{i}@example.com"} for i in range(25)])

        self.assertEqual([len(batch) for batch in batches], [10, 10, 5])

    def test_dry_run_does_not_call_paid_endpoints_and_respects_domain_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            budget = EnrichmentBudget(use_hunter=True, use_apollo=True, dry_run=True, enrichment_limit=2)
            providers = [
                make_provider(company="One", website="https://one.example/"),
                make_provider(company="Two", website="https://two.example/"),
                make_provider(company="Three", website="https://three.example/"),
            ]

            with patch.dict(os.environ, {"HUNTER_API_KEY": "hunter-secret", "APOLLO_API_KEY": "apollo-secret"}):
                with patch("provider_pipeline.contact_enrichment.urllib.request.urlopen") as urlopen:
                    for provider in providers:
                        plan_enrichment_for_provider(provider, budget=budget, cache_dir=Path(tmpdir))

            urlopen.assert_not_called()
            self.assertEqual(budget.domains, ["one.example", "two.example"])
            self.assertEqual(budget.hunter_calls, 2)
            self.assertEqual(budget.apollo_search_calls, 2)
            self.assertEqual(budget.apollo_enrichment_calls, 2)

    def test_dry_run_estimates_zero_calls_when_keys_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            budget = EnrichmentBudget(use_hunter=True, use_apollo=True, dry_run=True, enrichment_limit=10)
            with patch.dict(os.environ, {}, clear=True):
                plan_enrichment_for_provider(make_provider(), budget=budget, cache_dir=Path(tmpdir))

            self.assertEqual(budget.domains, ["example.com"])
            self.assertFalse(budget.hunter_key_visible)
            self.assertFalse(budget.apollo_key_visible)
            self.assertEqual(budget.hunter_calls, 0)
            self.assertEqual(budget.apollo_search_calls, 0)
            self.assertEqual(budget.apollo_enrichment_calls, 0)

    def test_cached_domains_are_not_counted_as_paid_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            (cache_dir / "hunter").mkdir()
            (cache_dir / "apollo").mkdir()
            (cache_dir / "hunter" / "example.com.json").write_text("{}", encoding="utf-8")
            (cache_dir / "apollo" / "example.com.json").write_text("{}", encoding="utf-8")
            budget = EnrichmentBudget(use_hunter=True, use_apollo=True, dry_run=True, enrichment_limit=10)

            with patch.dict(os.environ, {"HUNTER_API_KEY": "hunter-secret", "APOLLO_API_KEY": "apollo-secret"}):
                plan_enrichment_for_provider(make_provider(), budget=budget, cache_dir=cache_dir)

            self.assertEqual(budget.hunter_calls, 0)
            self.assertEqual(budget.apollo_search_calls, 0)
            self.assertEqual(budget.apollo_enrichment_calls, 0)
            self.assertEqual(budget.hunter_cached_domains, ["example.com"])
            self.assertEqual(budget.apollo_cached_domains, ["example.com"])

    def test_dedupe_prefers_paid_named_person_over_website_fallback(self) -> None:
        provider = make_provider()
        hunter = _contacts_from_hunter(
            provider,
            "example.com",
            {
                "data": {
                    "emails": [
                        {
                            "value": "jane@example.com",
                            "first_name": "Jane",
                            "last_name": "Smith",
                            "position": "Sales Director",
                            "confidence": 90,
                        }
                    ]
                }
            },
        )[0]
        fallback = ContactRecord(
            company=provider.company,
            email="jane@example.com",
            source_type="website_generic_email",
            contact_quality="department_email",
            confidence=30,
        )

        deduped = dedupe_contacts([score_contact(fallback), score_contact(hunter)])
        kept = keep_best_contacts_per_company(deduped, limit=1)

        self.assertEqual(kept[0].source_type, "hunter")

    def test_hunter_success_writes_diagnostic_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            budget = EnrichmentBudget(use_hunter=True, run_id="run-1")
            payload = {
                "data": {
                    "emails": [
                        {
                            "value": "jane@example.com",
                            "first_name": "Jane",
                            "last_name": "Smith",
                            "position": "Sales Director",
                            "confidence": 91,
                        }
                    ]
                }
            }

            with patch.dict(os.environ, {"HUNTER_API_KEY": "hunter-secret"}):
                with patch(
                    "provider_pipeline.contact_enrichment.urllib.request.urlopen",
                    return_value=FakeResponse(payload),
                ):
                    contacts = enrich_contacts_if_enabled(
                        make_provider(),
                        [],
                        budget=budget,
                        cache_dir=Path(tmpdir),
                    )

            self.assertEqual(len(contacts), 1)
            row = budget.diagnostics[0]
            self.assertEqual(row.run_id, "run-1")
            self.assertTrue(row.hunter_attempted)
            self.assertEqual(row.hunter_status, "success")
            self.assertEqual(row.hunter_results_count, 1)
            self.assertEqual(row.hunter_contacts_added, 1)
            self.assertEqual(row.final_contacts_added, 1)

    def test_hunter_limit_error_writes_limited_diagnostic_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            budget = EnrichmentBudget(use_hunter=True, run_id="run-1")
            error = http_error(429, {"errors": [{"code": "rate_limit", "message": "Request limit reached"}]})

            with patch.dict(os.environ, {"HUNTER_API_KEY": "hunter-secret"}):
                with patch(
                    "provider_pipeline.contact_enrichment.urllib.request.urlopen",
                    side_effect=error,
                ):
                    contacts = enrich_contacts_if_enabled(
                        make_provider(),
                        [],
                        budget=budget,
                        cache_dir=Path(tmpdir),
                    )

            self.assertEqual(contacts, [])
            row = budget.diagnostics[0]
            self.assertEqual(row.hunter_status, "limited")
            self.assertEqual(row.hunter_http_status, "429")
            self.assertEqual(row.hunter_error_code, "rate_limit")
            self.assertIn("Request limit reached", row.hunter_error_message_short)

    def test_apollo_search_inaccessible_writes_diagnostic_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            budget = EnrichmentBudget(use_apollo=True, run_id="run-1")
            error = http_error(403, {"error": {"code": "forbidden", "message": "People search unavailable"}})

            with patch.dict(os.environ, {"APOLLO_API_KEY": "apollo-secret"}):
                with patch(
                    "provider_pipeline.contact_enrichment.urllib.request.urlopen",
                    side_effect=error,
                ):
                    contacts = enrich_contacts_if_enabled(
                        make_provider(),
                        [],
                        budget=budget,
                        cache_dir=Path(tmpdir),
                    )

            self.assertEqual(contacts, [])
            row = budget.diagnostics[0]
            self.assertTrue(row.apollo_search_attempted)
            self.assertEqual(row.apollo_search_status, "inaccessible")
            self.assertEqual(row.apollo_search_http_status, "403")
            self.assertEqual(row.apollo_enrichment_status, "not_attempted")

    def test_apollo_enrichment_failure_writes_separate_diagnostic_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            budget = EnrichmentBudget(use_apollo=True, run_id="run-1")
            search_payload = {
                "people": [
                    {
                        "id": "person-1",
                        "name": "Jane Smith",
                        "title": "Sales Manager",
                        "email": "jane@example.com",
                    }
                ]
            }
            enrichment_error = http_error(500, {"error": {"code": "server_error", "message": "Match failed"}})

            with patch.dict(os.environ, {"APOLLO_API_KEY": "apollo-secret"}):
                with patch(
                    "provider_pipeline.contact_enrichment.urllib.request.urlopen",
                    side_effect=[FakeResponse(search_payload), enrichment_error],
                ):
                    contacts = enrich_contacts_if_enabled(
                        make_provider(),
                        [],
                        budget=budget,
                        cache_dir=Path(tmpdir),
                    )

            self.assertEqual(len(contacts), 1)
            row = budget.diagnostics[0]
            self.assertEqual(row.apollo_search_status, "success")
            self.assertEqual(row.apollo_search_results_count, 1)
            self.assertTrue(row.apollo_enrichment_attempted)
            self.assertEqual(row.apollo_enrichment_status, "failed")
            self.assertEqual(row.apollo_enrichment_http_status, "500")
            self.assertEqual(row.apollo_enrichment_error_code, "server_error")
            self.assertEqual(row.apollo_contacts_added, 1)

    def test_apollo_enrichment_only_success_updates_candidate_contacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            budget = EnrichmentBudget(use_apollo_enrich=True, run_id="run-1")
            payload = {
                "matches": [
                    {
                        "person": {
                            "name": "Jane Smith",
                            "title": "VP Sales",
                            "email": "jane@example.com",
                            "email_status": "verified",
                        }
                    }
                ]
            }

            with patch.dict(os.environ, {"APOLLO_API_KEY": "apollo-secret"}):
                with patch(
                    "provider_pipeline.contact_enrichment.urllib.request.urlopen",
                    return_value=FakeResponse(payload),
                ):
                    contacts = enrich_contacts_if_enabled(
                        make_provider(),
                        [ContactRecord(company="Example Logistics", email="jane@example.com")],
                        budget=budget,
                        cache_dir=Path(tmpdir),
                    )

            apollo_contacts = [contact for contact in contacts if contact.source_type == "apollo_enrichment"]
            self.assertEqual(len(apollo_contacts), 1)
            self.assertEqual(apollo_contacts[0].contact_name, "Jane Smith")
            self.assertEqual(apollo_contacts[0].contact_title, "VP Sales")
            row = budget.diagnostics[0]
            self.assertFalse(row.apollo_search_attempted)
            self.assertEqual(row.apollo_search_status, "not_attempted")
            self.assertTrue(row.apollo_enrichment_attempted)
            self.assertEqual(row.apollo_enrichment_status, "success")
            self.assertEqual(row.apollo_enrichment_results_count, 1)
            self.assertEqual(row.apollo_contacts_updated, 1)
            self.assertEqual(row.apollo_contacts_added, 0)

    def test_apollo_enrichment_only_error_diagnostics_for_401_403_422(self) -> None:
        for status in [401, 403, 422]:
            with self.subTest(status=status):
                with tempfile.TemporaryDirectory() as tmpdir:
                    budget = EnrichmentBudget(use_apollo_enrich=True, run_id="run-1")
                    error = http_error(status, {"error": {"code": f"error_{status}", "message": "No access"}})

                    with patch.dict(os.environ, {"APOLLO_API_KEY": "apollo-secret"}):
                        with patch(
                            "provider_pipeline.contact_enrichment.urllib.request.urlopen",
                            side_effect=error,
                        ):
                            enrich_contacts_if_enabled(
                                make_provider(),
                                [ContactRecord(company="Example Logistics", email="jane@example.com")],
                                budget=budget,
                                cache_dir=Path(tmpdir),
                            )

                    row = budget.diagnostics[0]
                    self.assertTrue(row.apollo_enrichment_attempted)
                    self.assertEqual(row.apollo_enrichment_status, "failed")
                    self.assertEqual(row.apollo_enrichment_http_status, str(status))
                    self.assertEqual(row.apollo_enrichment_error_code, f"error_{status}")

    def test_apollo_enrichment_dry_run_makes_no_api_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            budget = EnrichmentBudget(use_apollo_enrich=True, dry_run=True, enrichment_limit=1)

            with patch.dict(os.environ, {"APOLLO_API_KEY": "apollo-secret"}):
                with patch("provider_pipeline.contact_enrichment.urllib.request.urlopen") as urlopen:
                    contacts = enrich_contacts_if_enabled(
                        make_provider(),
                        [
                            ContactRecord(company="Example Logistics", email="jane@example.com"),
                            ContactRecord(company="Example Logistics", email="pat@example.com"),
                        ],
                        budget=budget,
                        cache_dir=Path(tmpdir),
                    )

            urlopen.assert_not_called()
            self.assertEqual(len(contacts), 2)
            self.assertEqual(budget.apollo_enrichment_candidates, 1)
            self.assertEqual(budget.apollo_enrichment_calls, 1)

    def test_apollo_enrichment_cache_prevents_duplicate_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = {"matches": [{"person": {"name": "Jane Smith", "email": "jane@example.com"}}]}
            provider = make_provider()
            contact = ContactRecord(company="Example Logistics", email="jane@example.com")

            with patch.dict(os.environ, {"APOLLO_API_KEY": "apollo-secret"}):
                with patch(
                    "provider_pipeline.contact_enrichment.urllib.request.urlopen",
                    return_value=FakeResponse(payload),
                ) as urlopen:
                    enrich_contacts_if_enabled(
                        provider,
                        [contact],
                        budget=EnrichmentBudget(use_apollo_enrich=True),
                        cache_dir=Path(tmpdir),
                    )
                    enrich_contacts_if_enabled(
                        provider,
                        [contact],
                        budget=EnrichmentBudget(use_apollo_enrich=True),
                        cache_dir=Path(tmpdir),
                    )

            self.assertEqual(urlopen.call_count, 1)

    def test_diagnostics_writers_and_summary_counts(self) -> None:
        rows = [
            EnrichmentDiagnostic(
                run_id="run-1",
                provider_company="One",
                provider_domain="one.example",
                hunter_attempted=True,
                hunter_status="success",
                hunter_contacts_added=1,
                apollo_search_attempted=True,
                apollo_search_status="inaccessible",
            ),
            EnrichmentDiagnostic(
                run_id="run-1",
                provider_company="Two",
                provider_domain="two.example",
                hunter_attempted=True,
                hunter_status="limited",
                apollo_search_attempted=True,
                apollo_search_status="success",
                apollo_enrichment_attempted=True,
                apollo_enrichment_status="failed",
                apollo_contacts_updated=1,
                apollo_contacts_added=2,
            ),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "contact_enrichment_report.csv"
            jsonl_path = Path(tmpdir) / "contact_enrichment_report.jsonl"
            write_enrichment_diagnostics_csv(csv_path, rows)
            write_enrichment_diagnostics_jsonl(jsonl_path, rows)

            self.assertIn("hunter_status", csv_path.read_text(encoding="utf-8"))
            self.assertEqual(len(jsonl_path.read_text(encoding="utf-8").splitlines()), 2)

        summary = enrichment_summary_counts(rows)
        self.assertEqual(summary["hunter_domains_attempted"], 2)
        self.assertEqual(summary["hunter_success"], 1)
        self.assertEqual(summary["hunter_limited"], 1)
        self.assertEqual(summary["hunter_contacts_added"], 1)
        self.assertEqual(summary["apollo_search_attempted"], 2)
        self.assertEqual(summary["apollo_search_inaccessible"], 1)
        self.assertEqual(summary["apollo_enrichment_failed"], 1)
        self.assertEqual(summary["apollo_contacts_updated"], 1)
        self.assertEqual(summary["apollo_contacts_added"], 2)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        import json

        return json.dumps(self.payload).encode("utf-8")


def http_error(status: int, payload: dict) -> urllib.error.HTTPError:
    import json

    return urllib.error.HTTPError(
        url="https://api.example.test",
        code=status,
        msg="error",
        hdrs={},
        fp=io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


if __name__ == "__main__":
    unittest.main()
