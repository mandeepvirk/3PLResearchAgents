from __future__ import annotations

import filecmp
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


OUTPUT_FILENAMES = [
    "providers_raw.csv",
    "providers_raw.jsonl",
    "providers_enriched.csv",
    "providers_enriched.jsonl",
    "providers_verified.csv",
    "providers_verified.jsonl",
    "providers_audited.csv",
    "providers_audited.jsonl",
    "providers_scored.csv",
    "providers_scored.jsonl",
    "call_sheet.csv",
    "provider_contacts_raw.csv",
    "provider_contacts_raw.jsonl",
    "provider_contacts_scored.csv",
    "provider_contacts_scored.jsonl",
    "provider_contact_call_sheet.csv",
    "contact_enrichment_report.csv",
    "contact_enrichment_report.jsonl",
    "call_log.csv",
    "call_log.jsonl",
]

OPEN_IN_SHEETS_FILENAMES = [
    "call_log.csv",
    "provider_contact_call_sheet.csv",
    "call_sheet.csv",
]

CRM_FILENAMES = [
    "providers_scored.csv",
    "provider_contacts_scored.csv",
]

DEBUG_FILENAMES = [
    "providers_raw.csv",
    "providers_enriched.csv",
    "providers_verified.csv",
    "providers_audited.csv",
    "provider_contacts_raw.csv",
    "contact_enrichment_report.csv",
    "contact_enrichment_report.jsonl",
    "manifest.json",
]

OPEN_THIS_FIRST_TEXT = """BC3PL latest output folder

1. Open output/latest/open_in_sheets/call_log.csv in Google Sheets.
2. Do not edit files in crm/ or debug/.
3. runs/ is the historical archive.

Useful read-only sheets:
- output/latest/open_in_sheets/provider_contact_call_sheet.csv
- output/latest/open_in_sheets/call_sheet.csv

The output/latest root is intentionally clean. Pipeline internals read from the subfolders.
"""

CONTACT_ENRICHMENT_CACHE_DIRNAME = "contact_enrichment_cache"


def latest_file_path(latest_dir: Path, filename: str) -> Path:
    if filename in OPEN_IN_SHEETS_FILENAMES:
        return latest_dir / "open_in_sheets" / filename
    if filename in CRM_FILENAMES:
        return latest_dir / "crm" / filename
    return latest_dir / "debug" / filename


