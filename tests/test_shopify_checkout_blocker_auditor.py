"""Tests for Shopify Checkout Blocker Auditor Skill (Phase 6).

Verifies cart readiness, cart-to-checkout UI transition, local checkout steps, shipping loading,
mandatory payment security guardrails, non-mutative PII masking, local 2-pass reproduction, and CLI exclusion.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from playwright.async_api import async_playwright

from nano_sre.skills.shopify_checkout_blocker_auditor import ShopifyCheckoutBlockerAuditor, PaymentGuardrailException
from nano_sre.cli import DEFAULT_SKILLS


@pytest.fixture(scope="module")
def checkout_cart_store():
    carts = {}
    order_endpoints_hit = []

    class CheckoutStoreHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path

            if path == "/cart.js":
                cookie_hdr = self.headers.get("Cookie", "")
                session_id = "default"
                if "cart_session=" in cookie_hdr:
                    session_id = cookie_hdr.split("cart_session=")[1].split(";")[0]
                c = carts.get(session_id, [])
                self.send(200, json.dumps({"item_count": sum(i["quantity"] for i in c), "currency": "USD", "items": c}), "application/json")

            elif path.startswith("/checkout/working"):
                html = """<!doctype html><html><body>
                <div id="checkout">
                  <h1>Checkout</h1>
                  <input type="email" name="email" value="">
                  <input type="text" name="first_name" value="">
                  <input type="text" name="last_name" value="">
                  <input type="text" name="address1" value="">
                  <input type="text" name="city" value="">
                  <input type="text" name="zip" value="">
                  <div id="payment-methods">Payment Options</div>
                </div></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/checkout/spinner-hang"):
                html = """<!doctype html><html><body>
                <div id="checkout">
                  <h1>Checkout</h1>
                  <div class="loading-spinner" data-shipping-loading>Loading shipping...</div>
                </div></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/cart-working"):
                html = """<!doctype html><html><body>
                <form action="/checkout" method="post">
                  <button type="submit" name="checkout">Checkout</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = (e) => {
                  e.preventDefault();
                  window.location.href = '/checkout/working';
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/cart-empty"):
                html = """<!doctype html><html><body><div>Cart Empty</div></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/cart-no-btn"):
                html = """<!doctype html><html><body><div>Cart with no checkout button</div></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/cart-disabled-btn"):
                html = """<!doctype html><html><body>
                <button name="checkout" disabled>Checkout</button>
                </body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/cart-overlay-blocked"):
                html = """<!doctype html><html><body>
                <div class="modal-backdrop" style="position:fixed;top:0;left:0;width:100%;height:100%;z-index:999;">Modal</div>
                <button name="checkout" style="position:absolute;top:10px;left:10px;z-index:1;">Checkout</button>
                </body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/cart-click-no-action"):
                html = """<!doctype html><html><body>
                <button name="checkout">Checkout</button>
                <script>
                document.querySelector('button').onclick = (e) => { e.preventDefault(); };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/cart-redirect-loop"):
                html = """<!doctype html><html><body>
                <button name="checkout">Checkout</button>
                <script>
                document.querySelector('button').onclick = (e) => {
                  e.preventDefault();
                  window.location.href = '/cart-redirect-loop';
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/cart-error-page"):
                html = """<!doctype html><html><body>
                <button name="checkout">Checkout</button>
                <script>
                document.querySelector('button').onclick = (e) => {
                  e.preventDefault();
                  document.body.innerHTML = '500 Internal Server Error';
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/checkout/spinner-page"):
                html = """<!doctype html><html><body>
                <form action="/checkout" method="post">
                  <button type="submit" name="checkout">Checkout</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = (e) => {
                  e.preventDefault();
                  window.location.href = '/checkout/spinner-hang';
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            else:
                self.send(404, "Not found", "text/plain")

        def do_POST(self):
            if "/checkout/pay" in self.path or "/orders/create" in self.path:
                order_endpoints_hit.append(self.path)
                self.send(200, json.dumps({"order_id": 999}), "application/json")
                return

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
                headers = [("Set-Cookie", f"cart_session={session_id}; Path=/")]
                carts[session_id].append({"variant_id": 1001, "product_id": 100, "quantity": 1, "price": 2500, "final_price": 2500})
                self.send_with_headers(200, json.dumps({"id": 1001, "quantity": 1}), "application/json", headers)
            else:
                self.send(404, "Not found", "text/plain")

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

    server = ThreadingHTTPServer(("127.0.0.1", 0), CheckoutStoreHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{server.server_port}", order_endpoints_hit

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.mark.asyncio
async def test_working_checkout_journey_pass(checkout_cart_store):
    """Cart with items transitions to local checkout UI up to payment step access -> PASS."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        # Add item to cart session first
        await page.goto(f"{store_url}/cart-working")
        await page.evaluate("""async () => {
            await fetch('/cart/add.js', {method: 'POST', body: 'id=1001&quantity=1'});
        }""")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-working"})

        assert result.status == "PASS"
        assert result.details["checks"]["cart_readiness"]["status"] == "PASS"
        assert result.details["checks"]["cart_to_checkout"]["status"] == "PASS"
        assert result.details["checks"]["payment_step_access"]["status"] == "PASS"
        await browser.close()


@pytest.mark.asyncio
async def test_empty_cart_readiness_warn(checkout_cart_store):
    """Empty cart returns WARN for cart_readiness without inferring checkout blocker."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(f"{store_url}/cart-empty")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-empty"})

        assert result.status == "WARN"
        assert result.details["checks"]["cart_readiness"]["status"] == "WARN"
        assert result.details["reason_code"] == "CART_NOT_READY"
        await browser.close()


@pytest.mark.asyncio
async def test_missing_checkout_button_fail(checkout_cart_store):
    """Cart has items but checkout button is missing -> FAIL (BUTTON_MISSING)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-no-btn")
        await page.evaluate("""async () => {
            await fetch('/cart/add.js', {method: 'POST', body: 'id=1001&quantity=1'});
        }""")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-no-btn"})

        assert result.status == "FAIL"
        assert result.details["checks"]["cart_to_checkout"]["status"] == "FAIL"
        assert result.details["checks"]["cart_to_checkout"]["reason_code"] == "BUTTON_MISSING"
        await browser.close()


@pytest.mark.asyncio
async def test_disabled_checkout_button_fail(checkout_cart_store):
    """Checkout button is present but disabled -> FAIL (BUTTON_DISABLED)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-disabled-btn")
        await page.evaluate("""async () => {
            await fetch('/cart/add.js', {method: 'POST', body: 'id=1001&quantity=1'});
        }""")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-disabled-btn"})

        assert result.status == "FAIL"
        assert result.details["checks"]["cart_to_checkout"]["reason_code"] == "BUTTON_DISABLED"
        await browser.close()


@pytest.mark.asyncio
async def test_overlay_blocked_button_fail(checkout_cart_store):
    """Checkout button obscured by overlay -> FAIL (BUTTON_OVERLAY_BLOCKED)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-overlay-blocked")
        await page.evaluate("""async () => {
            await fetch('/cart/add.js', {method: 'POST', body: 'id=1001&quantity=1'});
        }""")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-overlay-blocked"})

        assert result.status == "FAIL"
        assert result.details["checks"]["cart_to_checkout"]["reason_code"] == "BUTTON_OVERLAY_BLOCKED"
        await browser.close()


@pytest.mark.asyncio
async def test_click_no_action_fail(checkout_cart_store):
    """Clicking checkout button produces no network request or navigation -> FAIL (CLICK_NO_ACTION)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-click-no-action")
        await page.evaluate("""async () => {
            await fetch('/cart/add.js', {method: 'POST', body: 'id=1001&quantity=1'});
        }""")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-click-no-action"})

        assert result.status == "FAIL"
        assert result.details["checks"]["cart_to_checkout"]["reason_code"] == "CLICK_NO_ACTION"
        await browser.close()


@pytest.mark.asyncio
async def test_cart_redirect_loop_fail(checkout_cart_store):
    """Clicking checkout re-renders cart page -> FAIL (CHECKOUT_REDIRECT_CART)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-redirect-loop")
        await page.evaluate("""async () => {
            await fetch('/cart/add.js', {method: 'POST', body: 'id=1001&quantity=1'});
        }""")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-redirect-loop"})

        assert result.status == "FAIL"
        assert result.details["checks"]["cart_to_checkout"]["reason_code"] == "CHECKOUT_REDIRECT_CART"
        await browser.close()


@pytest.mark.asyncio
async def test_shipping_spinner_timeout_fail(checkout_cart_store):
    """Shipping options loading spinner hangs indefinitely -> FAIL (SHIPPING_SPINNER_TIMEOUT)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/checkout/spinner-page")
        await page.evaluate("""async () => {
            await fetch('/cart/add.js', {method: 'POST', body: 'id=1001&quantity=1'});
        }""")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/checkout/spinner-page"})

        assert result.status == "FAIL"
        assert result.details["checks"]["shipping"]["status"] == "FAIL"
        assert result.details["checks"]["shipping"]["reason_code"] == "SHIPPING_SPINNER_TIMEOUT"
        await browser.close()


@pytest.mark.asyncio
async def test_mandatory_payment_security_guardrail(checkout_cart_store):
    """Verify that order completion endpoints (/checkout/pay, /orders/create) were NEVER called during audit."""
    store_url, order_endpoints_hit = checkout_cart_store
    order_endpoints_hit.clear()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-working")
        await page.evaluate("""async () => {
            await fetch('/cart/add.js', {method: 'POST', body: 'id=1001&quantity=1'});
        }""")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-working"})

        assert result.status == "PASS"
        assert len(order_endpoints_hit) == 0, f"SECURITY VIOLATION: Order pay endpoint was called! {order_endpoints_hit}"
        assert result.details["order_pay_guardrail_verified"] is True
        await browser.close()


@pytest.mark.asyncio
async def test_non_mutative_pii_masking_restoration():
    """Verify CSS PII masking does not mutate input or textarea values in checkout form."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.set_content("""
        <!doctype html><html><body>
        <div id="checkout">
          <input type="text" name="address1" value="123 Confidential St">
        </div>
        </body></html>
        """)

        auditor = ShopifyCheckoutBlockerAuditor()
        screenshot_path = await auditor._take_screenshot(page, "pii_checkout_check")

        val_after = await page.evaluate("() => document.querySelector('input[name=\"address1\"]').value")
        assert val_after == "123 Confidential St", f"Input value mutated! Got: {val_after}"

        has_orig_attr = await page.evaluate("() => document.querySelector('[data-orig-bg]') !== null")
        assert has_orig_attr is False, "Temporary dataset attributes were not restored in finally!"

        await browser.close()


def test_skill_excluded_from_default_skills():
    """Verify shopify_checkout_blocker_auditor is excluded from CLI DEFAULT_SKILLS."""
    assert "shopify_checkout_blocker_auditor" not in DEFAULT_SKILLS
