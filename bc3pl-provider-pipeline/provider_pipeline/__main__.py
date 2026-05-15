from __future__ import annotations

import argparse
import sys
from pathlib import Path

from provider_pipeline.agents import (
    CallSheetAgent,
    ContactFinderAgent,
    ContactRoleScoringAgent,
    ProviderAuditAgent,
    ProviderContactCallSheetAgent,
    ProviderEnrichmentAgent,
    ProviderFinderAgent,
    ProviderScoringAgent,
    ProviderVerificationAgent,
)
from provider_pipeline.env import load_env_file
from provider_pipeline.pipeline import ProviderPipeline
from provider_pipeline.call_tracking import init_call_log
from provider_pipeline.run_outputs import (
    RunOutputSession,
    copy_latest_call_log_to_run,
    explicit_output_dir,
    mirror_latest_to_flat,
    output_dir_for_single_stage,
)


COMMANDS = {
    "run-all",
    "find-providers",
    "enrich-providers",
    "verify-providers",
    "audit-providers",
    "score-providers",
    "build-call-sheet",
    "find-provider-contacts",
    "score-provider-contacts",
    "build-provider-contact-call-sheet",
    "run-contact-flow",
    "init-call-log",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    output_dir_explicit = any(
        token == "--output-dir" or token.startswith("--output-dir=")
        for token in argv
    )
    if argv and argv[0] in COMMANDS:
        args = _build_subcommand_parser().parse_args(argv)
        args.output_dir_explicit = output_dir_explicit
        return args

    args = _build_legacy_parser().parse_args(argv)
    args.command = "run-all"
    args.output_dir_explicit = output_dir_explicit
    return args


def _build_legacy_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find, enrich, score, and export BC logistics provider targets."
    )
    _add_full_pipeline_args(parser)
    return parser


def _build_subcommand_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run BC3PL provider pipeline agents independently or end to end."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_all = subparsers.add_parser(
        "run-all",
        help="Run the full provider pipeline end to end.",
    )
    _add_full_pipeline_args(run_all)

    find_providers = subparsers.add_parser(
        "find-providers",
        help="Search for provider candidates and write raw provider outputs.",
    )
    _add_common_path_args(find_providers)
    find_providers.add_argument(
        "--max-results-per-query",
        type=int,
        default=10,
        help="Maximum Google Places results to keep per query.",
    )
    find_providers.add_argument(
        "--sample",
        action="store_true",
        help="Run against bundled sample provider data without API keys.",
    )

    enrich_providers = subparsers.add_parser(
        "enrich-providers",
        help="Read raw providers and write enriched provider outputs.",
    )
    _add_output_dir_arg(enrich_providers)
    enrich_providers.add_argument(
        "--no-openai",
        action="store_true",
        help="Skip OpenAI web-backed enrichment and use keyword enrichment only.",
    )

    verify_providers = subparsers.add_parser(
        "verify-providers",
        help="Read enriched providers and write verified provider outputs.",
    )
    _add_output_dir_arg(verify_providers)

    audit_providers = subparsers.add_parser(
        "audit-providers",
        help="Use the review model to audit high-value verified providers.",
    )
    _add_output_dir_arg(audit_providers)
    audit_providers.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum approved/review providers to audit with the review model.",
    )

    score_providers = subparsers.add_parser(
        "score-providers",
        help="Read verified providers when available and write scored provider outputs.",
    )
    _add_output_dir_arg(score_providers)

    build_call_sheet = subparsers.add_parser(
        "build-call-sheet",
        help="Read scored providers and build the call sheet CSV.",
    )
    _add_output_dir_arg(build_call_sheet)

    find_provider_contacts = subparsers.add_parser(
        "find-provider-contacts",
        help="Read scored providers and find website contacts.",
    )
    _add_output_dir_arg(find_provider_contacts)
    find_provider_contacts.add_argument(
        "--hunter",
        action="store_true",
        help="Opt in to Hunter domain-search enrichment when HUNTER_API_KEY is set.",
    )
    _add_paid_enrichment_args(find_provider_contacts)

    score_provider_contacts = subparsers.add_parser(
        "score-provider-contacts",
        help="Read raw provider contacts and score best outreach routes.",
    )
    _add_output_dir_arg(score_provider_contacts)

    build_provider_contact_call_sheet = subparsers.add_parser(
        "build-provider-contact-call-sheet",
        help="Read scored provider contacts and build the provider contact call sheet CSV.",
    )
    _add_output_dir_arg(build_provider_contact_call_sheet)

    run_contact_flow = subparsers.add_parser(
        "run-contact-flow",
        help="Run contact finding, contact scoring, and contact call sheet export.",
    )
    _add_output_dir_arg(run_contact_flow)
    run_contact_flow.add_argument(
        "--hunter",
        action="store_true",
        help="Opt in to Hunter domain-search enrichment when HUNTER_API_KEY is set.",
    )
    _add_paid_enrichment_args(run_contact_flow)

    init_call_log_parser = subparsers.add_parser(
        "init-call-log",
        help="Create or merge a manual provider call tracking log.",
    )
    _add_output_dir_arg(init_call_log_parser)

    return parser


