"""Tests for Shopify Checkout Blocker Auditor Skill (Phase 6).

Verifies cart readiness, cart-to-checkout UI transition, local checkout steps, shipping loading,
mandatory payment security guardrails, non-mutative PII masking, local 2-pass reproduction, and CLI exclusion.
"""

import asyncio
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import pytest
from playwright.async_api import async_playwright

from nano_sre.skills.shopify_checkout_blocker_auditor import ShopifyCheckoutBlockerAuditor, PaymentGuardrailException
from nano_sre.cli import DEFAULT_SKILLS


@pytest.fixture(scope="module")
def checkout_cart_store():
    carts = {}
    order_endpoints_hit = []
    flaky_count = [0]

    class CheckoutStoreHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path

            cookie_hdr = self.headers.get("Cookie", "")
            session_id = None
            is_new_session = False
            if "cart_session=" in cookie_hdr:
                session_id = cookie_hdr.split("cart_session=")[1].split(";")[0]

            if not session_id:
                session_id = str(uuid.uuid4())
                is_new_session = True

            extra_headers = [("Set-Cookie", f"cart_session={session_id}; Path=/")] if is_new_session else []

            if session_id not in carts:
                if path.startswith("/cart-empty"):
                    carts[session_id] = []
                else:
                    carts[session_id] = [{"variant_id": 1001, "product_id": 100, "quantity": 1, "price": 2500, "final_price": 2500, "key": "line_key_1001"}]

            if path == "/cart.js":
                c = carts.get(session_id, [])
                self.send_with_headers(200, json.dumps({"item_count": sum(i["quantity"] for i in c), "currency": "USD", "items": c}), "application/json", extra_headers)

            elif path.startswith("/checkout/working"):
                html = """<!doctype html><html><body>
                <div id="checkout">
                  <h1>Checkout</h1>
                  <input type="email" name="email" value="user@example.test">
                  <input type="text" name="first_name" value="Jane">
                  <input type="text" name="last_name" value="Doe">
                  <input type="text" name="address1" value="123 Synthetic Way">
                  <input type="text" name="city" value="Metropolis">
                  <input type="text" name="zip" value="10001">
                  <button type="button" data-continue-to-shipping id="cont-btn">Continue to shipping</button>
                  <div id="shipping-section" style="display:none;">
                    <div class="shipping-option">Standard Ground Shipping - $5.00</div>
                    <div id="payment-methods">Payment Options</div>
                  </div>
                </div>
                <script>
                document.getElementById('cont-btn').onclick = () => {
                  document.getElementById('shipping-section').style.display = 'block';
                };
                </script></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/checkout/preexisting-payment-fail"):
                html = """<!doctype html><html><body>
                <div id="checkout">
                  <h1>Checkout</h1>
                  <input type="email" name="email" value="user@example.test">
                  <input type="text" name="address1" value="123 Synthetic Way">
                  <input type="text" name="zip" value="10001">
                  <div id="payment-methods">Pre-existing Payment Options</div>
                </div></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/checkout/place-order-form"):
                html = """<!doctype html><html><body>
                <div id="checkout">
                  <h1>Single Page Checkout</h1>
                  <input type="email" name="email" value="user@example.test">
                  <input type="text" name="address1" value="123 Synthetic Way">
                  <input type="text" name="zip" value="10001">
                  <button type="submit" id="place-order-btn">Place order</button>
                </div>
                <script>
                document.getElementById('place-order-btn').onclick = (e) => {
                  e.preventDefault();
                  fetch('/api/custom-order', {method: 'POST'});
                };
                </script></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/checkout/address-rejected"):
                html = """<!doctype html><html><body>
                <div id="checkout">
                  <h1>Checkout</h1>
                  <input type="email" name="email" value="user@example.test">
                  <input type="text" name="address1" value="123 Synthetic Way">
                  <div class="address-error">Address not deliverable</div>
                </div></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/checkout/shipping-ineligible"):
                html = """<!doctype html><html><body>
                <div id="checkout">
                  <h1>Checkout</h1>
                  <div class="shipping-error">No shipping methods available for this destination</div>
                </div></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/checkout/spinner-hang"):
                html = """<!doctype html><html><body>
                <div id="checkout">
                  <h1>Checkout</h1>
                  <div class="shipping-option">Standard Shipping</div>
                  <div class="loading-spinner" data-shipping-loading>Loading shipping...</div>
                </div></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/checkout/spinner-no-eligibility"):
                html = """<!doctype html><html><body>
                <div id="checkout">
                  <h1>Checkout</h1>
                  <div class="loading-spinner" data-shipping-loading>Loading shipping...</div>
                </div></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/checkout/address-no-continuation"):
                html = """<!doctype html><html><body>
                <div id="checkout">
                  <h1>Checkout</h1>
                  <input type="email" name="email" value="user@example.test">
                  <input type="text" name="address1" value="123 Synthetic Way">
                  <input type="text" name="zip" value="10001">
                </div></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

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
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/cart-preexisting-payment-fail"):
                html = """<!doctype html><html><body>
                <form action="/checkout" method="post">
                  <button type="submit" name="checkout">Checkout</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = (e) => {
                  e.preventDefault();
                  window.location.href = '/checkout/preexisting-payment-fail';
                };
                </script></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/cart-place-order-form"):
                html = """<!doctype html><html><body>
                <form action="/checkout" method="post">
                  <button type="submit" name="checkout">Checkout</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = (e) => {
                  e.preventDefault();
                  window.location.href = '/checkout/place-order-form';
                };
                </script></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/cart-address-no-continuation"):
                html = """<!doctype html><html><body>
                <form action="/checkout" method="post">
                  <button type="submit" name="checkout">Checkout</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = (e) => {
                  e.preventDefault();
                  window.location.href = '/checkout/address-no-continuation';
                };
                </script></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/cart-spinner-no-eligibility"):
                html = """<!doctype html><html><body>
                <form action="/checkout" method="post">
                  <button type="submit" name="checkout">Checkout</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = (e) => {
                  e.preventDefault();
                  window.location.href = '/checkout/spinner-no-eligibility';
                };
                </script></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/cart-address-rejected"):
                html = """<!doctype html><html><body>
                <form action="/checkout" method="post">
                  <button type="submit" name="checkout">Checkout</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = (e) => {
                  e.preventDefault();
                  window.location.href = '/checkout/address-rejected';
                };
                </script></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/cart-shipping-ineligible"):
                html = """<!doctype html><html><body>
                <form action="/checkout" method="post">
                  <button type="submit" name="checkout">Checkout</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = (e) => {
                  e.preventDefault();
                  window.location.href = '/checkout/shipping-ineligible';
                };
                </script></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/cart-express-alt"):
                html = """<!doctype html><html><body>
                <div class="cart-container">
                  <button data-shopify-express-checkout class="shopify-payment-button__button">Express Checkout</button>
                </div>
                <script>
                document.querySelector('button').onclick = (e) => {
                  e.preventDefault();
                  window.location.href = '/checkout/working';
                };
                </script></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/cart-empty"):
                carts[session_id] = []
                html = """<!doctype html><html><body><div>Cart Empty</div></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/cart-no-btn"):
                html = """<!doctype html><html><body><div>Cart with no checkout button</div></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/cart-disabled-btn"):
                html = """<!doctype html><html><body>
                <button name="checkout" disabled>Checkout</button>
                </body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/cart-overlay-blocked"):
                html = """<!doctype html><html><body>
                <div class="modal-backdrop" style="position:fixed;top:0;left:0;width:100%;height:100%;z-index:999;">Modal</div>
                <button name="checkout" style="position:absolute;top:10px;left:10px;z-index:1;">Checkout</button>
                </body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/cart-click-no-action"):
                html = """<!doctype html><html><body>
                <button name="checkout">Checkout</button>
                <script>
                document.querySelector('button').onclick = (e) => { e.preventDefault(); };
                </script></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/cart-redirect-loop"):
                html = """<!doctype html><html><body>
                <button name="checkout">Checkout</button>
                <script>
                document.querySelector('button').onclick = (e) => {
                  e.preventDefault();
                  window.location.href = '/cart-redirect-loop';
                };
                </script></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/cart-error-page"):
                html = """<!doctype html><html><body>
                <button name="checkout">Checkout</button>
                <script>
                document.querySelector('button').onclick = (e) => {
                  e.preventDefault();
                  document.body.innerHTML = '500 Internal Server Error';
                };
                </script></body></html>"""
                self.send_with_headers(200, html, "text/html", extra_headers)

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
                self.send_with_headers(200, html, "text/html", extra_headers)

            elif path.startswith("/cart-flaky"):
                flaky_count[0] += 1
                if flaky_count[0] <= 1:
                    html = """<!doctype html><html><body><div>No checkout button in run 1</div></body></html>"""
                else:
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
                self.send_with_headers(200, html, "text/html", extra_headers)

            else:
                self.send(404, "Not found", "text/plain")

        def do_POST(self):
            if "/checkout/pay" in self.path or "/orders/create" in self.path or "/api/custom-order" in self.path:
                order_endpoints_hit.append(self.path)
                self.send(200, json.dumps({"order_id": 999}), "application/json")
                return

            cookie_hdr = self.headers.get("Cookie", "")
            session_id = "default"
            if "cart_session=" in cookie_hdr:
                session_id = cookie_hdr.split("cart_session=")[1].split(";")[0]

            if session_id not in carts:
                carts[session_id] = []

            if self.path == "/cart/add.js":
                headers = [("Set-Cookie", f"cart_session={session_id}; Path=/")]
                carts[session_id].append({"variant_id": 1001, "product_id": 100, "quantity": 1, "price": 2500, "final_price": 2500, "key": "line_key_1001"})
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

        await page.goto(f"{store_url}/cart-working")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-working"})

        assert result.status == "PASS"
        assert result.details["checks"]["cart_readiness"]["status"] == "PASS"
        assert result.details["checks"]["cart_to_checkout"]["status"] == "PASS"
        assert result.details["checks"]["payment_step_access"]["status"] == "PASS"
        await browser.close()


@pytest.mark.asyncio
async def test_express_checkout_alternative_path_pass(checkout_cart_store):
    """Missing standard checkout button falls back to valid express checkout UI path -> PASS."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-express-alt")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-express-alt"})

        assert result.status == "PASS"
        assert result.details["checks"]["cart_to_checkout"]["status"] == "PASS"
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
    """Cart has items but checkout button is missing in both passes -> FAIL (BUTTON_MISSING)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-no-btn")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-no-btn"})

        assert result.status == "FAIL"
        assert result.details["reproduction_summary"]["confirmed"] is True
        assert result.details["reason_code"] == "BUTTON_MISSING"
        await browser.close()


@pytest.mark.asyncio
async def test_disabled_checkout_button_fail(checkout_cart_store):
    """Checkout button is present but disabled in both passes -> FAIL (BUTTON_DISABLED)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-disabled-btn")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-disabled-btn"})

        assert result.status == "FAIL"
        assert result.details["reproduction_summary"]["confirmed"] is True
        assert result.details["reason_code"] == "BUTTON_DISABLED"
        await browser.close()


