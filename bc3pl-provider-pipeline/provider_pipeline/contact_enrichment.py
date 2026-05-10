from __future__ import annotations

import os

from provider_pipeline.models import ContactRecord, ProviderRecord


def enrich_contacts_if_enabled(
    provider: ProviderRecord,
    contacts: list[ContactRecord],
    *,
    enabled: bool = False,
) -> list[ContactRecord]:
    """Placeholder for paid enrichment APIs.

    This is intentionally disabled by default. Future CLI flags can opt in to
    Hunter or Apollo calls without changing the website-only contact flow.
    """
    if not enabled:
        return contacts

    if not (os.getenv("HUNTER_API_KEY") or os.getenv("APOLLO_API_KEY")):
        print(f"Paid contact enrichment skipped for {provider.company}: no API key.")
        return contacts

    print(f"Paid contact enrichment is not implemented; using website contacts for {provider.company}.")
    return contacts
