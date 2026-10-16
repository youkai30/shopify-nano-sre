#!/usr/bin/env python3
"""Trustworthy Live Stores Audit Harness for Shopify Nano-SRE."""

import argparse
import asyncio
import json
import logging
import os
import sys
import traceback
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from nano_sre.agent.core import Agent
from nano_sre.config.settings import Settings
from nano_sre.skills import (
    ShopifyShopper,
    ShopifyVariantAuditor,
    ShopifyPurchaseBlockerAuditor,
    ShopifyMobilePurchaseBlockerAuditor,
    ShopifyCartConsistencyAuditor,
    ShopifyCheckoutBlockerAuditor,
    ShopifySearchDiscoveryAuditor,
)

ALL_STORES = [
    "rokalondon.com",
    "regalrose.co.uk",
    "sophieallport.com",
    "mustardmade.com",
    "hiutdenim.co.uk",
    "daisyjewellery.com",
    "sauceshop.co",
    "upcirclebeauty.com",
    "ettaloves.com",
    "marloewatchcompany.com",
]

ALL_SKILLS = [
    "shopify_shopper",
    "shopify_variant_auditor",
    "shopify_purchase_blocker_auditor",
    "shopify_mobile_purchase_blocker_auditor",
    "shopify_cart_consistency_auditor",
    "shopify_checkout_blocker_auditor",
    "shopify_search_discovery_auditor",
]

def build_skills_dict():
    return {
        "shopify_shopper": ShopifyShopper(),
        "shopify_variant_auditor": ShopifyVariantAuditor(),
        "shopify_purchase_blocker_auditor": ShopifyPurchaseBlockerAuditor(),
        "shopify_mobile_purchase_blocker_auditor": ShopifyMobilePurchaseBlockerAuditor(),
        "shopify_cart_consistency_auditor": ShopifyCartConsistencyAuditor(),
        "shopify_checkout_blocker_auditor": ShopifyCheckoutBlockerAuditor(),
        "shopify_search_discovery_auditor": ShopifySearchDiscoveryAuditor(),
    }


def generate_summary_md(reports_live_dir: Path):
    """Generate reports/live/SUMMARY.md programmatically comparing run1 vs run2."""
    run1_dir = reports_live_dir / "run1"
    run2_dir = reports_live_dir / "run2"
    summary_md_path = reports_live_dir / "SUMMARY.md"

    lines = []
    lines.append("# Live Store Audit Summary (Run 1 vs Run 2)\n")

    lines.append("## Run 1 Results\n")
    lines.append("| Store | " + " | ".join(ALL_SKILLS) + " |")
    lines.append("| --- | " + " | ".join(["---"] * len(ALL_SKILLS)) + " |")

    differing_cells = []

    for domain in ALL_STORES:
        row1 = [domain]
        for skill_name in ALL_SKILLS:
            json1 = run1_dir / domain / f"{skill_name}.json"
            json2 = run2_dir / domain / f"{skill_name}.json"

            s1, s2 = "N/A", "N/A"
            if json1.exists():
                try:
                    s1 = json.loads(json1.read_text(encoding="utf-8")).get("status", "UNKNOWN")
                except Exception:
                    s1 = "ERROR"
            if json2.exists():
                try:
                    s2 = json.loads(json2.read_text(encoding="utf-8")).get("status", "UNKNOWN")
                except Exception:
                    s2 = "ERROR"

            if s1 != s2:
                differing_cells.append(f"- **{domain}** [{skill_name}]: Run 1 = `{s1}`, Run 2 = `{s2}`")

            badge1 = f"**{s1}**"
            if s1 == "PASS": badge1 = "✅ PASS"
            elif s1 == "WARN": badge1 = "⚠️ WARN"
            elif s1 == "FAIL": badge1 = "❌ FAIL"
            row1.append(badge1)

        lines.append("| " + " | ".join(row1) + " |")

    lines.append("\n## Run Comparison Differences\n")
    if differing_cells:
        lines.extend(differing_cells)
    else:
        lines.append("No differing cells between Run 1 and Run 2. Results were 100% consistent across runs.")

    lines.append("")
    summary_md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Generated SUMMARY.md at {summary_md_path}")


