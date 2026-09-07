"""Validate model analysis without exposing model output in progress errors."""

import json

from pydantic import ValidationError

from modules.plugin_ai import RepositoryAnalysis


class AnalysisFormatError(ValueError):
    """Safe, actionable structured-output diagnostics for a single repository."""


def parse_analysis(content: str) -> RepositoryAnalysis:
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(content)
    except ValueError as exc:
        raise AnalysisFormatError(
            "AI analysis is not valid JSON; structured output is required"
        ) from exc
    # A negative classification needs no installation metadata and cannot mutate the catalog.
    if isinstance(data, dict) and data.get("is_plugin") is False:
        return RepositoryAnalysis(
            is_plugin=False,
            title="Not a CS2 plugin",
            description="",
            category="other",
            framework="other",
        )
    try:
        return RepositoryAnalysis.model_validate(data)
    except ValidationError as exc:
        problems = []
        for error in exc.errors(include_input=False, include_url=False)[:3]:
            field = error["loc"][0] if error["loc"] else "response"
            if field not in RepositoryAnalysis.model_fields:
                field = "response"
            problems.append(f"{field}: {error['type']}")
        raise AnalysisFormatError(
            "AI analysis schema mismatch (" + "; ".join(problems) + ")"
        ) from exc
