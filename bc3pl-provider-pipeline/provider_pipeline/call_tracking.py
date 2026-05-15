from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from provider_pipeline.models import CallLogRecord
from provider_pipeline.run_outputs import latest_file_path


CALL_STATUSES = {
    "not_called",
    "called_no_answer",
    "left_voicemail",
    "gatekeeper",
    "wrong_contact",
    "interested",
    "not_interested",
    "follow_up",
    "written_yes_received",
    "do_not_call",
}

CALL_LOG_FIELDS = [
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

MANUAL_FIELDS = {
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
}


def init_call_log(output_dir: Path) -> tuple[int, Path, Path]:
    call_sheet_path = _output_path(output_dir, "provider_contact_call_sheet.csv")
    if not call_sheet_path.exists():
        raise FileNotFoundError(
            f"Required input file is missing: {call_sheet_path}. Run "
            "`python -m provider_pipeline run-contact-flow` first."
        )

    run_id = _read_run_id(_output_path(output_dir, "manifest.json"))
    targets = _read_csv(call_sheet_path)
    existing_records = _read_call_log(_output_path(output_dir, "call_log.csv"))
    existing_by_key = {_merge_key(asdict(record)): record for record in existing_records}
    existing_by_id = {record.call_id: record for record in existing_records if record.call_id}
    merged: list[CallLogRecord] = []
    seen_keys: set[str] = set()
    seen_ids: set[str] = set()

    for target in targets:
        base = _record_from_target(target, run_id)
        key = _merge_key(asdict(base))
        seen_keys.add(key)
        seen_ids.add(base.call_id)
        existing = existing_by_id.get(base.call_id) or existing_by_key.get(key)
        merged.append(_merge_record(base, existing) if existing else base)

    for record in existing_records:
        key = _merge_key(asdict(record))
        if record.call_id not in seen_ids and key not in seen_keys and _should_preserve_old_record(record):
            merged.append(record)

    csv_path = _output_path(output_dir, "call_log.csv")
    jsonl_path = _output_path(output_dir, "call_log.jsonl")
    write_call_log_csv(csv_path, merged)
    write_call_log_jsonl(jsonl_path, merged)
    return len(merged), csv_path, jsonl_path


def stable_call_id(company: str, contact_name: str, phone: str, email: str) -> str:
    key = "|".join(
        [
            _normalize_key(company),
            _normalize_key(contact_name),
            _normalize_key(phone),
            _normalize_key(email),
        ]
    )
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"call_{digest}"


def write_call_log_csv(path: Path, records: list[CallLogRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CALL_LOG_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(_row(record))


def write_call_log_jsonl(path: Path, records: list[CallLogRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(_row(record), ensure_ascii=True))
            handle.write("\n")


def _record_from_target(row: dict[str, str], run_id: str) -> CallLogRecord:
    phone_called = row.get("direct_phone") or row.get("company_phone") or ""
    email = row.get("email", "")
    company = row.get("company", "")
    contact_name = row.get("contact_name", "")
    return CallLogRecord(
        call_id=stable_call_id(company, contact_name, phone_called, email),
        run_id=run_id,
        company=company,
        provider_place_id=row.get("provider_place_id", ""),
        provider_category=row.get("provider_category", ""),
        provider_priority=row.get("provider_priority", ""),
        provider_score=_int(row.get("provider_score"), default=0),
        contact_name=contact_name,
        contact_title=row.get("contact_title", ""),
        phone_called=phone_called,
        email=email,
        contact_quality=row.get("contact_quality", ""),
        source_type=row.get("source_type", ""),
        email_status=row.get("email_status", ""),
        linkedin_url=row.get("linkedin_url", ""),
        department=row.get("department", ""),
        confidence=_int(row.get("confidence"), default=0),
        call_status="not_called",
    )


def _merge_record(base: CallLogRecord, existing: CallLogRecord | None) -> CallLogRecord:
    if existing is None:
        return base

    data = asdict(base)
    existing_data = asdict(existing)
    data["call_id"] = existing.call_id or base.call_id
    for field in MANUAL_FIELDS:
        data[field] = existing_data.get(field, "")
    if data["call_status"] not in CALL_STATUSES:
        data["call_status"] = "not_called"
    return CallLogRecord(**data)


def _read_call_log(path: Path) -> list[CallLogRecord]:
    if not path.exists():
        return []
    records = []
    for row in _read_csv(path):
        status = row.get("call_status") or "not_called"
        if status not in CALL_STATUSES:
            status = "not_called"
        records.append(
            CallLogRecord(
                call_id=row.get("call_id") or stable_call_id(
                    row.get("company", ""),
                    row.get("contact_name", ""),
                    row.get("phone_called", ""),
                    row.get("email", ""),
                ),
                run_id=row.get("run_id", ""),
                company=row.get("company", ""),
                provider_place_id=row.get("provider_place_id", ""),
                provider_category=row.get("provider_category", ""),
                provider_priority=row.get("provider_priority", ""),
                provider_score=_int(row.get("provider_score"), default=0),
                contact_name=row.get("contact_name", ""),
                contact_title=row.get("contact_title", ""),
                phone_called=row.get("phone_called", ""),
                email=row.get("email", ""),
                contact_quality=row.get("contact_quality", ""),
                source_type=row.get("source_type", ""),
                email_status=row.get("email_status", ""),
                linkedin_url=row.get("linkedin_url", ""),
                department=row.get("department", ""),
                confidence=_int(row.get("confidence"), default=0),
                call_datetime=row.get("call_datetime", ""),
                call_status=status,
                outcome=row.get("outcome", ""),
                accepted_standard_price=row.get("accepted_standard_price", ""),
                accepted_exclusive_price=row.get("accepted_exclusive_price", ""),
                follow_up_date=row.get("follow_up_date", ""),
                objections=row.get("objections", ""),
                notes=row.get("notes", ""),
                recording_url=row.get("recording_url", ""),
                transcript_path=row.get("transcript_path", ""),
                summary=row.get("summary", ""),
                next_action=row.get("next_action", ""),
            )
        )
    return records


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [
            {key: "" if value is None else value for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def _read_run_id(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    return str(data.get("run_id", ""))


def _row(record: CallLogRecord) -> dict[str, str]:
    data = asdict(record)
    return {field: "" if data.get(field) is None else str(data.get(field, "")) for field in CALL_LOG_FIELDS}


def _merge_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            _normalize_key(row.get("company", "")),
            _normalize_key(row.get("contact_name", "")),
            _normalize_key(row.get("phone_called", "")),
            _normalize_key(row.get("email", "")),
        ]
    )


def _should_preserve_old_record(record: CallLogRecord) -> bool:
    if record.contact_quality == "bad_extraction" and record.call_status == "not_called" and not _has_manual_data(record):
        return False
    if record.call_status != "not_called":
        return True
    return _has_manual_data(record)


def _has_manual_data(record: CallLogRecord) -> bool:
    return any(
        getattr(record, field)
        for field in [
            "call_datetime",
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
    )


def _normalize_key(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _output_path(output_dir: Path, filename: str) -> Path:
    if output_dir.name == "latest":
        return latest_file_path(output_dir, filename)
    return output_dir / filename
