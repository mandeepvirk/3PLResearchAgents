from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QuerySpec:
    target_category: str
    query: str


@dataclass(frozen=True)
class PipelineConfig:
    region: str
    queries: list[QuerySpec]


def load_config(path: Path) -> PipelineConfig:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    queries = [
        QuerySpec(
            target_category=item["target_category"].strip(),
            query=item["query"].strip(),
        )
        for item in payload.get("queries", [])
        if item.get("target_category") and item.get("query")
    ]
    if not queries:
        raise ValueError(f"No queries found in {path}")

    return PipelineConfig(
        region=payload.get("region", "BC Lower Mainland"),
        queries=queries,
    )
