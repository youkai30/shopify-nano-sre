"""Report generation for incident reports."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from nano_sre.agent.core import SkillResult

logger = logging.getLogger(__name__)


async def generate_report(
    results: list[SkillResult],
    store_url: str,
    report_dir: str = "reports",
    llm_configured: bool = False,
    ai_diagnosis: Optional[str] = None,
) -> str:
    """
    Generate an incident report in markdown format.

    Args:
        results: List of SkillResult objects from skill executions
        store_url: The Shopify store URL that was monitored
        report_dir: Directory to save the report (default: "reports")
        llm_configured: Whether LLM is configured for AI diagnosis
        ai_diagnosis: Optional AI-generated diagnosis text

    Returns:
        Path to the generated report file

    Example:
        >>> results = [SkillResult(...), SkillResult(...)]
        >>> report_path = await generate_report(
        ...     results,
        ...     "https://mystore.myshopify.com",
        ...     report_dir="reports"
        ... )
    """
    # Create report directory if it doesn't exist
    report_path = Path(report_dir)
    report_path.mkdir(parents=True, exist_ok=True)

    # Generate filename with timestamp
    timestamp = datetime.now(timezone.utc)
    filename = f"incident_report_{timestamp.strftime('%Y%m%d_%H%M%S')}.md"
    report_file = report_path / filename

    # Generate report content
    content = _generate_report_content(
        results=results,
        store_url=store_url,
        timestamp=timestamp,
        llm_configured=llm_configured,
        ai_diagnosis=ai_diagnosis,
    )

    # Write report to file
    report_file.write_text(content, encoding="utf-8")
    logger.info(f"Generated incident report: {report_file}")

    return str(report_file)


def _format_timestamp(dt: datetime) -> str:
    """
    Format a datetime object as a string.

    Handles both timezone-aware and timezone-naive datetimes.
    For timezone-naive datetimes (from SkillResult), assumes UTC.

    Args:
        dt: Datetime to format

    Returns:
        Formatted timestamp string
    """
    # If timezone-naive, assume UTC (as per SkillResult's datetime.utcnow() usage)
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    # If timezone-aware, convert to UTC and format
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _generate_report_content(
    results: list[SkillResult],
    store_url: str,
    timestamp: datetime,
    llm_configured: bool = False,
    ai_diagnosis: Optional[str] = None,
) -> str:
    """
    Generate the markdown content for the incident report.

    Args:
        results: List of SkillResult objects
        store_url: Store URL
        timestamp: Report timestamp
        llm_configured: Whether LLM is configured
        ai_diagnosis: Optional AI diagnosis

    Returns:
        Markdown formatted report content
    """
    lines = []

    # Header section
    lines.append("# Incident Report")
    lines.append("")
    lines.append(f"**Generated:** {_format_timestamp(timestamp)}")
    lines.append(f"**Store URL:** {store_url}")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Skill | Status | Summary |")
    lines.append("|-------|--------|---------|")

    for result in results:
        status_emoji = _get_status_emoji(result.status)
        skill_name = result.skill_name
        summary = result.summary.replace("\n", " ").replace("|", "\\|")
        lines.append(f"| {skill_name} | {status_emoji} {result.status} | {summary} |")

    lines.append("")

    # Overall statistics
    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    warned = sum(1 for r in results if r.status == "WARN")
    failed = sum(1 for r in results if r.status == "FAIL")

    lines.append(
        f"**Total Skills:** {total} | **Passed:** {passed} | **Warnings:** {warned} | **Failed:** {failed}"
    )
    lines.append("")

    # Per-skill findings
    lines.append("## Detailed Findings")
    lines.append("")

    for result in results:
        lines.append(f"### {result.skill_name}")
        lines.append("")
        lines.append(f"**Status:** {_get_status_emoji(result.status)} {result.status}")
        lines.append(f"**Timestamp:** {_format_timestamp(result.timestamp)}")
        lines.append(f"**Summary:** {result.summary}")
        lines.append("")

        # Error details if present
        if result.error:
            lines.append("**Error:**")
            lines.append("```")
            lines.append(result.error)
            lines.append("```")
            lines.append("")

        # Additional details
        if result.details:
            lines.append("**Details:**")
            lines.append("")
            lines.append(_format_details(result.details))
            lines.append("")

        # Screenshots
        if result.screenshots:
            lines.append("**Screenshots:**")
            lines.append("")
            for screenshot in result.screenshots:
                lines.append(f"- `{screenshot}`")
            lines.append("")

        lines.append("---")
        lines.append("")

    # Recommended actions
    lines.append("## Recommended Actions")
    lines.append("")

    actions = _generate_recommended_actions(results)
    if actions:
        for action in actions:
            lines.append(f"- {action}")
    else:
        lines.append("- ✅ All checks passed. No immediate action required.")

    lines.append("")

    # AI Diagnosis (optional)
    if llm_configured and ai_diagnosis:
        lines.append("## AI Diagnosis")
        lines.append("")
        lines.append(ai_diagnosis)
        lines.append("")

    return "\n".join(lines)


def _get_status_emoji(status: str) -> str:
    """Get emoji for status."""
    status_emojis = {
        "PASS": "✅",
        "WARN": "⚠️",
        "FAIL": "❌",
    }
    return status_emojis.get(status, "❓")


def _format_details(details: dict[str, Any], indent: int = 0) -> str:
    """
    Format details dictionary as markdown.

    Args:
        details: Details dictionary
        indent: Indentation level

    Returns:
        Formatted markdown string
    """
    lines = []
    indent_str = "  " * indent

    for key, value in details.items():
        # Skip internal/redundant keys
        if key in ("mcp_configured", "llm_used", "api_version_in_use", "mock_mode"):
            continue

        if isinstance(value, dict):
            if value:
                rendered = _format_details(value, indent + 1)
                if rendered:
                    lines.append(f"{indent_str}- **{key}:**")
                    lines.append(rendered)
        elif isinstance(value, list):
            if value or key == "cart_evidence":
                # Special handling for MCP recommendations to make them readable
                if key == "recommendations":
                    lines.append(f"{indent_str}- **{key}:**")
                    for item in value:
                        if isinstance(item, dict):
                            error = item.get("error", "Issue")
                            lines.append(f"{indent_str}  - **{error}:**")
                            # If explanation is a list (from search results), format as direct links
                            explanation = item.get("explanation")
                            if isinstance(explanation, list):
                                for exp in explanation:
                                    if isinstance(exp, dict) and "url" in exp:
                                        title = exp.get("title", "Reference")
                                        lines.append(f"{indent_str}    - [{title}]({exp['url']})")
                            elif explanation:
                                lines.append(f"{indent_str}    - {explanation}")

                            fix = item.get("recommended_fix")
                            if fix and "See the search results" not in fix:
                                lines.append(f"{indent_str}    - **Fix:** {fix}")
                else:
                    lines.append(f"{indent_str}- **{key}:**")
                    for item in value:
                        if isinstance(item, dict):
                            rendered = _format_details(item, indent + 1)
                            if rendered:
                                lines.append(rendered)
                        else:
                            lines.append(f"{indent_str}  - {item}")
        elif value is not None and value != "" and value != "(empty)":
            lines.append(f"{indent_str}- **{key}:** {value}")

    return "\n".join(lines)


def _generate_recommended_actions(results: list[SkillResult]) -> list[str]:
    """
    Generate recommended actions based on skill results.

    Args:
        results: List of SkillResult objects

    Returns:
        List of recommended action strings
    """
    actions = []

    # Group results by status
    failed = [r for r in results if r.status == "FAIL"]
    warned = [r for r in results if r.status == "WARN"]

    # Critical failures
    if failed:
        actions.append(
            f"🚨 **Critical:** {len(failed)} skill(s) failed - immediate investigation required"
        )
        for result in failed:
            actions.append(f"  - Investigate {result.skill_name}: {result.summary}")

    # Warnings
    if warned:
        actions.append(
            f"⚠️ **Warning:** {len(warned)} skill(s) reported warnings - review recommended"
        )
        for result in warned:
            actions.append(f"  - Review {result.skill_name}: {result.summary}")

    # Check for specific issues
    for result in results:
        if result.details:
            # Rate limit issues
            if "rate_limit_issues" in result.details and result.details["rate_limit_issues"]:
                actions.append("📊 Consider implementing rate limit backoff strategies")

            # Hydration mismatches
            if "hydration_mismatches" in result.details and result.details["hydration_mismatches"]:
                actions.append("⚛️ Review server-side rendering and client hydration logic")

            # Stale data
            if "stale_data_issues" in result.details and result.details["stale_data_issues"]:
                actions.append("🔄 Check ISR/cache invalidation configuration")

    return actions
