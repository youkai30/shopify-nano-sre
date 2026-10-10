"""Tests for Shopify Mobile Purchase Blocker Auditor Skill (Phase 4).

Tests real Chromium mobile context emulation, option selection proof, ATC overlay blocker detection,
cart evidence verification, desktop comparison, local reproduction, blocker identity matching, and PII masking.
"""

import asyncio
import json
import pytest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from playwright.async_api import async_playwright

from nano_sre.skills.shopify_mobile_purchase_blocker_auditor import ShopifyMobilePurchaseBlockerAuditor
from nano_sre.cli import DEFAULT_SKILLS


@pytest.fixture(scope="module")
def local_mobile_store():
    # Store session cart per session_id cookie
    carts = {}

    class MobileStoreHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path

            if path in [
                "/products/healthy.js",
                "/products/offscreen-atc.js",
                "/products/dismissable-modal.js",
                "/products/alt-sticky-cta.js",
                "/products/delayed-drawer.js",
                "/products/caller-unaffected.js",
                "/products/touch-required.js",
                "/products/transient-issue.js",
                "/products/narrow-overlay.js",
                "/products/desktop-warn.js",
                "/products/diff-overlay.js",
                "/products/diff-repro.js",
                "/products/session-test.js",
                "/products/diff-variant.js",
                "/products/diff-options.js"
            ]:
                data = {
                    "id": 1,
                    "handle": path.split("/")[2].replace(".js", ""),
                    "options": [],
                    "variants": [
                        {"id": 101, "title": "Default", "price": 1000, "available": True, "options": []}
                    ]
                }
                self.send(200, json.dumps(data), "application/json")

            elif path == "/products/sticky-overlay.js":
                data = {
                    "id": 2,
                    "handle": "sticky-overlay",
                    "options": [],
                    "variants": [
                        {"id": 201, "title": "Default", "price": 1000, "available": True, "options": []}
                    ]
                }
                self.send(200, json.dumps(data), "application/json")

            elif path == "/products/unselectable-option.js":
                data = {
                    "id": 3,
                    "handle": "unselectable-option",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 301, "title": "Small", "price": 1000, "available": True, "options": ["Small"]}
                    ]
                }
                self.send(200, json.dumps(data), "application/json")

            elif path == "/products/swatch-no-state.js":
                data = {
                    "id": 35,
                    "handle": "swatch-no-state",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 351, "title": "Small", "price": 1000, "available": True, "options": ["Small"]}
                    ]
                }
                self.send(200, json.dumps(data), "application/json")

            elif path == "/products/customization-required.js":
                data = {
                    "id": 4,
                    "handle": "customization-required",
                    "options": [],
                    "variants": [
                        {"id": 401, "title": "Default", "price": 1000, "available": True, "options": []}
                    ]
                }
                self.send(200, json.dumps(data), "application/json")

            elif path == "/products/no-cart-change.js":
                data = {
                    "id": 5,
                    "handle": "no-cart-change",
                    "options": [],
                    "variants": [
                        {"id": 501, "title": "Default", "price": 1000, "available": True, "options": []}
                    ]
                }
                self.send(200, json.dumps(data), "application/json")

            elif path == "/products/analytics-error.js":
                data = {
                    "id": 6,
                    "handle": "analytics-error",
                    "options": [],
                    "variants": [
                        {"id": 601, "title": "Default", "price": 1000, "available": True, "options": []}
                    ]
                }
                self.send(200, json.dumps(data), "application/json")

            elif path == "/products/wrong-variant.js":
                data = {
                    "id": 7,
                    "handle": "wrong-variant",
                    "options": [],
                    "variants": [
                        {"id": 701, "title": "Target", "price": 1000, "available": True, "options": []},
                        {"id": 702, "title": "Wrong", "price": 1000, "available": True, "options": []}
                    ]
                }
                self.send(200, json.dumps(data), "application/json")

            elif path == "/cart.js":
                cookie_hdr = self.headers.get("Cookie", "")
                session_id = "default"
                if "cart_session=" in cookie_hdr:
                    session_id = cookie_hdr.split("cart_session=")[1].split(";")[0]
                c = carts.get(session_id, [])
                self.send(200, json.dumps({"item_count": sum(i["quantity"] for i in c), "items": c}), "application/json")

            elif path.startswith("/products/healthy") or path.startswith("/products/caller-unaffected") or path.startswith("/products/session-test"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=101'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/diff-overlay"):
                html = """<!doctype html><html><head>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>
                  @media (max-width: 767px) {
                    div#mobileBlock { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: red; z-index: 9999; }
                    div#desktopBlock { display: none; }
                  }
                  @media (min-width: 768px) {
                    div#desktopBlock { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: blue; z-index: 9999; }
                    div#mobileBlock { display: none; }
                  }
                </style>
                </head><body>
                <div id="mobileBlock">Mobile Block</div>
                <div id="desktopBlock">Desktop Block</div>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/touch-required"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <button type="submit" id="atc-btn" name="add">Add to cart</button>
                </form>
                <script>
                let touched = false;
                document.getElementById('atc-btn').addEventListener('touchstart', () => { touched = true; });
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  if (touched) {
                    await fetch('/cart/add.js', {method:'POST', body:'id=101'});
                  }
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/narrow-overlay"):
                html = """<!doctype html><html><head>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>
                  @media (max-width: 375px) {
                    .narrow-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: red; z-index: 9999; }
                  }
                  @media (min-width: 376px) {
                    .narrow-overlay { display: none; }
                  }
                </style>
                </head><body>
                <div class="narrow-overlay">Narrow Overlay</div>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=101'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/desktop-warn"):
                html = """<!doctype html><html><head>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>
                  @media (max-width: 767px) {
                    .mobile-sticky-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: red; z-index: 9999; }
                  }
                  @media (min-width: 768px) {
                    .mobile-sticky-overlay { display: none; }
                  }
                </style>
                </head><body>
                <div class="mobile-sticky-overlay">Overlay</div>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=601'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/offscreen-atc"):
                html = """<!doctype html><html><body>
                <div style="height: 1200px;">Long Content</div>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=101'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/sticky-overlay"):
                html = """<!doctype html><html><head>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>
                  @media (max-width: 767px) {
                    .mobile-sticky-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: red; z-index: 9999; }
                  }
                  @media (min-width: 768px) {
                    .mobile-sticky-overlay { display: none; }
                  }
                </style>
                </head><body>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="mobile-sticky-overlay">Blocking Overlay</div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=201'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/dismissable-modal"):
                html = """<!doctype html><html><body>
                <div id="popup" style="position:fixed;top:0;left:0;width:100%;height:100%;z-index:999;background:white;">
                  <button class="close" onclick="this.parentElement.style.display='none'">Close</button>
                </div>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=101'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/unselectable-option"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]" disabled>
                    <option value="Small" disabled>Small</option>
                  </select>
                  <button type="submit" name="add">Add to cart</button>
                </form></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/swatch-no-state"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <button type="button" class="swatch" data-value="Small">Small</button>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('.swatch').onclick = () => {};
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/alt-sticky-cta"):
                html = """<!doctype html><html><body>
                <div style="position:fixed;bottom:0;left:0;width:100%;height:50px;background:red;z-index:999;">Blocked Overlay</div>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add" style="position:fixed;bottom:5px;">Primary Add</button>
                </form>
                <div class="sticky-atc" style="position:fixed;top:10px;z-index:10000;">
                  <button type="button" onclick="fetch('/cart/add.js', {method:'POST', body:'id=101'})">Sticky CTA</button>
                </div></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/customization-required"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <input type="text" name="properties[Engraving]" required placeholder="Required engraving">
                  <button type="submit" name="add">Add to cart</button>
                </form></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/no-cart-change"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=501'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/analytics-error"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  console.error('Analytics tracking failed');
                  await fetch('/cart/add.js', {method:'POST', body:'id=601'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/wrong-variant"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=702'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/delayed-drawer"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div id="drawer" style="display:none;">Drawer</div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=101'});
                  setTimeout(() => { document.getElementById('drawer').style.display='block'; }, 4000);
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            else:
                self.send(404, "Not found", "text/plain")

        def do_POST(self):
            cookie_hdr = self.headers.get("Cookie", "")
            session_id = "default"
            if "cart_session=" in cookie_hdr:
                session_id = cookie_hdr.split("cart_session=")[1].split(";")[0]
            else:
                import uuid
                session_id = str(uuid.uuid4())

            if session_id not in carts:
                carts[session_id] = []

            if self.path == "/cart/add.js":
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else ""

                headers = [("Set-Cookie", f"cart_session={session_id}; Path=/")]
                if "id=101" in body:
                    carts[session_id].append({"variant_id": 101, "product_id": 1, "quantity": 1})
                    self.send_with_headers(200, json.dumps({"id": 101, "quantity": 1}), "application/json", headers)
                elif "id=201" in body:
                    carts[session_id].append({"variant_id": 201, "product_id": 2, "quantity": 1})
                    self.send_with_headers(200, json.dumps({"id": 201, "quantity": 1}), "application/json", headers)
                elif "id=501" in body:
                    self.send_with_headers(200, json.dumps({"id": 501}), "application/json", headers)
                elif "id=601" in body:
                    self.send_with_headers(500, json.dumps({"error": "server error"}), "application/json", headers)
                elif "id=702" in body:
                    carts[session_id].append({"variant_id": 702, "product_id": 7, "quantity": 1})
                    self.send_with_headers(200, json.dumps({"id": 702, "quantity": 1}), "application/json", headers)
                else:
                    self.send_with_headers(400, json.dumps({"error": "bad request"}), "application/json", headers)

        def send_with_headers(self, status, body, content_type, extra_headers=None):
            encoded = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            if extra_headers:
                for h, v in extra_headers:
                    self.send_header(h, v)
            self.end_headers()
            self.wfile.write(encoded)

        def send(self, status, body, content_type):
            encoded = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), MobileStoreHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{server.server_port}"

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_blocker_identity_mismatch_prevents_matching():
    """Unit test: _is_same_blocker_identity returns False if target_variant_id, handle, or options differ."""
    auditor = ShopifyMobilePurchaseBlockerAuditor()

    id1 = {
        "reason_code": "BUTTON_BLOCKED_BY_OVERLAY",
        "product_handle": "test-prod",
        "target_variant_id": 101,
        "proven_dom_options": {"Size": "Small"},
        "affected_control": "add_to_cart_button",
        "fault_descriptor": "div#overlay1",
    }

    # Differing target_variant_id
    id2 = dict(id1, target_variant_id=202)
    assert auditor._is_same_blocker_identity(id1, id2) is False

    # Differing product_handle
    id3 = dict(id1, product_handle="other-prod")
    assert auditor._is_same_blocker_identity(id1, id3) is False

    # Differing proven_dom_options
    id4 = dict(id1, proven_dom_options={"Size": "Large"})
    assert auditor._is_same_blocker_identity(id1, id4) is False

    # Differing fault_descriptor
    id5 = dict(id1, fault_descriptor="div#overlay2")
    assert auditor._is_same_blocker_identity(id1, id5) is False

    # Complete match
    id_same = dict(id1)
    assert auditor._is_same_blocker_identity(id1, id_same) is True


@pytest.mark.asyncio
async def test_differing_overlay_blockers_yields_comparison_unresolved(local_mobile_store):
    """Regression test: #mobileBlock on mobile vs #desktopBlock on desktop yields COMPARISON_UNRESOLVED (not SHARED)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/diff-overlay"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["desktop_comparison"]["classification"] == "COMPARISON_UNRESOLVED"
        assert "Shared issue" not in result.summary
        await browser.close()


