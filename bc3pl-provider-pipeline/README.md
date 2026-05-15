# BC3PL Provider Research Pipeline

This is the first automation for the BC logistics RFQ business.

It finds provider candidates, enriches them, scores fit for the buyer panel, dedupes records, and exports call-ready CSVs.

## What It Builds

Every default full run writes to a timestamped run folder and mirrors the newest outputs for easy access:

- `output/runs/YYYY-MM-DD_HH-MM-SS_run-all/`: historical outputs for one full provider run.
- `output/runs/YYYY-MM-DD_HH-MM-SS_run-contact-flow/`: historical outputs for one contact-refresh run.
- `output/latest/`: newest organized outputs from the last managed run.
- `output/OPEN_THIS_FIRST.txt`: short guide for which files to open.
- `manifest.json`: run metadata with command, timestamps, status, output files, parameters, and counts.

The files to open are:

- `output/latest/open_in_sheets/call_log.csv`: working call tracker. Import this into Google Sheets and update statuses here.
- `output/latest/open_in_sheets/provider_contact_call_sheet.csv`: read-only suggested people/contact routes to call.
- `output/latest/open_in_sheets/call_sheet.csv`: read-only company-level call sheet.

Single-stage commands without an explicit `--output-dir` read from and write to the organized `output/latest` subfolders, falling back to existing flat `output/` files if no `latest` folder exists yet. If you pass `--output-dir`, the command respects that directory directly.

Generated research files are organized into:

- `output/latest/crm/`: scored provider/contact CSVs for CRM imports.
- `output/latest/debug/`: JSONL files, manifest, paid enrichment diagnostics, and raw/enriched/verified/audited intermediate CSVs.

The `output/latest` root intentionally contains only `OPEN_THIS_FIRST.txt`, `open_in_sheets/`, `crm/`, and `debug/`. Do not edit files in `crm/` or `debug/`.

Lead products covered:

- Bonded / sufferance / cross-border warehousing RFQs.
- Cold storage / food-grade warehousing RFQs.
- General 3PL warehouses only as backup providers.

## Quick Start