@pytest.mark.asyncio
async def test_overlay_blocked_button_fail(checkout_cart_store):
    """Checkout button obscured by overlay in both passes -> FAIL (BUTTON_OVERLAY_BLOCKED)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-overlay-blocked")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-overlay-blocked"})

        assert result.status == "FAIL"
        assert result.details["reproduction_summary"]["confirmed"] is True
        assert result.details["reason_code"] == "BUTTON_OVERLAY_BLOCKED"
        await browser.close()


@pytest.mark.asyncio
async def test_click_no_action_fail(checkout_cart_store):
    """Clicking checkout button produces no network request or navigation in both passes -> FAIL (CLICK_NO_ACTION)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-click-no-action")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-click-no-action"})

        assert result.status == "FAIL"
        assert result.details["reproduction_summary"]["confirmed"] is True
        assert result.details["reason_code"] == "CLICK_NO_ACTION"
        await browser.close()


@pytest.mark.asyncio
async def test_cart_redirect_loop_fail(checkout_cart_store):
    """Clicking checkout re-renders cart page in both passes -> FAIL (CHECKOUT_REDIRECT_CART)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-redirect-loop")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-redirect-loop"})

        assert result.status == "FAIL"
        assert result.details["reproduction_summary"]["confirmed"] is True
        assert result.details["reason_code"] == "CHECKOUT_REDIRECT_CART"
        await browser.close()


@pytest.mark.asyncio
async def test_shipping_spinner_timeout_fail(checkout_cart_store):
    """Shipping options loading spinner hangs indefinitely in both passes -> FAIL (SHIPPING_SPINNER_TIMEOUT)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/checkout/spinner-page")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/checkout/spinner-page"})

        assert result.status == "FAIL"
        assert result.details["reproduction_summary"]["confirmed"] is True
        assert result.details["reason_code"] == "SHIPPING_SPINNER_TIMEOUT"
        await browser.close()


