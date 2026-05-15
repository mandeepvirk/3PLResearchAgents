from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from provider_pipeline.call_tracking import init_call_log, stable_call_id


CONTACT_FIELDS = [
    "company",
    "provider_place_id",
    "provider_score",
    "provider_category",
    "provider_priority",
    "city",
    "contact_name",
    "contact_title",
    "email",
    "direct_phone",
    "company_phone",
    "recommended_channel",
    "contact_quality",
    "source_type",
    "email_status",
    "linkedin_url",
    "department",
    "confidence",
    "source_url",
    "call_priority",
    "role_fit_score",
    "call_opener",
    "notes",
]


class CallTrackingTests(unittest.TestCase):
    def test_initializes_call_log_from_provider_contact_call_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            _write_contacts(
                output_dir,
                [
                    {
                        "company": "Example Logistics",
                        "provider_place_id": "place-1",
                        "provider_score": "90",
                        "provider_category": "bonded_cross_border",
                        "provider_priority": "A",
                        "contact_name": "Jane Smith",
                        "contact_title": "President",
                        "email": "jane@example.com",
                        "direct_phone": "(604) 555-0101",
                        "company_phone": "(604) 555-0100",
                        "contact_quality": "named_person",
                        "source_type": "website_person",
                        "email_status": "visible",
                        "department": "sales",
                        "confidence": "90",
                    }
                ],
            )

            count, csv_path, jsonl_path = init_call_log(output_dir)

            rows = _read_csv(csv_path)
            self.assertEqual(count, 1)
            self.assertTrue(jsonl_path.exists())
            self.assertEqual(rows[0]["call_status"], "not_called")
            self.assertEqual(rows[0]["company"], "Example Logistics")
            self.assertEqual(rows[0]["provider_place_id"], "place-1")
            self.assertEqual(rows[0]["phone_called"], "(604) 555-0101")
            self.assertEqual(rows[0]["contact_quality"], "named_person")
            self.assertEqual(rows[0]["source_type"], "website_person")

    def test_preserves_existing_status_notes_and_adds_new_contacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            first = {
                "company": "Example Logistics",
                "contact_name": "Jane Smith",
                "contact_title": "President",
                "email": "jane@example.com",
                "direct_phone": "(604) 555-0101",
                "company_phone": "(604) 555-0100",
                "contact_quality": "named_person",
            }
            second = {
                "company": "Second Logistics",
                "contact_name": "Pat Lee",
                "contact_title": "Sales Manager",
                "email": "pat@example.com",
                "direct_phone": "",
                "company_phone": "(604) 555-0200",
                "contact_quality": "named_person",
            }
            _write_contacts(output_dir, [first])
            init_call_log(output_dir)
            rows = _read_csv(output_dir / "call_log.csv")
            rows[0]["call_status"] = "follow_up"
            rows[0]["notes"] = "Asked for details by email."
            _write_raw_csv(output_dir / "call_log.csv", rows, rows[0].keys())
            _write_contacts(output_dir, [first, second])

            count, csv_path, _ = init_call_log(output_dir)

            merged = _read_csv(csv_path)
            self.assertEqual(count, 2)
            existing = next(row for row in merged if row["company"] == "Example Logistics")
            added = next(row for row in merged if row["company"] == "Second Logistics")
            self.assertEqual(existing["call_status"], "follow_up")
            self.assertEqual(existing["notes"], "Asked for details by email.")
            self.assertEqual(added["call_status"], "not_called")
            self.assertEqual(added["phone_called"], "(604) 555-0200")

    def test_stable_call_id_generation(self) -> None:
        first = stable_call_id("Example Logistics", "Jane Smith", "(604) 555-0101", "jane@example.com")
        second = stable_call_id(" example  logistics ", "jane smith", "(604) 555-0101", "JANE@example.com")

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("call_"))

    def test_removes_bad_not_called_rows_without_manual_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            _write_contacts(output_dir, [])
            bad = {
                field: ""
                for field in [
                    "call_id",
                    "run_id",
                    "company",
                    "provider_place_id",
                    "provider_category",
                    "provider_priority",
                    "provider_score",
                    "contact_name",
                    "contact_title",
                    "phone_called",
                    "email",
                    "contact_quality",
                    "source_type",
                    "email_status",
                    "linkedin_url",
                    "department",
                    "confidence",
                    "call_datetime",
                    "call_status",
                    "outcome",
                    "accepted_standard_price",
                    "accepted_exclusive_price",
                    "follow_up_date",
                    "objections",
                    "notes",
                    "recording_url",
                    "transcript_path",
                    "summary",
                    "next_action",
                ]
            }
            bad.update(
                {
                    "call_id": "call_bad",
                    "company": "Bad Logistics",
                    "contact_name": "Main Navigation Skip",
                    "contact_quality": "bad_extraction",
                    "call_status": "not_called",
                }
            )
            _write_raw_csv(output_dir / "call_log.csv", [bad], bad.keys())

            count, csv_path, _ = init_call_log(output_dir)

            self.assertEqual(count, 0)
            self.assertEqual(_read_csv(csv_path), [])

    def test_preserves_manual_row_when_contact_id_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            old = {
                "call_id": "call_old",
                "company": "Example Logistics",
                "contact_name": "Jane Smith",
                "phone_called": "(604) 555-0101",
                "email": "jane@example.com",
                "call_status": "follow_up",
                "notes": "Manual note",
            }
            _write_raw_csv(output_dir / "call_log.csv", [old], old.keys())
            _write_contacts(
                output_dir,
                [
                    {
                        "company": "New Logistics",
                        "contact_name": "Pat Lee",
                        "email": "pat@example.com",
                        "company_phone": "(604) 555-0200",
                    }
                ],
            )

            count, csv_path, _ = init_call_log(output_dir)

            rows = _read_csv(csv_path)
            self.assertEqual(count, 2)
            self.assertTrue(any(row["notes"] == "Manual note" for row in rows))


def _write_contacts(output_dir: Path, rows: list[dict[str, str]]) -> None:
    normalized_rows = []
    for row in rows:
        normalized_rows.append({field: row.get(field, "") for field in CONTACT_FIELDS})
    _write_raw_csv(output_dir / "provider_contact_call_sheet.csv", normalized_rows, CONTACT_FIELDS)


def _write_raw_csv(path: Path, rows, fieldnames) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


if __name__ == "__main__":
    unittest.main()
