"""CLI entry point for Shopify Nano-SRE."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Iterable, Optional

import click
from playwright.async_api import async_playwright
from rich.console import Console
from rich.table import Table

from nano_sre.agent.core import Agent, Skill
from nano_sre.agent.reporter import generate_report
from nano_sre.config.settings import Settings, get_settings
from nano_sre.skills import (
    HeadlessProbeSkill,
    MCPAdvisor,
    PixelAuditor,
    ShopifyDoctorSkill,
    ShopifyMobilePurchaseBlockerAuditor,
    ShopifyPurchaseBlockerAuditor,
    ShopifyShopper,
    ShopifyVariantAuditor,
    VisualAuditor,
)

DEFAULT_SKILLS = [
    "shopify_shopper",
    "pixel_auditor",
    "visual_auditor",
    "shopify_doctor",
    "headless_probe",
    "mcp_advisor",
]
from nano_sre.utils.llm import is_vision_model
from nano_sre.utils.mcp import get_mcp_client
from nano_sre.utils.shopify import bypass_shopify_password

console = Console()
logger = logging.getLogger(__name__)


@click.group()
@click.option(
    "--report-dir",
    help="Directory to save incident reports",
)
@click.pass_context
def main(ctx, report_dir: Optional[str]):
    """Shopify Nano-SRE: The open-source AI engineer that monitors your Shopify store."""
    # Ensure context object exists
    ctx.ensure_object(dict)

    # Store report_dir in context for subcommands (will use default if not provided)
    ctx.obj["report_dir"] = report_dir or "reports"


@main.command()
@click.option(
    "--url",
    help="URL of the Shopify store to audit (e.g., https://your-store.myshopify.com)",
)
@click.option(
    "--password",
    help="Shopify store password (if protected)",
)
@click.option(
    "--skill",
    "skill_names",
    multiple=True,
    help="Run only the specified skill(s) (repeatable)",
)
@click.option(
    "--update-baseline",
    is_flag=True,
    help="Update visual audit baselines instead of comparing",
)
@click.option(
    "--output",
    required=False,
    help="Path to save the audit report as JSON",
)
@click.pass_context
def audit(
    ctx,
    url: Optional[str],
    password: Optional[str],
    skill_names: Iterable[str],
    update_baseline: bool,
    output: Optional[str],
):
    """Run an audit on the specified Shopify store URL."""
    report_dir = ctx.obj.get("report_dir", "reports")
    asyncio.run(
        _run_audit(
            url,
            password=password,
            report_dir=report_dir,
            output=output,
            skill_names=list(skill_names),
            update_baseline=update_baseline,
        )
    )


@main.command()
@click.option(
    "--url",
    help="URL of the Shopify store to monitor (e.g., https://your-store.myshopify.com)",
)
@click.option(
    "--password",
    help="Shopify store password (if protected)",
)
@click.option(
    "--interval",
    help="Interval in minutes between checks",
)
@click.option(
    "--skill",
    "skill_names",
    multiple=True,
    help="Run only the specified skill(s) (repeatable)",
)
@click.pass_context
def watch(
    ctx,
    url: Optional[str],
    password: Optional[str],
    interval: Optional[int],
    skill_names: Iterable[str],
):
    """Continuously monitor a Shopify store at a fixed interval."""
    report_dir = ctx.obj.get("report_dir", "reports")
    asyncio.run(
        _run_watch(
            url,
            password=password,
            interval_minutes=interval,
            report_dir=report_dir,
            skill_names=list(skill_names),
        )
    )


@main.group()
def baseline():
    """Baseline operations for visual audits."""


@baseline.command("update")
@click.option(
    "--url",
    help="URL of the Shopify store to baseline (e.g., https://your-store.myshopify.com)",
)
@click.option(
    "--password",
    help="Shopify store password (if protected)",
)
@click.option(
    "--skill",
    "skill_names",
    multiple=True,
    help="Baseline-capable skill(s) to run (default: visual_auditor)",
)
@click.pass_context
def baseline_update(ctx, url: str, password: Optional[str], skill_names: Iterable[str]):
    """Update visual audit baselines for a store."""
    report_dir = ctx.obj.get("report_dir", "reports")
    selected_skills = list(skill_names) if skill_names else ["visual_auditor"]
    normalized = {_normalize_skill_name(name) for name in selected_skills}
    if normalized != {"visual_auditor"}:
        raise click.BadParameter("Baseline updates currently support only visual_auditor.")
    asyncio.run(
        _run_audit(
            url,
            password=password,
            report_dir=report_dir,
            skill_names=selected_skills,
            update_baseline=True,
        )
    )


@main.group()
def report():
    """Report operations."""


@report.command("show")
@click.option(
    "--latest/--no-latest",
    default=True,
    show_default=True,
    help="Show the most recent report",
)
@click.option(
    "--path",
    "report_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to a specific report file",
)
@click.pass_context
def report_show(ctx, latest: bool, report_path: Optional[Path]):
    """Show the latest incident report."""
    if report_path:
        content = report_path.read_text(encoding="utf-8")
        console.print(content)
        return

    report_dir = Path(ctx.obj.get("report_dir", "reports"))
    if not report_dir.exists():
        console.print("[yellow]No reports directory found.[/yellow]")
        raise click.Abort()

    reports = sorted(
        report_dir.glob("incident_report_*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not reports:
        console.print("[yellow]No reports found.[/yellow]")
        raise click.Abort()

    if latest:
        content = reports[0].read_text(encoding="utf-8")
        console.print(content)
        return

    console.print("[yellow]No report selected. Use --latest or --path.[/yellow]")
    raise click.Abort()


async def _run_audit(
    url: Optional[str],
    report_dir: str,
    password: Optional[str] = None,
    output: Optional[str] = None,
    skill_names: Optional[list[str]] = None,
    update_baseline: bool = False,
):
    """Execute the audit asynchronously."""
    try:
        settings = get_settings()
        # Override values if provided via CLI
        if url:
            settings.store_url = url  # type: ignore
        if password:
            settings.store_password = password

        # Ensure we have a URL - now check for None since store_url is Optional
        if not settings.store_url:
            raise click.UsageError(
                "Store URL is required. Provide it via --url option or STORE_URL environment variable"
            )

        url = settings.store_url_str
        if not url or url == "None":
            raise click.UsageError(
                "Store URL is required. Provide it via --url option or STORE_URL environment variable"
            )

        console.print(f"[bold blue]Starting audit for:[/bold blue] {url}")

        agent = Agent(settings)
        skills = _build_skills(settings=settings, update_baseline=update_baseline)
        for skill in skills.values():
            agent.register_skill(skill)

        selected_skills = _resolve_skill_names(skill_names, skills.keys())

        async with async_playwright() as p:
            # Emulate iPhone 17 Pro
            try:
                iphone_17 = p.devices["iPhone 17 Pro"]
            except KeyError:
                # Fallback manual definition if Playwright is not yet updated for 2026 devices
                iphone_17 = {
                    "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Mobile/15E148 Safari/604.1",
                    "viewport": {"width": 430, "height": 932},
                    "device_scale_factor": 3,
                    "is_mobile": True,
                    "has_touch": True,
                }

            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(**iphone_17)
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="commit", timeout=60000)

                # Handle Shopify password if needed
                if settings.store_password:
                    await bypass_shopify_password(page, settings.store_password)

                context_data = {
                    "page": page,
                    "base_url": url,
                    "url": url,
                    "settings": settings,
                }

                # Initialize MCP client if enabled
                async with get_mcp_client(
                    command=settings.mcp_command if settings.mcp_enabled else None,
                    args=settings.mcp_args if settings.mcp_enabled else [],
                    url=settings.mcp_server_url if settings.mcp_enabled else None,
                ) as mcp_client:
                    if mcp_client:
                        context_data["mcp_client"] = mcp_client

                    results = await agent.execute_skills(
                        skill_names=selected_skills,
                        context=context_data,
                    )

                _display_results(results)

                llm_configured = bool(settings.llm_api_key or settings.llm_provider == "ollama")

                # Perform AI diagnosis for failures if LLM is available
                ai_diagnosis = None
                if llm_configured:
                    console.print("[bold yellow]Performing AI failure diagnosis...[/bold yellow]")
                    from nano_sre.agent.diagnosis import diagnose

                    # Diagnose individual failures and collect them
                    diagnoses = []
                    for r in results:
                        if r.status in ["WARN", "FAIL"]:
                            diag = await diagnose(r)
                            diagnoses.append(
                                f"### Diagnosis for {r.skill_name}\n\n**Root Cause:** {diag.get('root_cause')}\n\n**Fix:** {diag.get('recommended_fix')}"
                            )

                    if diagnoses:
                        ai_diagnosis = "\n\n".join(diagnoses)

                report_path = await generate_report(
                    results=results,
                    store_url=url,
                    report_dir=report_dir,
                    llm_configured=llm_configured,
                    ai_diagnosis=ai_diagnosis,
                )

                console.print(f"\n[green]Report saved to:[/green] {report_path}")

                if output:
                    output_path = Path(output)
                    output_path.parent.mkdir(parents=True, exist_ok=True)

                    report_data = {
                        "url": url,
                        "results": [
                            {
                                "skill_name": r.skill_name,
                                "status": r.status,
                                "summary": r.summary,
                                "details": r.details,
                            }
                            for r in results
                        ],
                    }

                    output_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")
                    console.print(f"[green]JSON output saved to:[/green] {output_path}")

            finally:
                await browser.close()

    except Exception as e:
        console.print(f"[bold red]Error during audit:[/bold red] {str(e)}")
        logger.exception("Audit failed")
        raise click.Abort()


async def _run_watch(
    url: Optional[str],
    password: Optional[str],
    interval_minutes: Optional[int],
    report_dir: str,
    skill_names: Optional[list[str]] = None,
):
    """Execute continuous monitoring at a fixed interval."""
    settings = get_settings()
    interval = interval_minutes or settings.check_interval_minutes
    interval = max(interval, 1)
    try:
        while True:
            await _run_audit(
                url,
                password=password,
                report_dir=report_dir,
                skill_names=skill_names,
            )
            console.print(f"[dim]Next check in {interval} minute(s). Press Ctrl+C to stop.[/dim]")
            await asyncio.sleep(interval * 60)
    except KeyboardInterrupt:
        console.print("[yellow]Monitoring stopped.[/yellow]")


def _display_results(results):
    """Display audit results in a formatted table."""
    if not results:
        console.print("[yellow]No results to display.[/yellow]")
        return

    # Create a table
    table = Table(title="Audit Results")
    table.add_column("Skill", style="cyan", no_wrap=True)
    table.add_column("Status", style="magenta")
    table.add_column("Summary", style="white")

    for result in results:
        # Color code status
        status_color = {
            "PASS": "[green]PASS[/green]",
            "WARN": "[yellow]WARN[/yellow]",
            "FAIL": "[red]FAIL[/red]",
        }.get(result.status, result.status)

        table.add_row(
            result.skill_name,
            status_color,
            result.summary,
        )

    console.print(table)

    # Print overall summary
    passed = sum(1 for r in results if r.status == "PASS")
    warned = sum(1 for r in results if r.status == "WARN")
    failed = sum(1 for r in results if r.status == "FAIL")

    console.print(f"\n[bold]Summary:[/bold] {passed} passed, {warned} warned, {failed} failed")


def _normalize_skill_name(name: str) -> str:
    """Normalize skill names for CLI matching."""
    return name.strip().lower().replace("-", "_")


def _build_skills(settings: Settings, update_baseline: bool) -> dict[str, Skill]:
    """Build available skill instances keyed by their canonical names."""
    # Only enable vision if model supports it
    use_vision = settings.llm_api_key and is_vision_model(settings.llm_model)

    skill_instances: list[Skill] = [
        ShopifyShopper(),
        PixelAuditor(mock_mode=False),
        VisualAuditor(
            llm_client={"model": settings.llm_model} if use_vision else None,
            update_baseline=update_baseline,
        ),
        ShopifyDoctorSkill(),
        HeadlessProbeSkill(),
        MCPAdvisor(),
        ShopifyVariantAuditor(),
        ShopifyPurchaseBlockerAuditor(),
        ShopifyMobilePurchaseBlockerAuditor(),
    ]
    return {skill.name(): skill for skill in skill_instances}


def _resolve_skill_names(
    requested: Optional[list[str]],
    available: Iterable[str],
) -> Optional[list[str]]:
    """Resolve user-specified skill names to canonical names."""
    if not requested:
        return DEFAULT_SKILLS.copy()

    available_set = {_normalize_skill_name(name) for name in available}
    resolved = []
    unknown = []

    for name in requested:
        normalized = _normalize_skill_name(name)
        if normalized in available_set:
            resolved.append(normalized)
        else:
            unknown.append(name)

    if unknown:
        available_list = ", ".join(sorted(available_set))
        raise click.BadParameter(
            f"Unknown skill(s): {', '.join(unknown)}. Available: {available_list}"
        )

    return resolved


if __name__ == "__main__":
    main()
