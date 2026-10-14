import asyncio
import json
import os
from playwright.async_api import async_playwright
from nano_sre.skills.shopify_search_discovery_auditor import ShopifySearchDiscoveryAuditor

async def evaluate_sauceshop():
    os.makedirs("reports/evaluation", exist_ok=True)

    gt_data = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Ground Truth Probe
        print("Navigating to Sauce Shop homepage for Ground Truth observation...")
        await page.goto("https://www.sauceshop.co/", wait_until="domcontentloaded", timeout=25000)

        # Look for search toggle button if search input is hidden
        search_toggle = page.locator("button[aria-label*='Search' i], a[href*='/search'], button:has-text('Search'), [data-search-toggle]").first
        if await search_toggle.count() > 0 and await search_toggle.is_visible():
            try:
                await search_toggle.click(timeout=3000)
                await page.wait_for_timeout(1000)
            except Exception:
                pass

        search_input = page.locator("input[type='search'], input[name='q'], input[placeholder*='Search' i]").first
        search_opened = False
        query_submitted = False

        if await search_input.count() > 0 and await search_input.is_visible():
            search_opened = True
            await search_input.fill("sauce")
            await search_input.press("Enter")
            await page.wait_for_timeout(3000)
            query_submitted = True
        else:
            # direct navigation fallback
            print("Direct navigation fallback to /search?q=sauce...")
            await page.goto("https://www.sauceshop.co/search?q=sauce", wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(3000)
            search_opened = True
            query_submitted = True

        gt_html = await page.content()
        gt_screenshot = "reports/evaluation/sauceshop_gt.png"
        gt_html_path = "reports/evaluation/sauceshop_gt.html"
        await page.screenshot(path=gt_screenshot, full_page=True)
        with open(gt_html_path, "w", encoding="utf-8") as f:
            f.write(gt_html)

        page_text = await page.inner_text("body")
        has_results = "sauce" in page_text.lower() or "result" in page_text.lower() or "product" in page_text.lower()
        results_or_no_results_found = has_results or ("no result" in page_text.lower() or "0 result" in page_text.lower())

        gt_data["ground_truth"] = {
            "search_opened": search_opened,
            "query_submitted": query_submitted,
            "results_or_no_results_rendered": results_or_no_results_found,
            "screenshot": gt_screenshot,
            "html": gt_html_path,
            "url_after_search": page.url
        }

        # Run Auditor on same context/URL
        print("Running shopify_search_discovery_auditor on Sauce Shop...")
        auditor = ShopifySearchDiscoveryAuditor()
        ctx = {
            "page": page,
            "base_url": "https://www.sauceshop.co/",
            "allow_live_audit": True,
            "search_query": "sauce"
        }
        res = await auditor.run(ctx)

        gt_data["auditor_verdict"] = {
            "status": res.status,
            "summary": res.summary,
            "details": res.details
        }

        await browser.close()

    with open("reports/evaluation/sauceshop_evaluation.json", "w", encoding="utf-8") as f:
        json.dump(gt_data, f, indent=2)

    print("Sauce Shop Ground Truth Evaluation complete!")
    print(json.dumps(gt_data, indent=2))

if __name__ == "__main__":
    asyncio.run(evaluate_sauceshop())
