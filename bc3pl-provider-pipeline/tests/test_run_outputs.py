from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from provider_pipeline.run_outputs import (
    RunOutputSession,
    organize_latest_outputs,
    output_dir_for_single_stage,
)


class RunOutputSessionTests(unittest.TestCase):
    def test_complete_writes_run_manifest_and_mirrors_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "output"
            session = RunOutputSession(
                base_output_dir=output_dir,
                command="run-all",
                metadata={"sample": True},
            )
            (session.run_dir / "providers_scored.csv").write_text("company\nExample\n", encoding="utf-8")
            (session.run_dir / "contact_enrichment_report.csv").write_text("run_id\nrun-1\n", encoding="utf-8")
            (session.run_dir / "contact_enrichment_cache" / "hunter").mkdir(parents=True)
            (session.run_dir / "contact_enrichment_cache" / "hunter" / "example.com.json").write_text(
                "{}\n",
                encoding="utf-8",
            )

            session.complete(
                {
                    "scored_providers": 1,
                    "hunter_domains_attempted": 1,
                    "hunter_success": 1,
                    "hunter_limited": 0,
                    "hunter_failed": 0,
                    "hunter_contacts_added": 2,
                    "apollo_search_attempted": 1,
                    "apollo_search_success": 0,
                    "apollo_search_inaccessible": 1,
                    "apollo_search_failed": 0,
                    "apollo_enrichment_attempted": 0,
                    "apollo_enrichment_success": 0,
                    "apollo_enrichment_failed": 0,
                    "apollo_contacts_added": 0,
                }
            )

            manifest = json.loads((session.run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["run_id"], session.run_id)
            self.assertEqual(manifest["command"], "run-all")
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["counts"]["scored_providers"], 1)
            self.assertEqual(manifest["counts"]["hunter_domains_attempted"], 1)
            self.assertEqual(manifest["counts"]["apollo_search_inaccessible"], 1)
            self.assertTrue(manifest["sample"])

            self.assertFalse((output_dir / "latest" / "providers_scored.csv").exists())
            self.assertFalse((output_dir / "latest" / "manifest.json").exists())
            self.assertFalse((output_dir / "providers_scored.csv").exists())
            self.assertFalse((output_dir / "manifest.json").exists())
            self.assertTrue((output_dir / "OPEN_THIS_FIRST.txt").exists())
            self.assertTrue((output_dir / "latest" / "OPEN_THIS_FIRST.txt").exists())
            self.assertTrue((output_dir / "latest" / "crm" / "providers_scored.csv").exists())
            self.assertTrue((output_dir / "latest" / "debug" / "contact_enrichment_report.csv").exists())
            self.assertTrue(
                (
                    output_dir
                    / "latest"
                    / "debug"
                    / "contact_enrichment_cache"
                    / "hunter"
                    / "example.com.json"
                ).exists()
            )
            self.assertTrue((output_dir / "latest" / "debug" / "manifest.json").exists())
            self.assertEqual(
                sorted(path.name for path in (output_dir / "latest").iterdir()),
                ["OPEN_THIS_FIRST.txt", "crm", "debug", "open_in_sheets"],
            )

    def test_complete_can_skip_latest_mirror_for_sample_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "output"
            session = RunOutputSession(
                base_output_dir=output_dir,
                command="run-all",
                metadata={"sample": True},
                mirror_to_latest=False,
            )
            (session.run_dir / "providers_scored.csv").write_text("company\nExample\n", encoding="utf-8")

            session.complete({"scored_providers": 1})

            self.assertTrue((session.run_dir / "providers_scored.csv").exists())
            self.assertFalse((output_dir / "latest").exists())
            self.assertTrue((output_dir / "OPEN_THIS_FIRST.txt").exists())

    def test_organize_latest_outputs_moves_human_friendly_subfolders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            latest_dir = Path(temp_dir) / "output" / "latest"
            latest_dir.mkdir(parents=True)
            (latest_dir / "call_log.csv").write_text("call_id\ncall_1\n", encoding="utf-8")
            (latest_dir / "provider_contact_call_sheet.csv").write_text("company\nExample\n", encoding="utf-8")
            (latest_dir / "call_sheet.csv").write_text("company\nExample\n", encoding="utf-8")
            (latest_dir / "providers_scored.csv").write_text("company\nExample\n", encoding="utf-8")
            (latest_dir / "provider_contacts_scored.csv").write_text("company\nExample\n", encoding="utf-8")
            (latest_dir / "providers_raw.csv").write_text("company\nExample\n", encoding="utf-8")
            (latest_dir / "providers_raw.jsonl").write_text("{}\n", encoding="utf-8")
            (latest_dir / "manifest.json").write_text("{}\n", encoding="utf-8")

            organize_latest_outputs(latest_dir)

            self.assertTrue((latest_dir / "OPEN_THIS_FIRST.txt").exists())
            self.assertTrue((latest_dir / "open_in_sheets" / "call_log.csv").exists())
            self.assertTrue((latest_dir / "open_in_sheets" / "provider_contact_call_sheet.csv").exists())
            self.assertTrue((latest_dir / "open_in_sheets" / "call_sheet.csv").exists())
            self.assertTrue((latest_dir / "crm" / "providers_scored.csv").exists())
            self.assertTrue((latest_dir / "crm" / "provider_contacts_scored.csv").exists())
            self.assertTrue((latest_dir / "debug" / "providers_raw.csv").exists())
            self.assertTrue((latest_dir / "debug" / "providers_raw.jsonl").exists())
            self.assertTrue((latest_dir / "debug" / "manifest.json").exists())
            self.assertFalse((latest_dir / "call_log.csv").exists())
            self.assertFalse((latest_dir / "providers_raw.jsonl").exists())
            self.assertEqual(
                sorted(path.name for path in latest_dir.iterdir()),
                ["OPEN_THIS_FIRST.txt", "crm", "debug", "open_in_sheets"],
            )


class SingleStageOutputDirTests(unittest.TestCase):
    def test_default_single_stage_uses_latest_and_seeds_from_flat_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()
            (output_dir / "providers_scored.jsonl").write_text("{}\n", encoding="utf-8")
            args = Namespace(output_dir=str(output_dir), output_dir_explicit=False)

            resolved = output_dir_for_single_stage(args)

            self.assertEqual(resolved, output_dir / "latest")
            self.assertTrue((output_dir / "latest" / "debug" / "providers_scored.jsonl").exists())

    def test_explicit_single_stage_output_dir_is_respected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "custom"
            args = Namespace(output_dir=str(output_dir), output_dir_explicit=True)

            self.assertEqual(output_dir_for_single_stage(args), output_dir)


if __name__ == "__main__":
    unittest.main()