@pytest.mark.asyncio
async def test_fault_disappearing_second_pass_warn(checkout_cart_store):
    """Fault appears in pass 1 but vanishes in pass 2 -> overall status WARN."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-flaky")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-flaky"})

        assert result.status == "WARN"
        assert result.details["reproduction_summary"]["confirmed"] is False
        await browser.close()


@pytest.mark.asyncio
async def test_address_rejection_fail(checkout_cart_store):
    """Synthetic address form explicit rejection error notice yields WARN (ADDRESS_VALIDATION_NOTICE)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-address-rejected")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-address-rejected"})

        assert result.status == "WARN"
        assert result.details["checks"]["contact_address"]["reason_code"] == "ADDRESS_VALIDATION_NOTICE"
        await browser.close()


@pytest.mark.asyncio
async def test_shipping_ineligible_warn(checkout_cart_store):
    """Shipping rate error banner yields WARN (SHIPPING_INELIGIBLE)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-shipping-ineligible")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-shipping-ineligible"})

        assert result.status == "WARN"
        assert result.details["checks"]["shipping"]["reason_code"] == "SHIPPING_INELIGIBLE"
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

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-working"})

        assert result.status == "PASS"
        assert len(order_endpoints_hit) == 0, f"SECURITY VIOLATION: Order pay endpoint was called! {order_endpoints_hit}"
        assert result.details["order_pay_guardrail_verified"] is True
        await browser.close()


@pytest.mark.asyncio
async def test_active_payment_route_abort_blocked(checkout_cart_store):
    """Verify Playwright route handler actively aborts network requests to payment/order creation endpoints during auditor.run."""
    store_url, order_endpoints_hit = checkout_cart_store
    order_endpoints_hit.clear()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-working")

        auditor = ShopifyCheckoutBlockerAuditor()

        async def trigger_call():
            await asyncio.sleep(0.1)
            try:
                await page.evaluate("async () => { await fetch('/orders/create', {method: 'POST'}); }")
            except Exception:
                pass

        task1 = asyncio.create_task(auditor._run_single_audit_pass({"page": page, "base_url": f"{store_url}/cart-working"}, page, f"{store_url}/cart-working", "salt"))
        task2 = asyncio.create_task(trigger_call())

        res, meta = await task1
        await task2

        # Server endpoint hit counter must remain 0
        assert len(order_endpoints_hit) == 0, "Payment endpoint reached server despite active route abort!"
        assert res.details["order_pay_guardrail_verified"] is False
        assert len(meta.get("order_pay_endpoints_called", [])) > 0
        await browser.close()


@pytest.mark.asyncio
async def test_address_no_continuation_unproven_warn(checkout_cart_store):
    """Address inputs filled without UI step continuation transition returns WARN (ADDRESS_INPUT_UNPROVEN)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-address-no-continuation")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-address-no-continuation"})

        assert result.status == "WARN"
        assert result.details["checks"]["contact_address"]["reason_code"] == "ADDRESS_INPUT_UNPROVEN"
        await browser.close()