@dataclass
class RunOutputSession:
    base_output_dir: Path
    command: str
    metadata: dict[str, Any] = field(default_factory=dict)
    mirror_to_latest: bool = True

    def __post_init__(self) -> None:
        self.started_at = datetime.now().astimezone()
        base_run_id = f"{self.started_at.strftime('%Y-%m-%d_%H-%M-%S')}_{self.command}"
        self.runs_dir = self.base_output_dir / "runs"
        self.latest_dir = self.base_output_dir / "latest"
        self.run_id, self.run_dir = self._unique_run_path(base_run_id)
        self.run_dir.mkdir(parents=True, exist_ok=False)

    def _unique_run_path(self, base_run_id: str) -> tuple[str, Path]:
        run_id = base_run_id
        run_dir = self.runs_dir / run_id
        suffix = 2
        while run_dir.exists():
            run_id = f"{base_run_id}-{suffix}"
            run_dir = self.runs_dir / run_id
            suffix += 1
        return run_id, run_dir

    def seed_from_latest_or_flat(self) -> None:
        source_dir = self.latest_dir if self.latest_dir.exists() else self.base_output_dir
        for filename in OUTPUT_FILENAMES:
            source = _existing_source(source_dir, filename)
            if source.exists():
                shutil.copy2(source, self.run_dir / filename)
        cache_source = _existing_cache_source(source_dir)
        if cache_source.exists():
            shutil.copytree(
                cache_source,
                self.run_dir / CONTACT_ENRICHMENT_CACHE_DIRNAME,
                dirs_exist_ok=True,
            )

    def complete(self, counts: dict[str, Any] | None = None) -> None:
        self._write_manifest("success", counts or {})
        self._mirror_run_outputs()

    def fail(self, error: BaseException, counts: dict[str, Any] | None = None) -> None:
        failure_counts = dict(counts or {})
        failure_counts["error"] = str(error)
        self._write_manifest("failed", failure_counts)
        self._mirror_run_outputs()

    def _write_manifest(self, status: str, counts: dict[str, Any]) -> None:
        completed_at = datetime.now().astimezone()
        output_files = sorted(
            path.name
            for path in self.run_dir.iterdir()
            if path.is_file() and path.name != "manifest.json"
        )
        manifest = {
            "run_id": self.run_id,
            "command": self.command,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "completed_at": completed_at.isoformat(timespec="seconds"),
            "status": status,
            "output_files": output_files,
            "counts": counts,
        }
        manifest.update(self.metadata)
        _write_json(self.run_dir / "manifest.json", manifest)

    def _mirror_run_outputs(self) -> None:
        _write_open_this_first(self.base_output_dir)
        if not self.mirror_to_latest:
            return

        self.latest_dir.mkdir(parents=True, exist_ok=True)
        for filename in OUTPUT_FILENAMES + ["manifest.json"]:
            source = self.run_dir / filename
            if not source.exists():
                continue
            target = latest_file_path(self.latest_dir, filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        cache_source = self.run_dir / CONTACT_ENRICHMENT_CACHE_DIRNAME
        if cache_source.exists():
            shutil.copytree(
                cache_source,
                self.latest_dir / "debug" / CONTACT_ENRICHMENT_CACHE_DIRNAME,
                dirs_exist_ok=True,
            )
        _write_open_this_first(self.latest_dir)
        organize_latest_outputs(self.latest_dir)


def explicit_output_dir(args: Any) -> bool:
    return bool(getattr(args, "output_dir_explicit", False))


def output_dir_for_single_stage(args: Any) -> Path:
    output_dir = Path(args.output_dir)
    if explicit_output_dir(args):
        return output_dir

    latest_dir = output_dir / "latest"
    if not latest_dir.exists():
        _seed_latest_from_flat(output_dir, latest_dir)
    return latest_dir if latest_dir.exists() else output_dir


def mirror_latest_to_flat(args: Any) -> None:
    if explicit_output_dir(args):
        return
    output_dir = Path(args.output_dir)
    latest_dir = output_dir / "latest"
    if latest_dir.exists():
        _write_open_this_first(latest_dir)
        organize_latest_outputs(latest_dir)
    _write_open_this_first(output_dir)


def copy_latest_call_log_to_run(args: Any) -> Path | None:
    if explicit_output_dir(args):
        return None

    output_dir = Path(args.output_dir)
    latest_dir = output_dir / "latest"
    manifest_path = latest_file_path(latest_dir, "manifest.json")
    call_log_csv = latest_file_path(latest_dir, "call_log.csv")
    call_log_jsonl = latest_file_path(latest_dir, "call_log.jsonl")
    if not manifest_path.exists() or not call_log_csv.exists():
        return None

    _add_manifest_files(manifest_path, ["call_log.csv", "call_log.jsonl"])

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    run_id = str(data.get("run_id", ""))
    if not run_id:
        return None

    run_dir = output_dir / "runs" / run_id
    if not run_dir.exists():
        return None

    shutil.copy2(call_log_csv, run_dir / "call_log.csv")
    if call_log_jsonl.exists():
        shutil.copy2(call_log_jsonl, run_dir / "call_log.jsonl")
    _add_manifest_files(run_dir / "manifest.json", ["call_log.csv", "call_log.jsonl"])
    organize_latest_outputs(latest_dir)
    return run_dir


def organize_latest_outputs(latest_dir: Path) -> None:
    if not latest_dir.exists():
        return

    _write_open_this_first(latest_dir)
    for source in sorted(path for path in latest_dir.iterdir() if path.is_file()):
        if source.name == "OPEN_THIS_FIRST.txt":
            continue
        target = latest_file_path(latest_dir, source.name)
        _move_without_data_loss(source, target)

    for dirname in ["open_in_sheets", "crm", "debug"]:
        (latest_dir / dirname).mkdir(parents=True, exist_ok=True)


def _move_without_data_loss(source: Path, target: Path) -> None:
    if source.resolve() == target.resolve():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if filecmp.cmp(source, target, shallow=False):
            source.unlink()
            return
        conflict_dir = source.parent / "debug" / "conflicts"
        conflict_dir.mkdir(parents=True, exist_ok=True)
        conflict_target = _unique_path(conflict_dir / target.name)
        shutil.move(str(target), str(conflict_target))
    shutil.move(str(source), str(target))


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 2
    while True:
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _add_manifest_files(path: Path, filenames: list[str]) -> None:
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return

    output_files = set(str(filename) for filename in data.get("output_files", []))
    source_base = path.parent.parent if path.parent.name == "debug" else path.parent
    for filename in filenames:
        if _existing_source(source_base, filename).exists():
            output_files.add(filename)
    data["output_files"] = sorted(output_files)
    _write_json(path, data)


def _seed_latest_from_flat(output_dir: Path, latest_dir: Path) -> None:
    existing_files = [
        _existing_source(output_dir, filename)
        for filename in OUTPUT_FILENAMES
        if _existing_source(output_dir, filename).exists()
    ]
    if not existing_files:
        return

    latest_dir.mkdir(parents=True, exist_ok=True)
    for source in existing_files:
        target = latest_file_path(latest_dir, source.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    _write_open_this_first(latest_dir)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_open_this_first(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "OPEN_THIS_FIRST.txt").write_text(OPEN_THIS_FIRST_TEXT, encoding="utf-8")


def _existing_source(base_dir: Path, filename: str) -> Path:
    organized = latest_file_path(base_dir, filename)
    if organized.exists():
        return organized
    return base_dir / filename


def _existing_cache_source(base_dir: Path) -> Path:
    organized = base_dir / "debug" / CONTACT_ENRICHMENT_CACHE_DIRNAME
    if organized.exists():
        return organized
    return base_dir / CONTACT_ENRICHMENT_CACHE_DIRNAME