def _add_full_pipeline_args(parser: argparse.ArgumentParser) -> None:
    _add_common_path_args(parser)
    parser.add_argument(
        "--max-results-per-query",
        type=int,
        default=10,
        help="Maximum Google Places results to keep per query.",
    )
    parser.add_argument(
        "--no-openai",
        action="store_true",
        help="Skip OpenAI web-backed enrichment and use keyword scoring only.",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Run against bundled sample provider data without API keys.",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Audit high-value verified providers with the review model before scoring.",
    )
    parser.add_argument(
        "--audit-limit",
        type=int,
        default=25,
        help="Maximum approved/review providers to audit when --audit is enabled.",
    )


def _add_common_path_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default="config/provider_queries.json",
        help="Path to provider query config JSON.",
    )
    _add_output_dir_arg(parser)


def _add_output_dir_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory where CSV and JSONL outputs are written.",
    )


def _add_paid_enrichment_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--apollo",
        action="store_true",
        help="Opt in to legacy Apollo People Search. Prefer --apollo-enrich for enrichment-only.",
    )
    parser.add_argument(
        "--apollo-enrich",
        action="store_true",
        help="Opt in to Apollo People Enrichment using existing website/Hunter candidate contacts.",
    )
    parser.add_argument(
        "--enrichment-limit",
        type=int,
        default=0,
        help="Maximum paid domains for Hunter/search, or candidate contacts for --apollo-enrich. 0 means no cap.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report paid enrichment calls that would be made without calling paid APIs.",
    )