@pytest.mark.asyncio
async def test_shipping_spinner_no_eligibility_warn(checkout_cart_store):
    """Shipping loading spinner timing out without proven address eligibility yields WARN (ADDRESS_VALIDATION_NOTICE)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-spinner-no-eligibility")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-spinner-no-eligibility"})

        assert result.status == "WARN"
        assert result.details["checks"]["shipping"]["reason_code"] == "ADDRESS_VALIDATION_NOTICE"
        await browser.close()


@pytest.mark.asyncio
async def test_per_uuid_cart_session_isolation(checkout_cart_store):
    """Verify that cart store isolates state by UUID session cookie and mutating one cart does not mutate another session's cart."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        ctx1 = await browser.new_context()
        page1 = await ctx1.new_page()
        await page1.goto(f"{store_url}/cart-working")

        ctx2 = await browser.new_context()
        page2 = await ctx2.new_page()
        await page2.goto(f"{store_url}/cart-empty")

        # Mutate session 1 cart by adding an item via POST
        await page1.request.post(f"{store_url}/cart/add.js", data={"id": 1001, "quantity": 1})

        res1 = await page1.request.get(f"{store_url}/cart.js")
        data1 = await res1.json()

        res2 = await page2.request.get(f"{store_url}/cart.js")
        data2 = await res2.json()

        # Session 1 should be mutated (2 items), session 2 must remain isolated (0 items)
        assert data1["item_count"] == 2
        assert data2["item_count"] == 0
        await browser.close()