```bash
cd /Users/mandeepsingh/Documents/New\ project/bc3pl-provider-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add keys to `.env`:

- `GOOGLE_MAPS_API_KEY`: required for live provider discovery.
- `OPENAI_API_KEY`: optional but recommended for web-backed enrichment.
- `OPENAI_MODEL_FAST`: defaults to `gpt-5-mini` for normal enrichment.
- `OPENAI_MODEL_REVIEW`: defaults to `gpt-5.2` for high-value audit and verification review.

The scraper and evidence extraction stay deterministic. The fast model is used only to improve notes, service labels, and call angles during bulk enrichment. The review model is used only when you enable audit for the subset of approved/review providers that are most worth calling.

Run a no-key test first:

```bash
python -m provider_pipeline run-all --sample --no-openai
```

Sample runs write only to their timestamped folder in `output/runs/`; they do not replace `output/latest/`.

Run live discovery:

```bash
python -m provider_pipeline run-all --max-results-per-query 10
```

Run live discovery with high-value provider audit:

```bash
python -m provider_pipeline run-all --max-results-per-query 10 --audit --audit-limit 25
```

Run live discovery without OpenAI enrichment:

```bash
python -m provider_pipeline run-all --max-results-per-query 10 --no-openai
```

Legacy full-pipeline invocation still works:

```bash
python -m provider_pipeline --sample --no-openai
```

## Agent Commands

Each stage now runs as an independently runnable agent that reads and writes files in `output/`.

`ProviderFinderAgent`
- Reads `config/provider_queries.json`
- Searches Google Places or bundled sample data
- Writes `output/providers_raw.csv` and `output/providers_raw.jsonl`

```bash
python -m provider_pipeline find-providers --sample
python -m provider_pipeline find-providers --max-results-per-query 10
```

`ProviderEnrichmentAgent`
- Reads `output/providers_raw.jsonl`
- Uses OpenAI web-backed enrichment when enabled
- Falls back to keyword enrichment when `--no-openai` is used or OpenAI is unavailable
- Writes `output/providers_enriched.csv` and `output/providers_enriched.jsonl`

```bash
python -m provider_pipeline enrich-providers --no-openai
python -m provider_pipeline enrich-providers
```

`ProviderScoringAgent`
- Reads `output/providers_audited.jsonl` when present, then `output/providers_verified.jsonl`, otherwise falls back to `output/providers_enriched.jsonl`
- Applies fit scoring and priority rules
- Writes `output/providers_scored.csv` and `output/providers_scored.jsonl`

```bash
python -m provider_pipeline score-providers
```

`ProviderVerificationAgent`
- Reads `output/providers_enriched.jsonl`
- Marks each provider as `approved`, `review`, or `rejected`
- Writes `output/providers_verified.csv` and `output/providers_verified.jsonl`

```bash
python -m provider_pipeline verify-providers
```

`ProviderAuditAgent`
- Reads `output/providers_verified.jsonl`
- Uses `OPENAI_MODEL_REVIEW` to audit only approved/review providers up to `--limit`
- Keeps deterministic evidence flags as authoritative and focuses on verification notes, call angle, confidence, and rejection/review decisions
- Writes `output/providers_audited.csv` and `output/providers_audited.jsonl`

```bash
python -m provider_pipeline audit-providers --limit 25
```

`CallSheetAgent`
- Reads `output/providers_scored.jsonl`
- Writes `output/call_sheet.csv` with call opener text
- Excludes providers with `verification_status=rejected`

```bash
python -m provider_pipeline build-call-sheet
```

`ContactFinderAgent`
- Reads `output/providers_scored.jsonl`
- Skips providers with `verification_status=rejected`
- Checks public company website pages first: home, contact, about, team, leadership, management, locations, sales, and services pages
- Extracts visible names, titles, emails, phones, mailto/tel links, and LinkedIn URLs that are already published on the company site
- Rejects navigation labels, CTA text, page headings, addresses, postal codes, and long snippets as fake people/titles
- Writes `output/provider_contacts_raw.csv` and `output/provider_contacts_raw.jsonl`

```bash
python -m provider_pipeline find-provider-contacts
python -m provider_pipeline find-provider-contacts --hunter
```

`ContactRoleScoringAgent`
- Reads `output/provider_contacts_raw.jsonl`
- Scores contacts for selling accepted RFQs to provider companies
- Prioritizes owners, founders, presidents, CEOs, general managers, branch managers, managing directors, sales/business development leaders, and relevant operations managers
- Penalizes unrelated HR, accounting, finance, driver, mechanic, and IT roles
- Dedupes contacts and keeps the best 1-3 contacts per company
- Writes `output/provider_contacts_scored.csv` and `output/provider_contacts_scored.jsonl`

```bash
python -m provider_pipeline score-provider-contacts
```

`ProviderContactCallSheetAgent`
- Reads `output/provider_contacts_scored.jsonl`
- Writes `output/provider_contact_call_sheet.csv`
- Sorts by provider priority, provider score, contact call priority, and role fit score

```bash
python -m provider_pipeline build-provider-contact-call-sheet
```

Run all three contact stages after provider scoring:

```bash
python -m provider_pipeline run-contact-flow
python -m provider_pipeline run-contact-flow --hunter
python -m provider_pipeline run-contact-flow --hunter --apollo --enrichment-limit 3
```

`--enrichment-limit` caps paid contact enrichment to the first N eligible provider domains. Use it for smoke tests before spending calls across the full provider list.

Initialize or refresh the manual call log:

```bash
python -m provider_pipeline init-call-log
```

`init-call-log` reads `output/latest/open_in_sheets/provider_contact_call_sheet.csv` by default, or reads from the directory passed with `--output-dir`. It writes:

- `output/latest/open_in_sheets/call_log.csv`
- `output/latest/debug/call_log.jsonl`
- copies inside the latest run folder when `output/latest/debug/manifest.json` points to one

Rerunning the command is safe. It merges by company, contact name, phone, and email, adds new call targets, and preserves manually entered call status, notes, objections, follow-up dates, recordings, transcript links, summaries, and next actions.

## How The Pipeline Works

1. `ProviderFinderAgent` reads search terms from `config/provider_queries.json`.
2. It calls Google Places Text Search for each query or uses bundled sample data.
3. It dedupes by Google place ID or normalized company/city and writes raw outputs.
4. `ProviderEnrichmentAgent` reads the raw JSONL and enriches each provider.
5. `ProviderVerificationAgent` reads the enriched JSONL and approves, reviews, or rejects each provider.
6. Optional `ProviderAuditAgent` uses the review model to audit high-value approved/review providers.
7. `ProviderScoringAgent` reads audited records when present, otherwise verified records, and applies the scoring model.
8. `CallSheetAgent` reads the scored JSONL and writes the final call sheet CSV.
9. Optional contact flow reads the scored provider JSONL and creates call-ready contacts for provider buyer-panel outreach.

## Contact Flow Notes

The contact flow is website-first and produces new outputs instead of changing the existing provider outputs. It is designed to find the best person or route at each provider company so you can call or email about paying for accepted RFQs.

LinkedIn scraping is intentionally not supported. The pipeline may store LinkedIn URLs that appear on a public company website, but it does not automate LinkedIn browsing, login, or scraping.

Website-only contact flow is the default. It uses public company sites and classifies contacts with `contact_quality`:

- `named_person`: plausible human name with useful role/title evidence.
- `personal_email_unknown_name`: personal-looking work email without a confirmed website name/title.
- `department_email`: generic usable inbox such as sales, info, warehouse, logistics, dispatch, or operations.
- `company_phone_fallback`: company phone route used only when no better contact route exists.
- `bad_extraction`: rejected extraction; excluded from the provider contact call sheet and new call log targets.

Hunter enrichment is optional and off by default. Add this to `.env` to enable it:

```bash
HUNTER_API_KEY=...
```

Then run:

```bash
python -m provider_pipeline run-contact-flow --hunter
```

Hunter and Apollo diagnostics are written to `output/latest/debug/contact_enrichment_report.csv` and `output/latest/debug/contact_enrichment_report.jsonl`. The report shows which domains were attempted, cache hits, HTTP statuses, short error messages, and contacts added. Hunter results are capped at 10 per domain by default and cached under the run output cache folder to avoid repeat API lookups. Missing paid API keys do not stop normal website-only runs. Hunter or Apollo may return LinkedIn URLs, which the pipeline stores as data, but it still does not browse or scrape LinkedIn.

## Manual Call Tracking

Call tracking is local CSV/JSONL only for now. There is no OpenPhone, CallRail, Twilio, or paid phone API integration.

Use `output/latest/open_in_sheets/call_log.csv` while calling providers. The main status values are:

- `not_called`: target is ready but no call has happened.
- `called_no_answer`: call attempt made, no answer.
- `left_voicemail`: voicemail left.
- `gatekeeper`: reached reception, dispatch, or another blocker.
- `wrong_contact`: contact is not the right person or route.
- `interested`: verbal interest.
- `not_interested`: verbal no.
- `follow_up`: follow-up required.
- `written_yes_received`: written acceptance received.
- `do_not_call`: suppress future calls.

Recordings and transcripts are manually linked with `recording_url` and `transcript_path`. Use `summary`, `objections`, and `next_action` to keep the next call clear.

## Provider CRM Columns

The scored CSV includes:

- company
- category
- city
- phone
- website
- services
- bonded / sufferance / cross-border / cold storage / food-grade flags
- score
- priority
- evidence URLs
- recommended call angle

## Suggested VSCode Workflow

Use Codex for implementation changes and test runs.

Use Claude Code for independent review prompts like:

```text
Review the provider enrichment prompt and scoring logic for false positives.
The goal is to find BC bonded/sufferance/cold-storage providers likely to pay for accepted RFQs.
Suggest concrete changes only.
```

Then bring the suggestions back into Codex for edits.

## Next Automation After This

Once the provider panel workflow is producing a clean list, add:

- Google Sheets sync.
- OpenPhone/CallRail call logging.
- Provider status tracking.
- A daily report of 20 calls to make.

## Dashboard

A Streamlit dashboard is available to view pipeline outputs and run commands.

```bash
pip install -r requirements.txt
streamlit run provider_pipeline_dashboard.py
```

Open the URL printed in the terminal to view provider data, filter by status/category/city, and trigger pipeline runs from the browser.

## Compliance Notes

This tool prepares research and call sheets. Do not use it to automatically blast cold emails or texts. Keep manual review before outreach, maintain an internal do-not-call list, and follow Canadian CASL/CRTC rules.
