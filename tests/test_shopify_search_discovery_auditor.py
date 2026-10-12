"""Tests for ShopifySearchDiscoveryAuditor Skill (Phase 7)."""

import asyncio
import json
import threading
import pytest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from playwright.async_api import async_playwright
from nano_sre.cli import DEFAULT_SKILLS, _build_skills, get_settings
from nano_sre.skills.shopify_search_discovery_auditor import ShopifySearchDiscoveryAuditor


class ComprehensiveMockSearchServer(BaseHTTPRequestHandler):
    flaky_count = [0]
    differing_count = [0]

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path

        if path.startswith("/products/search-pass"):
            html = """<!doctype html><html><body>
            <h1>Search Pass Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9001",
              "handle": "snowboard-pass",
              "title": "Pass Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form action="/search" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/preexisting-results-no-action-fail"):
            html = """<!doctype html><html><body>
            <h1>Preexisting Results No Action Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9013",
              "handle": "snowboard-pass",
              "title": "Pass Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form action="#" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit" id="sbtn2">Search</button>
            </form>
            <div id="search-results">
              <div class="product-card">
                <a href="/products/preexisting-item">Pre-existing Item</a>
              </div>
            </div>
            <script>
            document.getElementById('sbtn2').onclick = (e) => { e.preventDefault(); };
            </script>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/missing-fixture-warn"):
            html = """<!doctype html><html><body>
            <h1>No Fixture Demo</h1>
            <form action="/search" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/ineligible-disabled-button-warn"):
            html = """<!doctype html><html><body>
            <h1>Ineligible Disabled Button Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9002",
              "handle": "ineligible-item",
              "title": "Ineligible Item",
              "published_status": false,
              "eligible_for_search": false,
              "expected_query": "snowboard"
            }
            </script>
            <form action="/search" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit" disabled>Search</button>
            </form>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/disabled-button-fail"):
            html = """<!doctype html><html><body>
            <h1>Disabled Search Button Fail Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9003",
              "handle": "snowboard-fail",
              "title": "Fail Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form action="/search" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit" disabled>Search</button>
            </form>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/overlay-blocked-fail"):
            html = """<!doctype html><html><body>
            <h1>Overlay Blocked Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9004",
              "handle": "overlay-item",
              "title": "Overlay Item",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <div class="modal-backdrop" style="position:fixed;top:0;left:0;width:100%;height:100%;z-index:999;">Modal</div>
            <form action="/search" method="get" style="position:absolute;top:10px;left:10px;z-index:1;">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/submit-no-action-fail"):
            html = """<!doctype html><html><body>
            <h1>Submit No Action Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9005",
              "handle": "noaction-item",
              "title": "No Action Item",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form action="#" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit" id="sbtn">Search</button>
            </form>
            <script>
            document.getElementById('sbtn').onclick = (e) => { e.preventDefault(); };
            </script>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/server-error-fail"):
            html = """<!doctype html><html><body>
            <h1>Server Error Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9006",
              "handle": "error-item",
              "title": "Error Item",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form action="/search-500" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/search-500"):
            self.send_response(500)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"500 Internal Server Error")

        elif path.startswith("/products/alt-search-pass"):
            html = """<!doctype html><html><body>
            <h1>Alt Search Path Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9007",
              "handle": "snowboard-pass",
              "title": "Pass Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <button aria-label="Search" id="toggle-btn">Search Icon</button>
            <div id="search-modal" style="display:none;">
              <form action="/search" method="get">
                <input type="search" name="q" placeholder="Search products...">
                <button type="submit">Search</button>
              </form>
            </div>
            <script>
            document.getElementById('toggle-btn').onclick = () => {
              document.getElementById('search-modal').style.display = 'block';
            };
            </script>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/handle-substring-board-other-fail"):
            html = """<!doctype html><html><body>
            <h1>Substring Handle Fail Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9008",
              "handle": "board",
              "title": "Board",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "board"
            }
            </script>
            <form action="/search-substring" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/search-substring"):
            html = """<!doctype html><html><body>
            <h1>Search Results</h1>
            <div id="search-results">
              <div class="product-card">
                <a href="/products/board-other">Board Other Product</a>
              </div>
            </div>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/board-other"):
            html = """<!doctype html><html><body>
            <h1>Board Other PDP</h1>
            <p>Product ID: 9998</p>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/collection-wrong-dest-fail"):
            html = """<!doctype html><html><body>
            <h1>Collection Wrong Dest Fail Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9010",
              "handle": "snowboard-pass",
              "title": "Pass Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form action="/search-coll-wrong" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/search-coll-wrong"):
            html = """<!doctype html><html><body>
            <h1>Search Results</h1>
            <div id="search-results">
              <div class="product-card">
                <a href="/products/snowboard-pass">Pass Snowboard Link</a>
              </div>
              <div class="collection-grid">
                <a href="/products/board-other">Collection Item</a>
              </div>
            </div>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/collection-journey-pass"):
            html = """<!doctype html><html><body>
            <h1>Collection Journey Pass Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9009",
              "handle": "snowboard-pass",
              "title": "Pass Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form action="/search-coll" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/search-coll"):
            html = """<!doctype html><html><body>
            <h1>Search Results</h1>
            <div id="search-results">
              <div class="product-card">
                <a href="/products/snowboard-pass">Pass Snowboard</a>
              </div>
            </div>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/disappearing-fault-warn"):
            ComprehensiveMockSearchServer.flaky_count[0] += 1
            if ComprehensiveMockSearchServer.flaky_count[0] == 1:
                html = """<!doctype html><html><body>
                <h1>Disappearing Fault Demo Run 1</h1>
                <script data-catalog-fixture type="application/json">
                {
                  "product_id": "9011",
                  "handle": "flaky-item",
                  "title": "Flaky Item",
                  "published_status": true,
                  "eligible_for_search": true,
                  "expected_query": "snowboard"
                }
                </script>
                <form action="/search" method="get">
                  <input type="search" name="q" placeholder="Search products...">
                  <button type="submit" disabled>Search</button>
                </form>
                </body></html>"""
            else:
                html = """<!doctype html><html><body>
                <h1>Disappearing Fault Demo Run 2</h1>
                <script data-catalog-fixture type="application/json">
                {
                  "product_id": "9011",
                  "handle": "snowboard-pass",
                  "title": "Pass Snowboard",
                  "published_status": true,
                  "eligible_for_search": true,
                  "expected_query": "snowboard"
                }
                </script>
                <form action="/search" method="get">
                  <input type="search" name="q" placeholder="Search products...">
                  <button type="submit">Search</button>
                </form>
                </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/differing-fault-warn"):
            ComprehensiveMockSearchServer.differing_count[0] += 1
            if ComprehensiveMockSearchServer.differing_count[0] == 1:
                html = """<!doctype html><html><body>
                <h1>Differing Fault Demo Run 1</h1>
                <script data-catalog-fixture type="application/json">
                {
                  "product_id": "9012",
                  "handle": "broken-item",
                  "title": "Broken Item",
                  "published_status": true,
                  "eligible_for_search": true,
                  "expected_query": "snowboard"
                }
                </script>
                <form action="/search" method="get">
                  <input type="search" name="q" placeholder="Search products...">
                  <button type="submit" disabled>Search</button>
                </form>
                </body></html>"""
            else:
                html = """<!doctype html><html><body>
                <h1>Differing Fault Demo Run 2</h1>
                <script data-catalog-fixture type="application/json">
                {
                  "product_id": "9012",
                  "handle": "broken-item",
                  "title": "Broken Item",
                  "published_status": true,
                  "eligible_for_search": true,
                  "expected_query": "snowboard"
                }
                </script>
                <form action="/search-broken" method="get">
                  <input type="search" name="q" placeholder="Search products...">
                  <button type="submit">Search</button>
                </form>
                </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/search-broken"):
            html = """<!doctype html><html><body>
            <h1>Search Results</h1>
            <div id="search-results">
              <div class="product-card">
                <a href="/products/coll-broken-404">Broken Item</a>
              </div>
            </div>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/search"):
            html = """<!doctype html><html><body>
            <h1>Search Results</h1>
            <div id="search-results">
              <div class="product-card">
                <a href="/products/snowboard-pass">Pass Snowboard</a>
              </div>
            </div>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/snowboard-pass"):
            html = """<!doctype html><html><body>
            <h1>Pass Snowboard PDP</h1>
            <p>Product ID: 9001</p>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def mock_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ComprehensiveMockSearchServer)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_skill_excluded_from_default_skills():
    auditor = ShopifySearchDiscoveryAuditor()
    assert auditor.name() == "shopify_search_discovery_auditor"
    assert "shopify_search_discovery_auditor" not in DEFAULT_SKILLS


@pytest.mark.asyncio
async def test_missing_catalog_fixture_returns_warn(mock_server):
    url = f"{mock_server}/products/missing-fixture-warn"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reason_code"] == "MISSING_CATALOG_FIXTURE"
        await browser.close()


@pytest.mark.asyncio
async def test_ineligible_product_disabled_button_returns_warn_not_fail(mock_server):
    url = f"{mock_server}/products/ineligible-disabled-button-warn"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reason_code"] == "PRODUCT_NOT_ELIGIBLE_FOR_QUERY"
        assert result.details.get("lead_candidate") is not True
        await browser.close()


@pytest.mark.asyncio
async def test_disabled_search_button_returns_fail_and_lead(mock_server):
    url = f"{mock_server}/products/disabled-button-fail"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["reason_code"] == "SEARCH_BUTTON_DISABLED"
        assert result.details.get("lead_candidate") is True
        await browser.close()


@pytest.mark.asyncio
async def test_overlay_blocked_search_returns_fail(mock_server):
    url = f"{mock_server}/products/overlay-blocked-fail"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["reason_code"] == "SEARCH_INPUT_OVERLAY_BLOCKED"
        await browser.close()


@pytest.mark.asyncio
async def test_submit_no_action_returns_fail(mock_server):
    url = f"{mock_server}/products/submit-no-action-fail"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["reason_code"] == "SEARCH_SUBMIT_NO_ACTION"
        await browser.close()


@pytest.mark.asyncio
async def test_preexisting_results_submit_no_action_fail(mock_server):
    """Pre-existing results container without DOM update on submit MUST return FAIL (SEARCH_SUBMIT_NO_ACTION)."""
    url = f"{mock_server}/products/preexisting-results-no-action-fail"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["reason_code"] == "SEARCH_SUBMIT_NO_ACTION"
        await browser.close()


@pytest.mark.asyncio
async def test_server_error_response_returns_fail(mock_server):
    url = f"{mock_server}/products/server-error-fail"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["reason_code"] == "SEARCH_RESPONSE_ERROR"
        await browser.close()


@pytest.mark.asyncio
async def test_alt_search_trigger_path_pass(mock_server):
    url = f"{mock_server}/products/alt-search-pass"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        await browser.close()


@pytest.mark.asyncio
async def test_handle_substring_board_other_fail(mock_server):
    """Handle 'board' must NOT match 'board-other' -> FAIL (WRONG_PRODUCT_DESTINATION)."""
    url = f"{mock_server}/products/handle-substring-board-other-fail"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["reason_code"] == "WRONG_PRODUCT_DESTINATION"
        await browser.close()


@pytest.mark.asyncio
async def test_collection_product_link_pass(mock_server):
    url = f"{mock_server}/products/collection-journey-pass"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["checks"]["collection_links"]["status"] == "NOT_APPLICABLE"
        await browser.close()


@pytest.mark.asyncio
async def test_collection_product_wrong_dest_fail(mock_server):
    url = f"{mock_server}/products/collection-wrong-dest-fail"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["checks"]["collection_links"]["status"] == "FAIL"
        assert result.details["checks"]["collection_links"]["reason_code"] == "WRONG_COLLECTION_PRODUCT_DESTINATION"
        await browser.close()


@pytest.mark.asyncio
async def test_disappearing_fault_second_pass_warn(mock_server):
    url = f"{mock_server}/products/disappearing-fault-warn"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reproduction_summary"]["confirmed"] is False
        await browser.close()


@pytest.mark.asyncio
async def test_differing_fault_second_pass_warn(mock_server):
    url = f"{mock_server}/products/differing-fault-warn"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reproduction_summary"]["confirmed"] is False
        await browser.close()
