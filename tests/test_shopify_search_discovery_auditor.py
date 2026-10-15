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
    protocol_version = "HTTP/1.0"
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

        elif path.startswith("/products/html-change-without-dom-results-warn"):
            html = """<!doctype html><html><body>
            <div class="search-results" id="sr-box">Initial container text</div>
            <form action="/search-no-matching-container" method="get">
              <input type="search" name="q" placeholder="Search...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/search-no-matching-container"):
            html = """<!doctype html><html><body>
            <div class="search-results" id="sr-box">
              Updated container text that is much longer than fifty characters in length but does not contain matching query terms or products
            </div>
            <footer>
              <a href="/products/unrelated-footer-item">Footer Product Link</a>
            </footer>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/navigation-without-results-container-warn"):
            html = """<!doctype html><html><body>
            <header>
              <nav><a href="/products/header-item">Header Product Item</a></nav>
            </header>
            <h1>Page Title</h1>
            <form action="/search-no-results-container" method="get">
              <input type="search" name="q" placeholder="Search...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/search-no-results-container"):
            html = """<!doctype html><html><body>
            <header>
              <nav><a href="/products/header-item">Header Product Item</a></nav>
            </header>
            <h1>Search Page Header</h1>
            <p>Welcome to search page without search results section</p>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/sauceshop-desktop-trigger-pass"):
            html = """<!doctype html><html><body>
            <h1>Sauceshop Desktop Span Trigger Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9016",
              "handle": "snowboard-pass",
              "title": "Pass Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <div class="flex flex-row gap-x-4 items-center">
              <span class="p-2 navbar__desktop--menu__search-button" id="sauce-search-btn">
                <svg width="20" height="21"><path d="M0 0h20v20H0z"/></svg>
              </span>
            </div>
            <div class="navbar__desktop--menu__search-form" id="sauce-form" style="display:none;">
              <form action="/search" method="get">
                <input type="text" name="q" placeholder="Search for sauces" class="search-box">
                <button type="submit">Search</button>
              </form>
            </div>
            <script>
            document.getElementById('sauce-search-btn').onclick = () => {
              document.getElementById('sauce-form').style.display = 'block';
            };
            </script>
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

        elif path.startswith("/products/delayed-results-pass"):
            html = """<!doctype html><html><body>
            <h1>Delayed Results Product Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9020",
              "handle": "delayed-snowboard",
              "title": "Delayed Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form action="/search-delayed" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/search-delayed"):
            html = """<!doctype html><html><body>
            <h1>Search Results</h1>
            <div id="results-wrapper">Searching...</div>
            <script>
            setTimeout(() => {
              document.getElementById("results-wrapper").innerHTML = `
                <div id="search-results">
                  <div class="product-card">
                    <a href="/products/delayed-snowboard">Delayed Snowboard</a>
                  </div>
                </div>
              `;
            }, 1000);
            </script>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/delayed-snowboard"):
            html = """<!doctype html><html><body>
            <h1>Delayed Snowboard PDP</h1>
            <p>Product ID: 9020</p>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/mismatch-destination-fail"):
            html = """<!doctype html><html><body>
            <h1>Mismatch Destination Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9021",
              "handle": "expected-snowboard",
              "title": "Expected Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form action="/search-mismatch" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/search-mismatch"):
            html = """<!doctype html><html><body>
            <h1>Search Results</h1>
            <div id="search-results">
              <div class="product-card">
                <a href="/products/wrong-product">Wrong Product</a>
              </div>
            </div>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/wrong-product"):
            html = """<!doctype html><html><body>
            <h1>Wrong Product PDP</h1>
            <p>Product ID: 9999</p>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/roka-custom-card-pass"):
            html = """<!doctype html><html><body>
            <h1>Roka Custom Web Component Search Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9030",
              "handle": "canfield-roll-top-backpack",
              "title": "Canfield Backpack",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "bag"
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

        elif path.startswith("/products/empty-drawer-products-warn"):
            html = """<!doctype html><html><body>
            <h1>Empty Search Drawer Products Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9031",
              "handle": "some-suggested-item",
              "title": "Some Suggested Item",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "bag"
            }
            </script>
            <form action="/search-empty-drawer" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/search-empty-drawer"):
            html = """<!doctype html><html><body>
            <div class="search-drawer">
              <div class="search-drawer__suggestions">
                <p>Trending Search Suggestions</p>
                <a href="/products/some-suggested-item">Suggested Item Link</a>
              </div>
              <div class="search-drawer__products" role="list">
                <!-- Empty product results container -->
              </div>
            </div>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/search") and "q=bag" in path:
            html = """<!doctype html><html><body>
            <h1>Search Drawer Products</h1>
            <div class="search-drawer__products" role="list">
              <div class="search-drawer__product" role="listitem">
                <product-card class="product-card">
                  <div class="product-card__content">
                    <a class="product-card__overlay-link" href="/products/canfield-roll-top-backpack">Canfield Backpack</a>
                  </div>
                </product-card>
              </div>
            </div>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/canfield-roll-top-backpack"):
            html = """<!doctype html><html><body>
            <h1>Canfield Backpack PDP</h1>
            <p>Product ID: 9030</p>
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

        elif path.startswith("/products/hidden-search-trigger-pass"):
            html = """<!doctype html><html><body>
            <h1>Hidden Search Input Trigger Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9014",
              "handle": "snowboard-pass",
              "title": "Pass Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <button aria-label="Search Trigger" id="open-btn">Search Icon</button>
            <div id="drawer" style="display:none;">
              <form action="/search" method="get">
                <input type="search" name="q" placeholder="Search products...">
                <button type="submit">Search</button>
              </form>
            </div>
            <script>
            document.getElementById('open-btn').onclick = () => {
              document.getElementById('drawer').style.display = 'block';
            };
            </script>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

        elif path.startswith("/products/unresolved-search-trigger-warn"):
            html = """<!doctype html><html><body>
            <h1>Unresolved Search Trigger Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9015",
              "handle": "snowboard-pass",
              "title": "Pass Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <div>No search form or trigger element here</div>
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
    try:
        server.shutdown()
    except Exception:
        pass
    try:
        server.server_close()
    except Exception:
        pass


