from __future__ import annotations

from pathlib import Path

from provider_pipeline.agents import (
    CallSheetAgent,
    ProviderAuditAgent,
    ProviderEnrichmentAgent,
    ProviderFinderAgent,
    ProviderScoringAgent,
    ProviderVerificationAgent,
)
from provider_pipeline.models import PipelineResult


class ProviderPipeline:
    def __init__(
        self,
        config_path: Path,
        output_dir: Path,
        max_results_per_query: int,
        use_openai: bool,
        use_sample: bool,
        use_audit: bool = False,
        audit_limit: int = 25,
    ) -> None:
        self.config_path = config_path
        self.output_dir = output_dir
        self.max_results_per_query = max_results_per_query
        self.use_openai = use_openai
        self.use_sample = use_sample
        self.use_audit = use_audit
        self.audit_limit = audit_limit

    def run(self) -> PipelineResult:
        finder_result = ProviderFinderAgent(
            config_path=self.config_path,
            output_dir=self.output_dir,
            max_results_per_query=self.max_results_per_query,
            use_sample=self.use_sample,
        ).run()
        ProviderEnrichmentAgent(
            output_dir=self.output_dir,
            use_openai=self.use_openai,
        ).run()
        ProviderVerificationAgent(output_dir=self.output_dir).run()
        if self.use_audit:
            ProviderAuditAgent(output_dir=self.output_dir, limit=self.audit_limit).run()
        ProviderScoringAgent(output_dir=self.output_dir).run()
        call_sheet_result = CallSheetAgent(output_dir=self.output_dir).run()

        return PipelineResult(
            raw_count=finder_result.raw_count,
            deduped_count=finder_result.deduped_count,
            call_sheet_count=call_sheet_result.call_sheet_count,
            output_dir=str(self.output_dir.resolve()),
        )