@pytest.mark.asyncio
async def test_session_state_preservation(local_mobile_store):
    """Regression test: verify session cookies are passed to isolated auditing contexts without leakage."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/session-test"
        await page.goto(url)
        await ctx.add_cookies([{"name": "test_auth", "value": "secret_token_123", "domain": "127.0.0.1", "path": "/"}])

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        dumped = json.dumps(result.details)
        assert "secret_token_123" not in dumped, "Session cookie tokens leaked into result report details!"
        await browser.close()


@pytest.mark.asyncio
async def test_caller_desktop_page_remains_unaffected(local_mobile_store):
    """Regression check: verify that caller page and viewport remain completely unaffected."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 800}, is_mobile=False, has_touch=False)
        caller_page = await ctx.new_page()
        url = f"{local_mobile_store}/products/caller-unaffected"
        await caller_page.goto(url)

        initial_viewport = caller_page.viewport_size
        initial_is_mobile = await caller_page.evaluate("() => matchMedia('(max-width: 767px)').matches")

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": caller_page, "base_url": url})

        assert result.status == "PASS"
        assert caller_page.viewport_size == initial_viewport
        assert caller_page.viewport_size == {"width": 1280, "height": 800}
        assert (await caller_page.evaluate("() => matchMedia('(max-width: 767px)').matches")) == initial_is_mobile
        await browser.close()