def test_skill_excluded_from_default_skills():
    auditor = ShopifySearchDiscoveryAuditor()
    assert auditor.name() == "shopify_search_discovery_auditor"
    assert "shopify_search_discovery_auditor" not in DEFAULT_SKILLS


@pytest.mark.asyncio
async def test_allow_live_audit_mode_allows_search_navigation(mock_server):
    """Verify allow_live_audit=True permits search navigation while aborting mutation endpoints."""
    url = f"{mock_server}/products/search-pass"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        context = {"page": page, "base_url": url, "allow_live_audit": True}
        result = await auditor.run(context)

        assert result.status == "PASS"
        await browser.close()


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
        assert result.details["checks"]["catalog_fixture"]["status"] == "WARN"
        assert result.details["checks"]["product_eligibility"]["status"] == "WARN"
        assert result.details["checks"]["product_eligibility"]["reason_code"] == "PRODUCT_ELIGIBILITY_UNKNOWN"
        assert result.details.get("lead_candidate") is not True
        await browser.close()


@pytest.mark.asyncio
async def test_sauceshop_results_dom_structure(mock_server):
    """Search navigation that lands on a results page containing product links MUST pass UI search execution cleanly."""
    url = f"{mock_server}/products/sauceshop-desktop-trigger-pass"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.details["checks"]["search_ui_execution"]["status"] == "PASS"
        assert result.details["checks"]["search_ui_execution"]["reason_code"] == "NONE"
        await browser.close()


