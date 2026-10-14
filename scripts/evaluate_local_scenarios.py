import asyncio
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from playwright.async_api import async_playwright
from nano_sre.skills.shopify_search_discovery_auditor import ShopifySearchDiscoveryAuditor

class LocalScenariosServer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path

        # Scenario 1: Healthy Search
        if path.startswith("/scenario1/product"):
            html = """<!doctype html><html><body>
            <h1>Healthy Search Product Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9001",
              "handle": "pass-snowboard",
              "title": "Pass Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form action="/scenario1/search" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send(200, html, "text/html")

        elif path.startswith("/scenario1/search"):
            html = """<!doctype html><html><body>
            <h1>Search Results</h1>
            <div id="search-results">
              <div class="product-card">
                <a href="/scenario1/products/pass-snowboard">Pass Snowboard</a>
              </div>
            </div>
            </body></html>"""
            self.send(200, html, "text/html")

        elif path.startswith("/scenario1/products/pass-snowboard"):
            html = """<!doctype html><html><body>
            <h1>Pass Snowboard PDP</h1>
            <p>Product ID: 9001</p>
            </body></html>"""
            self.send(200, html, "text/html")

        # Scenario 2: Disabled Submit Button
        elif path.startswith("/scenario2/product"):
            html = """<!doctype html><html><body>
            <h1>Disabled Submit Button Product Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9002",
              "handle": "disabled-snowboard",
              "title": "Disabled Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form action="/scenario2/search" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit" disabled>Search</button>
            </form>
            </body></html>"""
            self.send(200, html, "text/html")

        # Scenario 3: No-Op Submit (Form submit does nothing)
        elif path.startswith("/scenario3/product"):
            html = """<!doctype html><html><body>
            <h1>No-Op Submit Product Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9003",
              "handle": "noop-snowboard",
              "title": "No-Op Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form onsubmit="event.preventDefault(); return false;">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit" id="search-btn">Search</button>
            </form>
            </body></html>"""
            self.send(200, html, "text/html")

        # Scenario 4: Delayed Results (Renders after JS timeout)
        elif path.startswith("/scenario4/product"):
            html = """<!doctype html><html><body>
            <h1>Delayed Results Product Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9004",
              "handle": "delayed-snowboard",
              "title": "Delayed Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form action="/scenario4/search" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send(200, html, "text/html")

        elif path.startswith("/scenario4/search"):
            html = """<!doctype html><html><body>
            <h1>Search Results</h1>
            <div id="results-wrapper">Searching...</div>
            <script>
            setTimeout(() => {
              document.getElementById("results-wrapper").innerHTML = `
                <div id="search-results">
                  <div class="product-card">
                    <a href="/scenario4/products/delayed-snowboard">Delayed Snowboard</a>
                  </div>
                </div>
              `;
            }, 1000);
            </script>
            </body></html>"""
            self.send(200, html, "text/html")

        elif path.startswith("/scenario4/products/delayed-snowboard"):
            html = """<!doctype html><html><body>
            <h1>Delayed Snowboard PDP</h1>
            <p>Product ID: 9004</p>
            </body></html>"""
            self.send(200, html, "text/html")

        # Scenario 5: Result Link Mismatch (Points to Wrong Product)
        elif path.startswith("/scenario5/product"):
            html = """<!doctype html><html><body>
            <h1>Mismatched Result Link Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9005",
              "handle": "expected-snowboard",
              "title": "Expected Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form action="/scenario5/search" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send(200, html, "text/html")

        elif path.startswith("/scenario5/search"):
            html = """<!doctype html><html><body>
            <h1>Search Results</h1>
            <div id="search-results">
              <div class="product-card">
                <a href="/scenario5/products/wrong-product">Wrong Product Entirely</a>
              </div>
            </div>
            </body></html>"""
            self.send(200, html, "text/html")

        elif path.startswith("/scenario5/products/wrong-product"):
            html = """<!doctype html><html><body>
            <h1>Wrong Product PDP</h1>
            <p>Product ID: 9999</p>
            </body></html>"""
            self.send(200, html, "text/html")

        else:
            self.send(404, "Not Found", "text/plain")

    def send(self, status, body, content_type):
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

async def run_evaluations():
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalScenariosServer)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    print(f"Local mock server listening on {base_url}")

    evaluations = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        scenarios_def = [
            ("scenario1", "البحث السليم", f"{base_url}/scenario1/product", "Search submit succeeds and renders matching product destination link", "PASS"),
            ("scenario2", "زر إرسال معطل", f"{base_url}/scenario2/product", "Search submit button has 'disabled' attribute preventing submit", "FAIL"),
            ("scenario3", "إرسال لا يفعل شيئًا", f"{base_url}/scenario3/product", "Form submit event is prevented and no navigation/DOM search state change occurs", "FAIL"),
            ("scenario4", "نتائج متأخرة لكنها سليمة", f"{base_url}/scenario4/product", "Search page renders results asynchronously after 1000ms delay within timeout", "PASS"),
            ("scenario5", "رابط نتيجة يؤدي إلى منتج خاطئ", f"{base_url}/scenario5/product", "Search result link targets handle 'wrong-product' (ID 9999) instead of expected target 'expected-snowboard' (ID 9005)", "FAIL")
        ]

        auditor = ShopifySearchDiscoveryAuditor()

        for key, name, start_url, gt_desc, expected_verdict in scenarios_def:
            context = await browser.new_context()
            page = await context.new_page()

            await page.goto(start_url, wait_until="domcontentloaded")

            ctx = {
                "page": page,
                "base_url": start_url,
                "allow_live_audit": True,
                "search_query": "snowboard"
            }

            res = await auditor.run(ctx)

            evaluations.append({
                "key": key,
                "name": name,
                "start_url": start_url,
                "ground_truth": gt_desc,
                "expected_verdict": expected_verdict,
                "tool_verdict": res.status,
                "summary": res.summary,
                "details": res.details
            })

            await context.close()

        await browser.close()

    server.shutdown()
    server.server_close()

    with open("reports/evaluation/local_scenarios_evaluation.json", "w", encoding="utf-8") as f:
        json.dump(evaluations, f, indent=2, ensure_ascii=False)

    print("Local scenarios evaluation complete!")
    print(json.dumps(evaluations, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(run_evaluations())
