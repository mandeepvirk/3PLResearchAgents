from __future__ import annotations

import unittest

from provider_pipeline.contacts import (
    dedupe_contacts,
    extract_contacts_from_page,
    keep_best_contacts_per_company,
    score_contact,
)
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


class ContactExtractionTests(unittest.TestCase):
    def test_extracts_visible_email_phone_and_person(self) -> None:
        provider = make_provider()
        text = (
            "Leadership • Jane Smith • General Manager • "
            "jane@example.com • (604) 555-0199 "
            "Sales: sales@example.com"
        )

        contacts = extract_contacts_from_page(provider, text, "https://example.com/contact")

        self.assertTrue(any(contact.email == "jane@example.com" for contact in contacts))
        self.assertTrue(any(contact.direct_phone == "(604) 555-0199" for contact in contacts))
        self.assertTrue(any(contact.contact_name == "Jane Smith" for contact in contacts))
        self.assertTrue(any(contact.email == "sales@example.com" for contact in contacts))

    def test_keeps_fallback_company_phone_when_no_person_exists(self) -> None:
        provider = make_provider()
        contacts = extract_contacts_from_page(
            provider,
            "Contact our dispatch office at (604) 555-0188.",
            "https://example.com/contact",
        )

        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0].direct_phone, "(604) 555-0188")
        self.assertEqual(contacts[0].source_type, "website_company_phone")


class ContactScoringTests(unittest.TestCase):
    def test_scores_decision_maker_title_highest(self) -> None:
        contact = score_contact(
            ContactRecord(
                company="Example Logistics",
                contact_name="Jane Smith",
                contact_title="President",
                email="jane@example.com",
                company_phone="(604) 555-0100",
                provider_category="general_3pl",
            )
        )

        self.assertEqual(contact.call_priority, "A")
        self.assertGreaterEqual(contact.seniority_score, 90)
        self.assertGreaterEqual(contact.role_fit_score, 85)

    def test_penalizes_unrelated_roles(self) -> None:
        contact = score_contact(
            ContactRecord(
                company="Example Logistics",
                contact_name="Pat Lee",
                contact_title="HR Manager",
                email="pat@example.com",
                company_phone="(604) 555-0100",
            )
        )

        self.assertEqual(contact.call_priority, "C")
        self.assertLess(contact.role_fit_score, 40)
        self.assertIn("unrelated role", contact.notes)

    def test_dedupes_duplicate_contacts_and_keeps_best(self) -> None:
        low = score_contact(
            ContactRecord(
                company="Example Logistics",
                contact_name="Jane Smith",
                contact_title="Reception",
                email="jane@example.com",
            )
        )
        high = score_contact(
            ContactRecord(
                company="Example Logistics",
                contact_name="Jane Smith",
                contact_title="General Manager",
                email="jane@example.com",
            )
        )

        deduped = dedupe_contacts([low, high])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].contact_title, "General Manager")

    def test_keeps_best_three_contacts_per_company(self) -> None:
        contacts = [
            score_contact(ContactRecord(company="Example Logistics", contact_name=f"Person {i}", contact_title=title))
            for i, title in enumerate(["President", "Sales Manager", "Operations Manager", "Reception"])
        ]

        kept = keep_best_contacts_per_company(contacts)

        self.assertEqual(len(kept), 3)
        self.assertFalse(any(contact.contact_title == "Reception" for contact in kept))


if __name__ == "__main__":
    unittest.main()