@pytest.mark.asyncio
async def test_navigation_without_search_results_container_warn(mock_server):
    """Navigation to search URL with header links but missing dedicated search results section MUST return WARN SEARCH_RESULTS_UNRESOLVED, not PASS."""
    url = f"{mock_server}/products/navigation-without-results-container-warn"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url, "search_query": "buffalo"})

        assert result.details["checks"]["search_ui_execution"]["status"] == "WARN"
        assert result.details["checks"]["search_ui_execution"]["reason_code"] == "SEARCH_RESULTS_UNRESOLVED"
        assert result.details.get("lead_candidate") is not True
        await browser.close()


@pytest.mark.asyncio
async def test_html_changed_without_dom_results_returns_warn(mock_server):
    """HTML changed by >50 chars with unrelated product links outside results container, but has_dom_results=False MUST return WARN SEARCH_RESULTS_UNRESOLVED, not PASS."""
    url = f"{mock_server}/products/html-change-without-dom-results-warn"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url, "search_query": "buffalo"})

        assert result.details["checks"]["search_ui_execution"]["status"] == "WARN"
        assert result.details["checks"]["search_ui_execution"]["reason_code"] == "SEARCH_RESULTS_UNRESOLVED"
        assert result.details.get("lead_candidate") is not True
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
async def test_roka_custom_product_card_container_pass(mock_server):
    """Roka London custom web component <product-card> and .search-drawer__products container MUST pass UI search execution."""
    url = f"{mock_server}/products/roka-custom-card-pass"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        # Explicit query in context matching DOM catalog fixture
        result = await auditor.run({"page": page, "base_url": url, "search_query": "bag"})

        assert result.status == "PASS"
        assert result.details["checks"]["search_ui_execution"]["status"] == "PASS"
        assert result.details["checks"]["discovery_links"]["status"] == "PASS"
        await browser.close()


@pytest.mark.asyncio
async def test_empty_search_drawer_products_returns_warn_not_pass(mock_server):
    """Search drawer with suggestions and product links outside empty .search-drawer__products MUST return WARN, not PASS."""
    url = f"{mock_server}/products/empty-drawer-products-warn"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url, "search_query": "bag"})

        assert result.details["checks"]["search_ui_execution"]["status"] == "WARN"
        assert result.details["checks"]["search_ui_execution"]["reason_code"] == "SEARCH_RESULTS_UNRESOLVED"
        assert result.status == "WARN"
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
    ComprehensiveMockSearchServer.flaky_count[0] = 0
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
async def test_hidden_search_input_trigger_pass(mock_server):
    """Hidden search input field revealed by clicking UI trigger button MUST complete journey as PASS."""
    url = f"{mock_server}/products/hidden-search-trigger-pass"
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
async def test_unresolved_search_trigger_returns_warn(mock_server):
    """Unable to detect or open search trigger element MUST return WARN with SEARCH_TRIGGER_UNRESOLVED and lead_candidate != True."""
    url = f"{mock_server}/products/unresolved-search-trigger-warn"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reason_code"] == "SEARCH_TRIGGER_UNRESOLVED"
        assert result.details.get("lead_candidate") is not True
        await browser.close()


@pytest.mark.asyncio
async def test_sauceshop_desktop_span_search_trigger_pass(mock_server):
    """Desktop span search trigger pattern (span.navbar__desktop--menu__search-button) MUST open search input and pass journey."""
    url = f"{mock_server}/products/sauceshop-desktop-trigger-pass"
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
async def test_delayed_results_within_timeout_pass(mock_server):
    """Delayed search results rendered asynchronously within timeout MUST pass UI search execution cleanly."""
    url = f"{mock_server}/products/delayed-results-pass"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifySearchDiscoveryAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["checks"]["search_ui_execution"]["status"] == "PASS"
        await browser.close()


@pytest.mark.asyncio
async def test_mismatch_destination_product_fail(mock_server):
    """Result link opening mismatched product PDP handle MUST return FAIL (WRONG_PRODUCT_DESTINATION)."""
    url = f"{mock_server}/products/mismatch-destination-fail"
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
async def test_differing_fault_second_pass_warn(mock_server):
    ComprehensiveMockSearchServer.differing_count[0] = 0
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