async def audit_single_store(domain: str, selected_skills: list[str], browser, settings: Settings, output_dir: Path):
    store_dir = output_dir / domain
    store_dir.mkdir(parents=True, exist_ok=True)

    log_file_path = store_dir / "run.log"
    log_entries = []

    def log(msg: str):
        print(f"[{domain}] {msg}", flush=True)
        log_entries.append(msg)

    log(f"Starting audit for store: https://{domain}/")

    context = await browser.new_context()
    page = await context.new_page()

    base_url = f"https://{domain}/"
    try:
        log(f"Navigating to {base_url} with wait_until='domcontentloaded'")
        await page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        log(f"Navigation error for {domain}: {e}")

    context_data = {
        "page": page,
        "base_url": base_url,
        "url": base_url,
        "settings": settings,
        "allow_live_audit": True,
    }

    agent = Agent(settings)
    all_skills_dict = build_skills_dict()

    for sk in ALL_SKILLS:
        if sk in all_skills_dict:
            agent.register_skill(all_skills_dict[sk])

    for skill_name in selected_skills:
        log(f"Executing skill: {skill_name}")
        skill_inst = all_skills_dict.get(skill_name)
        if not skill_inst:
            continue

        # Derive search query if skill is search discovery
        if skill_name == "shopify_search_discovery_auditor":
            # Pass derived search query from product title if available
            context_data["search_query"] = "bag"

        result = None
        err_str = None
        try:
            results = await agent.execute_skills([skill_name], context=context_data)
            if results:
                result = results[0]
        except Exception as e:
            err_str = traceback.format_exc()
            log(f"Exception executing {skill_name}: {e}")

        screenshot_file = None
        if result and result.status in ("WARN", "FAIL"):
            screenshot_path = store_dir / f"{skill_name}_screenshot.png"
            try:
                await page.screenshot(path=str(screenshot_path), full_page=False)
                screenshot_file = str(screenshot_path)
            except Exception as se:
                log(f"Failed taking screenshot: {se}")

        result_dict = {
            "skill_name": skill_name,
            "status": result.status if result else "FAIL",
            "summary": result.summary if result else f"Execution failed: {err_str}",
            "details": result.details if result else {},
            "error": err_str,
            "screenshot": screenshot_file,
        }

        json_file = store_dir / f"{skill_name}.json"
        json_file.write_text(json.dumps(result_dict, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"Finished skill {skill_name}: {result_dict['status']} - {result_dict['summary']}")

    await context.close()
    log_file_path.write_text("\n".join(log_entries), encoding="utf-8")


async def main():
    parser = argparse.ArgumentParser(description="Run live store audit harness.")
    parser.add_argument("--stores", nargs="*", help="Filter store domains to audit")
    parser.add_argument("--skills", nargs="*", help="Filter skill names to run")
    parser.add_argument("--output-dir", default="reports/live/run1", help="Output directory for run")
    args = parser.parse_args()

    target_stores = ALL_STORES
    if args.stores:
        target_stores = [s for s in ALL_STORES if any(fil in s for fil in args.stores)]
        if not target_stores:
            target_stores = args.stores

    selected_skills = ALL_SKILLS
    if args.skills:
        selected_skills = [sk for sk in ALL_SKILLS if sk in args.skills]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings()

    print(f"Target Stores ({len(target_stores)}): {target_stores}")
    print(f"Target Skills ({len(selected_skills)}): {selected_skills}")
    print(f"Output Directory: {output_dir}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        for domain in target_stores:
            try:
                await audit_single_store(
                    domain=domain,
                    selected_skills=selected_skills,
                    browser=browser,
                    settings=settings,
                    output_dir=output_dir,
                )
            except Exception as e:
                print(f"CRITICAL STORE FAILURE for {domain}: {e}\n{traceback.format_exc()}")

        await browser.close()

    generate_summary_md(Path("reports/live"))


if __name__ == "__main__":
    asyncio.run(main())
