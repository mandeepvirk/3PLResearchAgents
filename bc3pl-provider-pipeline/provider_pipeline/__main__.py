from __future__ import annotations

import argparse
import sys
from pathlib import Path

from provider_pipeline.agents import (
    CallSheetAgent,
    ProviderAuditAgent,
    ProviderEnrichmentAgent,
    ProviderFinderAgent,
    ProviderScoringAgent,
    ProviderVerificationAgent,
)
from provider_pipeline.env import load_env_file
from provider_pipeline.pipeline import ProviderPipeline


COMMANDS = {
    "run-all",
    "find-providers",
    "enrich-providers",
    "verify-providers",
    "audit-providers",
    "score-providers",
    "build-call-sheet",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in COMMANDS:
        return _build_subcommand_parser().parse_args(argv)

    args = _build_legacy_parser().parse_args(argv)
    args.command = "run-all"
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


def main() -> None:
    load_env_file(Path(".env"))
    args = parse_args()

    if args.command == "find-providers":
        result = ProviderFinderAgent(
            config_path=Path(args.config),
            output_dir=Path(args.output_dir),
            max_results_per_query=args.max_results_per_query,
            use_sample=args.sample,
        ).run()
        print("")
        print("Done.")
        print(f"Raw providers found: {result.raw_count}")
        print(f"Raw providers written: {result.deduped_count}")
        print(f"CSV output: {result.csv_path}")
        print(f"JSONL output: {result.jsonl_path}")
        return

    if args.command == "enrich-providers":
        result = ProviderEnrichmentAgent(
            output_dir=Path(args.output_dir),
            use_openai=not args.no_openai,
        ).run()
        print("")
        print("Done.")
        print(f"Enriched providers: {result.record_count}")
        print(f"CSV output: {result.csv_path}")
        print(f"JSONL output: {result.jsonl_path}")
        return

    if args.command == "score-providers":
        result = ProviderScoringAgent(output_dir=Path(args.output_dir)).run()
        print("")
        print("Done.")
        print(f"Scored providers: {result.record_count}")
        print(f"CSV output: {result.csv_path}")
        print(f"JSONL output: {result.jsonl_path}")
        return

    if args.command == "verify-providers":
        result = ProviderVerificationAgent(output_dir=Path(args.output_dir)).run()
        print("")
        print("Done.")
        print(f"Verified providers: {result.record_count}")
        print(f"CSV output: {result.csv_path}")
        print(f"JSONL output: {result.jsonl_path}")
        return

    if args.command == "audit-providers":
        result = ProviderAuditAgent(
            output_dir=Path(args.output_dir),
            limit=args.limit,
        ).run()
        print("")
        print("Done.")
        print(f"Audited provider records written: {result.record_count}")
        print(f"CSV output: {result.csv_path}")
        print(f"JSONL output: {result.jsonl_path}")
        return

    if args.command == "build-call-sheet":
        result = CallSheetAgent(output_dir=Path(args.output_dir)).run()
        print("")
        print("Done.")
        print(f"Call sheet rows: {result.call_sheet_count}")
        print(f"CSV output: {result.csv_path}")
        return

    pipeline = ProviderPipeline(
        config_path=Path(args.config),
        output_dir=Path(args.output_dir),
        max_results_per_query=args.max_results_per_query,
        use_openai=not args.no_openai,
        use_sample=args.sample,
        use_audit=args.audit,
        audit_limit=args.audit_limit,
    )
    result = pipeline.run()
    print("")
    print("Done.")
    print(f"Raw providers: {result.raw_count}")
    print(f"Deduped providers: {result.deduped_count}")
    print(f"Call sheet rows: {result.call_sheet_count}")
    print(f"Output directory: {result.output_dir}")


if __name__ == "__main__":
    main()