def main() -> None:
    load_env_file(Path(".env"))
    args = parse_args()

    if args.command == "find-providers":
        result = ProviderFinderAgent(
            config_path=Path(args.config),
            output_dir=output_dir_for_single_stage(args),
            max_results_per_query=args.max_results_per_query,
            use_sample=args.sample,
        ).run()
        print("")
        print("Done.")
        print(f"Raw providers found: {result.raw_count}")
        print(f"Raw providers written: {result.deduped_count}")
        print(f"CSV output: {result.csv_path}")
        print(f"JSONL output: {result.jsonl_path}")
        mirror_latest_to_flat(args)
        return

    if args.command == "enrich-providers":
        result = ProviderEnrichmentAgent(
            output_dir=output_dir_for_single_stage(args),
            use_openai=not args.no_openai,
        ).run()
        print("")
        print("Done.")
        print(f"Enriched providers: {result.record_count}")
        print(f"CSV output: {result.csv_path}")
        print(f"JSONL output: {result.jsonl_path}")
        mirror_latest_to_flat(args)
        return

    if args.command == "score-providers":
        result = ProviderScoringAgent(output_dir=output_dir_for_single_stage(args)).run()
        print("")
        print("Done.")
        print(f"Scored providers: {result.record_count}")
        print(f"CSV output: {result.csv_path}")
        print(f"JSONL output: {result.jsonl_path}")
        mirror_latest_to_flat(args)
        return

    if args.command == "verify-providers":
        result = ProviderVerificationAgent(output_dir=output_dir_for_single_stage(args)).run()
        print("")
        print("Done.")
        print(f"Verified providers: {result.record_count}")
        print(f"CSV output: {result.csv_path}")
        print(f"JSONL output: {result.jsonl_path}")
        mirror_latest_to_flat(args)
        return

    if args.command == "audit-providers":
        result = ProviderAuditAgent(
            output_dir=output_dir_for_single_stage(args),
            limit=args.limit,
        ).run()
        print("")
        print("Done.")
        print(f"Audited provider records written: {result.record_count}")
        print(f"CSV output: {result.csv_path}")
        print(f"JSONL output: {result.jsonl_path}")
        mirror_latest_to_flat(args)
        return

    if args.command == "build-call-sheet":
        result = CallSheetAgent(output_dir=output_dir_for_single_stage(args)).run()
        print("")
        print("Done.")
        print(f"Call sheet rows: {result.call_sheet_count}")
        print(f"CSV output: {result.csv_path}")
        mirror_latest_to_flat(args)
        return

    if args.command == "find-provider-contacts":
        if args.dry_run:
            ContactFinderAgent(
                output_dir=output_dir_for_single_stage(args),
                use_hunter=args.hunter,
                use_apollo=args.apollo,
                use_apollo_enrich=args.apollo_enrich,
                enrichment_limit=args.enrichment_limit,
                dry_run=True,
            ).plan_paid_enrichment()
            return
        result = ContactFinderAgent(
            output_dir=output_dir_for_single_stage(args),
            use_hunter=args.hunter,
            use_apollo=args.apollo,
            use_apollo_enrich=args.apollo_enrich,
            enrichment_limit=args.enrichment_limit,
            dry_run=args.dry_run,
        ).run()
        print("")
        print("Done.")
        print(f"Raw contacts: {result.contact_count}")
        print(f"CSV output: {result.csv_path}")
        print(f"JSONL output: {result.jsonl_path}")
        mirror_latest_to_flat(args)
        return

    if args.command == "score-provider-contacts":
        result = ContactRoleScoringAgent(output_dir=output_dir_for_single_stage(args)).run()
        print("")
        print("Done.")
        print(f"Scored contacts: {result.contact_count}")
        print(f"CSV output: {result.csv_path}")
        print(f"JSONL output: {result.jsonl_path}")
        mirror_latest_to_flat(args)
        return

    if args.command == "build-provider-contact-call-sheet":
        result = ProviderContactCallSheetAgent(output_dir=output_dir_for_single_stage(args)).run()
        print("")
        print("Done.")
        print(f"Provider contact call sheet rows: {result.call_sheet_count}")
        print(f"CSV output: {result.csv_path}")
        mirror_latest_to_flat(args)
        return

    if args.command == "run-contact-flow":
        if args.dry_run:
            output_dir = Path(args.output_dir) if explicit_output_dir(args) else output_dir_for_single_stage(args)
            ContactFinderAgent(
                output_dir=output_dir,
                use_hunter=args.hunter,
                use_apollo=args.apollo,
                use_apollo_enrich=args.apollo_enrich,
                enrichment_limit=args.enrichment_limit,
                dry_run=True,
            ).plan_paid_enrichment()
            return

        if explicit_output_dir(args):
            output_dir = Path(args.output_dir)
            raw_result = ContactFinderAgent(
                output_dir=output_dir,
                use_hunter=args.hunter,
                use_apollo=args.apollo,
                use_apollo_enrich=args.apollo_enrich,
                enrichment_limit=args.enrichment_limit,
                dry_run=args.dry_run,
            ).run()
            scored_result = ContactRoleScoringAgent(output_dir=output_dir).run()
            call_sheet_result = ProviderContactCallSheetAgent(output_dir=output_dir).run()
        else:
            session = RunOutputSession(
                base_output_dir=Path(args.output_dir),
                command=args.command,
            )
            session.seed_from_latest_or_flat()
            try:
                raw_result = ContactFinderAgent(
                    output_dir=session.run_dir,
                    use_hunter=args.hunter,
                    use_apollo=args.apollo,
                    use_apollo_enrich=args.apollo_enrich,
                    enrichment_limit=args.enrichment_limit,
                    dry_run=args.dry_run,
                    run_id=session.run_id,
                ).run()
                scored_result = ContactRoleScoringAgent(output_dir=session.run_dir).run()
                call_sheet_result = ProviderContactCallSheetAgent(output_dir=session.run_dir).run()
                counts = {
                    "raw_contacts": raw_result.contact_count,
                    "scored_contacts": scored_result.contact_count,
                    "provider_contact_call_sheet_rows": call_sheet_result.call_sheet_count,
                    "hunter_enabled": args.hunter,
                    "apollo_enabled": args.apollo,
                    "apollo_enrich_enabled": args.apollo_enrich,
                    "dry_run": args.dry_run,
                    "enrichment_limit": args.enrichment_limit,
                }
                counts.update(raw_result.enrichment_summary or {})
                session.complete(counts)
            except Exception as error:
                session.fail(error)
                raise
        print("")
        print("Done.")
        print(f"Raw contacts: {raw_result.contact_count}")
        print(f"Scored contacts: {scored_result.contact_count}")
        print(f"Provider contact call sheet rows: {call_sheet_result.call_sheet_count}")
        print(f"CSV output: {call_sheet_result.csv_path}")
        if raw_result.report_csv_path:
            print(f"Enrichment report CSV: {raw_result.report_csv_path}")
        if raw_result.report_jsonl_path:
            print(f"Enrichment report JSONL: {raw_result.report_jsonl_path}")
        return

    if args.command == "init-call-log":
        output_dir = output_dir_for_single_stage(args)
        row_count, csv_path, jsonl_path = init_call_log(output_dir)
        mirror_latest_to_flat(args)
        run_dir = copy_latest_call_log_to_run(args)
        print("")
        print("Done.")
        print(f"Call log rows: {row_count}")
        print(f"CSV output: {csv_path}")
        print(f"JSONL output: {jsonl_path}")
        if run_dir is not None:
            print(f"Run folder copy: {run_dir}")
        return

    if explicit_output_dir(args):
        output_dir = Path(args.output_dir)
        pipeline = ProviderPipeline(
            config_path=Path(args.config),
            output_dir=output_dir,
            max_results_per_query=args.max_results_per_query,
            use_openai=not args.no_openai,
            use_sample=args.sample,
            use_audit=args.audit,
            audit_limit=args.audit_limit,
        )
        result = pipeline.run()
    else:
        session = RunOutputSession(
            base_output_dir=Path(args.output_dir),
            command=args.command,
            mirror_to_latest=not args.sample,
            metadata={
                "config_path": args.config,
                "max_results_per_query": args.max_results_per_query,
                "audit_enabled": args.audit,
                "audit_limit": args.audit_limit,
                "no_openai": args.no_openai,
                "sample": args.sample,
            },
        )
        pipeline = ProviderPipeline(
            config_path=Path(args.config),
            output_dir=session.run_dir,
            max_results_per_query=args.max_results_per_query,
            use_openai=not args.no_openai,
            use_sample=args.sample,
            use_audit=args.audit,
            audit_limit=args.audit_limit,
        )
        try:
            result = pipeline.run()
            session.complete(
                {
                    "raw_providers": result.raw_count,
                    "deduped_providers": result.deduped_count,
                    "call_sheet_rows": result.call_sheet_count,
                }
            )
        except Exception as error:
            session.fail(error)
            raise
    print("")
    print("Done.")
    print(f"Raw providers: {result.raw_count}")
    print(f"Deduped providers: {result.deduped_count}")
    print(f"Call sheet rows: {result.call_sheet_count}")
    print(f"Output directory: {result.output_dir}")


if __name__ == "__main__":
    main()
