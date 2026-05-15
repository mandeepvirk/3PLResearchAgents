from __future__ import annotations

import unittest

from provider_pipeline.contacts import (
    dedupe_contacts,
    extract_contacts_from_page,
    filter_call_sheet_contacts,
    keep_best_contacts_per_company,
    score_contact,
)
from provider_pipeline.contact_enrichment import _contacts_from_hunter
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

    def test_rejects_fake_heading_names(self) -> None:
        provider = make_provider()
        contacts = extract_contacts_from_page(
            provider,
            "For Business Enquiries • Sales Manager • Email Us • sales@example.com "
            "Main Navigation Skip • General Manager",
            "https://example.com/contact",
        )
        scored = [score_contact(contact) for contact in contacts]

        self.assertFalse(any(contact.contact_name == "For Business Enquiries" for contact in scored))
        self.assertFalse(any(contact.contact_name == "Main Navigation Skip" for contact in scored))
        self.assertTrue(any(contact.contact_quality == "department_email" for contact in scored))

    def test_rejects_address_or_snippet_text_as_title(self) -> None:
        provider = make_provider()
        contacts = extract_contacts_from_page(
            provider,
            "Jane Smith • 123 Main Street Vancouver BC V6Z 2N2 Warehouse Manager",
            "https://example.com/team",
        )
        scored = [score_contact(contact) for contact in contacts]

        self.assertFalse(any(contact.contact_quality == "named_person" for contact in scored))

    def test_rejects_known_menu_address_and_nav_phrases_as_names(self) -> None:
        provider = make_provider()
        contacts = extract_contacts_from_page(
            provider,
            (
                "Airway Drive Mississauga • Safety Manager • toronto@example.com "
                "Terms Professional • Customer Service • ncs@example.com "
                "More Home • Owner • freight@example.com "
                "Trinity Airways • General Manager • martin@example.com"
            ),
            "https://example.com/contact",
        )
        scored = [score_contact(contact) for contact in contacts]

        blocked_names = {
            "Airway Drive Mississauga",
            "Terms Professional",
            "More Home",
            "Trinity Airways",
        }
        self.assertFalse(any(contact.contact_name in blocked_names for contact in scored))
        self.assertFalse(
            any(
                contact.contact_quality == "named_person"
                and contact.contact_name in blocked_names
                for contact in scored
            )
        )

    def test_generic_email_becomes_department_email(self) -> None:
        provider = make_provider()
        contacts = extract_contacts_from_page(provider, "Sales: sales@example.com", "https://example.com/contact")
        scored = [score_contact(contact) for contact in contacts]

        self.assertEqual(scored[0].contact_quality, "department_email")
        self.assertEqual(scored[0].department, "sales")

    def test_personal_email_becomes_personal_email_unknown_name(self) -> None:
        provider = make_provider()
        contacts = extract_contacts_from_page(provider, "Email elliot.markillie@example.com", "https://example.com/contact")
        scored = [score_contact(contact) for contact in contacts]

        self.assertEqual(scored[0].contact_quality, "personal_email_unknown_name")


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

    def test_hunter_relevant_contact_outranks_department_email(self) -> None:
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
                            "department": "sales",
                            "confidence": 90,
                        }
                    ]
                }
            },
        )[0]
        department = ContactRecord(company="Example Logistics", email="sales@example.com", contact_title="sales")

        kept = keep_best_contacts_per_company([score_contact(department), score_contact(hunter)], limit=1)

        self.assertEqual(kept[0].email, "jane@example.com")
        self.assertEqual(kept[0].source_type, "hunter")

    def test_filters_bad_extraction_from_final_contacts(self) -> None:
        bad = score_contact(
            ContactRecord(
                company="Example Logistics",
                contact_name="Main Navigation Skip",
                contact_title="General Manager",
            )
        )
        good = score_contact(ContactRecord(company="Example Logistics", email="sales@example.com", contact_title="sales"))

        filtered = filter_call_sheet_contacts([bad, good])

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].email, "sales@example.com")


if __name__ == "__main__":
    unittest.main()
