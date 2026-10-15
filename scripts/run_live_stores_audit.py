#!/usr/bin/env python3
"""Trustworthy Live Stores Audit Harness for Shopify Nano-SRE."""

import argparse
import asyncio
import json
import logging
import os
import sys
import trace
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
    """Generate reports/live/SUMMARY.md programmatically from raw JSON files."""
    summary_md_path = reports_live_dir / "SUMMARY.md"

    store_dirs = sorted([d for d in reports_live_dir.iterdir() if d.is_dir()])

    lines = []
    lines.append("# Live Store Audit Summary\n")
    lines.append("| Store | " + " | ".join(ALL_SKILLS) + " |")
    lines.append("| --- | " + " | ".join(["---"] * len(ALL_SKILLS)) + " |")

    non_pass_details = []

    for sdir in store_dirs:
        domain = sdir.name
        row = [domain]
        for skill_name in ALL_SKILLS:
            json_file = sdir / f"{skill_name}.json"
            if json_file.exists():
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                    status = data.get("status", "UNKNOWN")
                    summary = data.get("summary", "")

                    status_badge = f"**{status}**"
                    if status == "PASS":
                        status_badge = "✅ PASS"
                    elif status == "WARN":
                        status_badge = "⚠️ WARN"
                    elif status == "FAIL":
                        status_badge = "❌ FAIL"

                    row.append(status_badge)

                    if status != "PASS":
                        non_pass_details.append(f"- **{domain}** [{skill_name}] ({status}): {summary}")
                except Exception as e:
                    row.append("ERROR")
            else:
                row.append("N/A")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("\n## Non-PASS Details\n")
    if non_pass_details:
        lines.extend(non_pass_details)
    else:
        lines.append("All skills passed across all audited stores.")

    lines.append("")
    summary_md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Generated SUMMARY.md at {summary_md_path}")


async def audit_single_store(domain: str, selected_skills: list[str], browser, settings: Settings, reports_live_dir: Path):
    store_dir = reports_live_dir / domain
    store_dir.mkdir(parents=True, exist_ok=True)

    log_file_path = store_dir / "run.log"
    log_entries = []

    def log(msg: str):
        print(f"[{domain}] {msg}", flush=True)
        log_entries.append(msg)

    log(f"Starting audit for store: https://{domain}/")

    # Setup single shared context and page per store
    context = await browser.new_context()
    page = await context.new_page()

    # Intercept pointer events tracker
    click_intercepts = []

    def on_console(msg):
        text = msg.text
        if "intercepts pointer events" in text or "intercept" in text.lower():
            log(f"Console intercept log: {text}")

    page.on("console", on_console)

    ajax_product_meta = {
        "status": None,
        "final_url": None,
        "handle": None,
    }

    async def handle_response(response):
        url = response.url
        if "/products/" in url and url.endswith(".js"):
            ajax_product_meta["status"] = response.status
            ajax_product_meta["final_url"] = url
            log(f"Captured /products/<handle>.js: HTTP {response.status} at {url}")

    page.on("response", handle_response)

    base_url = f"https://{domain}/"
    try:
        log(f"Navigating to {base_url} with wait_until='domcontentloaded'")
        await page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        log(f"Navigation error: {e}")

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

        # Check if click intercepts occurred
        intercept_info = []
        if result and result.status in ("WARN", "FAIL"):
            try:
                intercept_elements = await page.evaluate("""
                    () => {
                        const items = [];
                        const dialogs = document.querySelectorAll('[role="dialog"], .modal, [class*="modal"], [id*="modal"], [class*="overlay"], [id*="overlay"]');
                        dialogs.forEach(el => {
                            if (el.offsetWidth > 0 && el.offsetHeight > 0) {
                                items.push({
                                    tagName: el.tagName,
                                    outerHTML: el.outerHTML.substring(0, 300)
                                });
                            }
                        });
                        return items;
                    }
                """)
                intercept_info = intercept_elements
            except Exception as ie:
                log(f"Failed evaluating intercept elements: {ie}")

        result_dict = {
            "skill_name": skill_name,
            "status": result.status if result else "FAIL",
            "summary": result.summary if result else f"Execution failed: {err_str}",
            "details": result.details if result else {},
            "error": err_str,
            "screenshot": screenshot_file,
            "ajax_product_meta": ajax_product_meta,
            "intercepted_pointer_elements": intercept_info,
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
    args = parser.parse_args()

    target_stores = ALL_STORES
    if args.stores:
        target_stores = [s for s in ALL_STORES if any(fil in s for fil in args.stores)]
        if not target_stores:
            target_stores = args.stores

    selected_skills = ALL_SKILLS
    if args.skills:
        selected_skills = [sk for sk in ALL_SKILLS if sk in args.skills]

    reports_live_dir = Path("reports/live")
    reports_live_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings()

    print(f"Target Stores ({len(target_stores)}): {target_stores}")
    print(f"Target Skills ({len(selected_skills)}): {selected_skills}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        for domain in target_stores:
            try:
                await audit_single_store(
                    domain=domain,
                    selected_skills=selected_skills,
                    browser=browser,
                    settings=settings,
                    reports_live_dir=reports_live_dir,
                )
            except Exception as e:
                print(f"CRITICAL STORE FAILURE for {domain}: {e}\n{traceback.format_exc()}")

        await browser.close()

    generate_summary_md(reports_live_dir)


if __name__ == "__main__":
    asyncio.run(main())