@pytest.mark.asyncio
async def test_place_order_button_never_clicked(checkout_cart_store):
    """Verify auditor NEVER clicks 'Place order' buttons and zero custom order requests reach server."""
    store_url, order_endpoints_hit = checkout_cart_store
    order_endpoints_hit.clear()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-place-order-form")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-place-order-form"})

        assert result.status == "WARN"
        assert len(order_endpoints_hit) == 0, f"SECURITY VIOLATION: Custom order endpoint reached! {order_endpoints_hit}"
        await browser.close()


@pytest.mark.asyncio
async def test_preexisting_payment_elements_unproven_address_warn(checkout_cart_store):
    """Pre-existing payment elements on page without address continuation transition proof MUST NOT return PASS (returns WARN)."""
    store_url, _ = checkout_cart_store
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(f"{store_url}/cart-preexisting-payment-fail")

        auditor = ShopifyCheckoutBlockerAuditor()
        result = await auditor.run({"page": page, "base_url": f"{store_url}/cart-preexisting-payment-fail"})

        assert result.status == "WARN"
        assert result.details["checks"]["contact_address"]["status"] != "PASS"
        assert result.details["checks"]["contact_address"]["reason_code"] == "ADDRESS_INPUT_UNPROVEN"
        await browser.close()


@pytest.mark.asyncio
async def test_mock_cli_server_cart_session_mutation_isolation():
    """Directly test MockShopifyCheckoutServer in CLI script to prove POST /cart/add.js mutates session 1 without mutating session 2."""
    from scripts.run_phase6_cli_verification import MockShopifyCheckoutServer
    from http.server import ThreadingHTTPServer
    import urllib.request

    server = ThreadingHTTPServer(("127.0.0.1", 0), MockShopifyCheckoutServer)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        url = f"http://127.0.0.1:{port}"

        # Fetch cart.js for Session 1
        req1 = urllib.request.Request(f"{url}/cart.js", headers={"Cookie": "cart_session=sess-uuid-1"})
        with urllib.request.urlopen(req1) as resp1:
            data1_init = json.loads(resp1.read().decode())

        # Fetch cart.js for Session 2
        req2 = urllib.request.Request(f"{url}/cart.js", headers={"Cookie": "cart_session=sess-uuid-2"})
        with urllib.request.urlopen(req2) as resp2:
            data2_init = json.loads(resp2.read().decode())

        assert data1_init["item_count"] == 0
        assert data2_init["item_count"] == 0

        # Mutate Session 1 via POST /cart/add.js
        post_req = urllib.request.Request(
            f"{url}/cart/add.js",
            data=b"id=1001&quantity=1",
            headers={"Cookie": "cart_session=sess-uuid-1", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(post_req) as post_resp:
            assert post_resp.status == 200

        # Verify Session 1 cart is mutated (item_count = 1)
        with urllib.request.urlopen(req1) as resp1_after:
            data1_after = json.loads(resp1_after.read().decode())

        # Verify Session 2 cart remains UNMUTATED (item_count = 0)
        with urllib.request.urlopen(req2) as resp2_after:
            data2_after = json.loads(resp2_after.read().decode())

        assert data1_after["item_count"] == 1, f"Session 1 failed to mutate! Got: {data1_after['item_count']}"
        assert data2_after["item_count"] == 0, f"Session 2 mutated unexpectedly! Got: {data2_after['item_count']}"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)


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
