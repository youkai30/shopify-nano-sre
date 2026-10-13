"""Live Search Discovery Audit Script for Sauce Shop."""

import asyncio
import json
import os
from playwright.async_api import async_playwright
from nano_sre.skills.shopify_search_discovery_auditor import ShopifySearchDiscoveryAuditor


async def run_sauceshop_live_audit():
    os.makedirs("reports", exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        print("Navigating to Sauce Shop homepage...")
        resp = await page.goto("https://www.sauceshop.co/", wait_until="domcontentloaded", timeout=20000)
        initial_url = page.url
        http_status = resp.status if resp else 200

        auditor = ShopifySearchDiscoveryAuditor()
        ctx = {
            "page": page,
            "base_url": initial_url,
            "allow_live_audit": True,
            "search_query": "buffalo"
        }

        print("Executing search discovery auditor for query 'buffalo'...")
        result = await auditor.run(ctx)
        final_url = page.url

        print(f"Audit completed. Status: {result.status}")
        print(f"Final URL after audit: {final_url}")

        report_data = {
            "store_url": "https://www.sauceshop.co/",
            "initial_url": initial_url,
            "final_url": final_url,
            "http_status": http_status,
            "command": "PYTHONPATH=src MCP_ENABLED=false LLM_PROVIDER=openai LLM_API_KEY=\"\" python3 scripts/run_sauceshop_live_search.py",
            "allow_live_audit_activation": "Passed context parameter allow_live_audit=True with active search_guardrail_route blocking mutation endpoints (/cart/add, /checkout, /cart/update, /cart/clear, /account, /challenge)",
            "query_tested": "buffalo",
            "lead_candidate": False,
            "audit_result": {
                "status": result.status,
                "summary": result.summary,
                "details": result.details
            }
        }

        with open("reports/sauceshop_live_search.json", "w") as f:
            json.dump(report_data, f, indent=2)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(run_sauceshop_live_audit())