@pytest.mark.asyncio
async def test_touch_interaction_required(local_mobile_store):
    """Regression check: verify that touch tap actions trigger touch events on mobile."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/touch-required"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["reason_code"] == "NONE"
        await browser.close()


@pytest.mark.asyncio
async def test_both_viewports_checked_narrow_overlay(local_mobile_store):
    """Regression check: blocker appears only on <=375px (360x800 fails, 390x844 passes) -> overall FAIL."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/narrow-overlay"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["reason_code"] == "BUTTON_BLOCKED_BY_OVERLAY"
        assert "390x844" in result.details["tested_viewports"]
        assert "360x800" in result.details["tested_viewports"]
        assert result.details["viewport_results"]["360x800"]["status"] == "FAIL"
        assert result.details["viewport_results"]["390x844"]["status"] == "PASS"
        await browser.close()


@pytest.mark.asyncio
async def test_desktop_warn_yields_comparison_unresolved(local_mobile_store):
    """Regression check: mobile FAIL + desktop WARN yields COMPARISON_UNRESOLVED (not SHARED)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/desktop-warn"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["desktop_comparison"]["classification"] == "COMPARISON_UNRESOLVED"
        assert result.details["desktop_comparison"]["desktop_status"] == "WARN"
        assert "reproduced on desktop" not in result.summary.lower()
        await browser.close()


@pytest.mark.asyncio
async def test_healthy_mobile_user_journey(local_mobile_store):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/healthy"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["reason_code"] == "NONE"
        assert "390x844" in result.details["tested_viewports"]
        assert "360x800" in result.details["tested_viewports"]
        assert result.details["network_cart_evidence"]["delta_quantity"] == 1
        await browser.close()


@pytest.mark.asyncio
async def test_offscreen_atc_button_reachable(local_mobile_store):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/offscreen-atc"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["reason_code"] == "NONE"
        await browser.close()


@pytest.mark.asyncio
async def test_sticky_overlay_blocks_atc_mobile_only(local_mobile_store):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/sticky-overlay"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["reason_code"] == "BUTTON_BLOCKED_BY_OVERLAY"
        assert result.details["desktop_comparison"]["classification"] == "MOBILE_SPECIFIC"
        assert result.details["reproduction_summary"]["reproduction_confirmed"] is True
        await browser.close()


@pytest.mark.asyncio
async def test_dismissable_modal_overlay(local_mobile_store):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/dismissable-modal"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["reason_code"] == "NONE"
        await browser.close()


@pytest.mark.asyncio
async def test_mandatory_option_unselectable(local_mobile_store):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/unselectable-option"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["reason_code"] == "MANDATORY_OPTION_UNSELECTABLE"
        await browser.close()


@pytest.mark.asyncio
async def test_swatch_no_state_change(local_mobile_store):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/swatch-no-state"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reason_code"] == "UNPROVEN_OPTION_PATTERN"
        await browser.close()


@pytest.mark.asyncio
async def test_valid_alt_sticky_cta_prevents_false_fail(local_mobile_store):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/alt-sticky-cta"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["reason_code"] == "NONE"
        await browser.close()


@pytest.mark.asyncio
async def test_http200_without_cart_change(local_mobile_store):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/no-cart-change"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reason_code"] == "NETWORK_OR_RESPONSE_UNRESOLVED"
        await browser.close()


@pytest.mark.asyncio
async def test_http500_with_analytics_error(local_mobile_store):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/analytics-error"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reason_code"] == "NETWORK_OR_RESPONSE_UNRESOLVED"
        await browser.close()


@pytest.mark.asyncio
async def test_unfulfilled_customization_field(local_mobile_store):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/customization-required"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reason_code"] == "UNFULFILLED_PURCHASE_REQUIREMENT"
        await browser.close()


@pytest.mark.asyncio
async def test_wrong_variant_added(local_mobile_store):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/wrong-variant"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["reason_code"] == "WRONG_VARIANT_ADDED"
        await browser.close()


@pytest.mark.asyncio
async def test_delayed_cart_ui_drawer(local_mobile_store):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        url = f"{local_mobile_store}/products/delayed-drawer"
        await page.goto(url)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["reason_code"] == "NONE"
        await browser.close()


@pytest.mark.asyncio
async def test_non_mutative_pii_masking_restoration(local_mobile_store):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = await ctx.new_page()

        await page.set_content("""
        <!doctype html><html><body>
        <form action="/cart/add" method="post">
          <input type="text" id="user-note" value="Secret User Note 123">
          <button type="submit" name="add">Add to cart</button>
        </form>
        </body></html>
        """)

        auditor = ShopifyMobilePurchaseBlockerAuditor()
        screenshot_path = await auditor._take_screenshot(page, "pii_test")

        val_after = await page.evaluate("() => document.getElementById('user-note').value")
        assert val_after == "Secret User Note 123", f"Input value was mutated during masking! Got: {val_after}"

        has_orig_attr = await page.evaluate("() => document.querySelector('[data-orig-bg]') !== null")
        assert has_orig_attr is False, "Temporary dataset attributes were not cleaned up in finally block!"

        await browser.close()


def test_skill_excluded_from_default_skills():
    assert "shopify_mobile_purchase_blocker_auditor" not in DEFAULT_SKILLS
