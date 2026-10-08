"""Browser regressions for drawer counts and the shopper-opened branch."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.async_api import async_playwright

from nano_sre.skills.shopify_shopper import ShopifyShopper


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("mode, expected_status, expected_trigger, expected_count", [
    ("auto_zero", "WARN", "auto_open_drawer", 0),
    ("opened_zero", "WARN", "shopper_opened_drawer", 0),
    ("opened_valid", "PASS", "shopper_opened_drawer", 1),
    ("aria_count", "PASS", "auto_open_drawer", 2),
])
async def test_drawer_count_paths(mode, expected_status, expected_trigger, expected_count):
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, body, content_type="text/html; charset=utf-8"):
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path.startswith("/products/item"):
                auto = mode in {"auto_zero", "aria_count"}
                count = 0 if "zero" in mode else (2 if mode == "aria_count" else 1)
                aria = (
                    "<button aria-label='Open cart (2 items)'>Open cart</button>"
                    if mode == "aria_count" else
                    "<button aria-label='Open cart'>Open cart</button>" if mode == "opened_zero" else ""
                )
                body = f"""<!doctype html><button class='add-to-cart'>Add to cart</button>{aria}
                  <script>window.Shopify={{routes:{{root:'/'}}}};
                  function drawer() {{ document.body.insertAdjacentHTML('beforeend',
                    `<div role="dialog" aria-modal="true"><h2>{'' if mode == 'aria_count' else f'CART ({count} ITEM)'}</h2><button name="checkout">CHECKOUT</button></div>`);
                    document.querySelector('button[name="checkout"]').onclick=()=>location.assign('/checkouts/test-token-secret'); }}
                  document.querySelector('.add-to-cart').onclick=async()=>{{
                    await fetch('/cart/add.js',{{method:'POST',body:'fixture'}});
                    if ({str(auto).lower()}) setTimeout(drawer, 150);
                  }};
                  const opener=document.querySelector('button[aria-label]');
                  if (opener && {str(mode == 'opened_zero').lower()}) opener.onclick=drawer;
                  if (!{str(auto).lower()} && {str(mode == 'opened_valid').lower()}) {{
                    document.body.insertAdjacentHTML('beforeend', `<button aria-label="Open cart">Open cart</button>`);
                    document.querySelector('button[aria-label="Open cart"]').onclick=drawer;
                  }}
                  </script>"""
                self.reply(200, body)
            elif self.path == "/cart.js":
                total = 0 if "zero" in mode else (2 if mode == "aria_count" else 1)
                items = [] if total == 0 else [{"key": "line", "variant_id": 1,
                    "product_id": 2, "product_title": "Fixture", "variant_title": "Default",
                    "quantity": total, "handle": "item"}]
                self.reply(200, json.dumps({"item_count": total, "items": items}), "application/json")
            elif self.path.startswith("/checkouts"):
                self.reply(200, "checkout")
            elif self.path == "/cart":
                self.reply(200, "<button name='checkout'>CHECKOUT</button>")
            else:
                self.reply(404, "missing", "text/plain")

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.reply(200, "{}", "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Storefront)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            result = await ShopifyShopper().run({"page": page, "base_url": origin + "/products/item"})
            await context.close()
            await browser.close()
        assert result.status == expected_status
        assert result.details["cart_evidence_meta"]["trigger"] == expected_trigger
        assert result.details["cart_item_count"] == expected_count
        assert "test-token-secret" not in str(result.details)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_zero_header_with_product_desc_sentence_and_opener_aria_yields_warn():
    class ZeroHeaderWithDescStorefront(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, body, content_type="text/html; charset=utf-8"):
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path.startswith("/products/item"):
                # Header says CART: 0, description sentence says "Set of 2 items", button aria-label="Open cart (2 items)"
                body = """<!doctype html><button class='add-to-cart'>Add to cart</button>
                  <button aria-label="Open cart (2 items)">Open cart</button>
                  <script>window.Shopify={routes:{root:'/'}};
                  function drawer() { document.body.insertAdjacentHTML('beforeend',
                    `<div role="dialog" aria-modal="true"><h2>CART: 0</h2><p>Set of 2 items in stock</p><button name="checkout">CHECKOUT</button></div>`); }
                  document.querySelector('.add-to-cart').onclick=()=>setTimeout(drawer, 100);
                  </script>"""
                self.reply(200, body)
            elif self.path == "/cart.js":
                self.reply(200, json.dumps({"item_count": 0, "items": []}), "application/json")
            else:
                self.reply(404, "missing", "text/plain")

        def do_POST(self):
            self.reply(200, "{}", "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", 0), ZeroHeaderWithDescStorefront)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            result = await ShopifyShopper().run({"page": page, "base_url": origin + "/products/item"})
            await context.close()
            await browser.close()
        # CART: 0 header must win over product description sentence and opener button aria-label -> WARN
        assert result.status == "WARN"
        assert result.details["cart_item_count"] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_promo_text_ignored_and_explicit_zero_header_yields_zero_and_warn():
    class PromoAndZeroHeaderStorefront(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, body, content_type="text/html; charset=utf-8"):
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path.startswith("/products/item"):
                # Promo line mentions 2 items, but header explicitly says CART (0 ITEMS)
                body = """<!doctype html><button class='add-to-cart'>Add to cart</button>
                  <script>window.Shopify={routes:{root:'/'}};
                  function drawer() { document.body.insertAdjacentHTML('beforeend',
                    `<div role="dialog" aria-modal="true"><h2>CART (0 ITEMS)</h2><p>Add 2 items for free shipping</p><button name="checkout">CHECKOUT</button></div>`); }
                  document.querySelector('.add-to-cart').onclick=()=>setTimeout(drawer, 100);
                  </script>"""
                self.reply(200, body)
            elif self.path == "/cart.js":
                self.reply(200, json.dumps({"item_count": 0, "items": []}), "application/json")
            else:
                self.reply(404, "missing", "text/plain")

        def do_POST(self):
            self.reply(200, "{}", "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", 0), PromoAndZeroHeaderStorefront)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            result = await ShopifyShopper().run({"page": page, "base_url": origin + "/products/item"})
            await context.close()
            await browser.close()
        # Explicit zero header wins over promo text "2 items" -> item_count = 0, status WARN (not PASS)
        assert result.status == "WARN"
        assert result.details["cart_item_count"] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_drawer_inner_text_failure_falls_back_to_aria_label():
    class ExceptionStorefront(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, body, content_type="text/html; charset=utf-8"):
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path.startswith("/products/item"):
                # Page button has Open cart (2 items) aria-label
                body = """<!doctype html><button class='add-to-cart'>Add to cart</button>
                  <button aria-label="Open cart (2 items)">Open cart</button>
                  <script>window.Shopify={routes:{root:'/'}};
                  function drawer() {
                    const div = document.createElement('div');
                    div.setAttribute('role', 'dialog');
                    div.setAttribute('aria-modal', 'true');
                    div.setAttribute('aria-label', 'Cart with 2 items');
                    // Override innerText to throw error
                    Object.defineProperty(div, 'innerText', { get() { throw new Error('DOM Error'); } });
                    div.innerHTML = '<a href="/checkouts/12345" name="checkout">CHECKOUT</a>';
                    document.body.appendChild(div);
                  }
                  document.querySelector('.add-to-cart').onclick=()=>setTimeout(drawer, 100);
                  </script>"""
                self.reply(200, body)
            elif self.path == "/cart.js":
                self.reply(200, json.dumps({"item_count": 2, "items": [
                    {"key": "a", "variant_id": 1, "product_id": 2, "product_title": "Tea",
                     "variant_title": "Small", "quantity": 2, "handle": "tea"}
                ]}), "application/json")
            elif self.path.startswith("/checkouts/"):
                self.reply(200, "checkout")
            else:
                self.reply(404, "missing", "text/plain")

        def do_POST(self):
            self.reply(200, "{}", "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", 0), ExceptionStorefront)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            result = await ShopifyShopper().run({"page": page, "base_url": origin + "/products/item"})
            await context.close()
            await browser.close()
        # Even if inner_text fails, aria-label fallback yields 2 items -> PASS
        assert result.status == "PASS"
        assert result.details["cart_item_count"] == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retains_cart_evidence_when_late_checkout_exception_occurs():
    class CheckoutFailStorefront(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, body, content_type="text/html; charset=utf-8"):
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path.startswith("/products/item"):
                body = """<!doctype html><button class='add-to-cart'>Add to cart</button>
                  <script>window.Shopify={routes:{root:'/'}};
                  function drawer() { document.body.insertAdjacentHTML('beforeend',
                    `<div role="dialog" aria-modal="true"><h2>CART (1 ITEM)</h2><button name="checkout">CHECKOUT</button></div>`);
                    document.querySelector('button[name="checkout"]').onclick=()=>{ throw new Error('Checkout JS crashed'); };
                  }
                  document.querySelector('.add-to-cart').onclick=()=>setTimeout(drawer, 100);
                  </script>"""
                self.reply(200, body)
            elif self.path == "/cart.js":
                self.reply(200, json.dumps({"item_count": 1, "items": [
                    {"key": "item-1", "variant_id": 10, "product_id": 20, "product_title": "Hat",
                     "variant_title": "Red", "quantity": 1, "handle": "hat"}
                ]}), "application/json")
            else:
                self.reply(404, "missing", "text/plain")

        def do_POST(self):
            self.reply(200, "{}", "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", 0), CheckoutFailStorefront)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            result = await ShopifyShopper().run({"page": page, "base_url": origin + "/products/item"})
            await context.close()
            await browser.close()
        # Journey fails or warns due to checkout navigation not reaching checkout URL
        assert result.status in ("WARN", "FAIL")
        # Evidence and metadata captured prior to checkout crash must be preserved
        assert len(result.details["cart_evidence"]) == 1
        assert result.details["cart_evidence"][0]["key"] == "item-1"
        assert result.details["cart_evidence_meta"]["status"] == "nonempty"
        assert result.details["cart_evidence_meta"]["api_total_quantity"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("text_snippet", [
    "CART (10,00 EUR)", "BAG: 10,00", "CART (10 USD)", "-2 ITEMS", "2,5 ITEMS",
    "CART (1-2 ITEMS)", "CART (10 JPY)", "CART (2abc)", "Pack of 2 items"
])
async def test_drawer_ignores_price_formats_currencies_floats_and_negatives(text_snippet):
    class PriceFormatStorefront(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, body, content_type="text/html; charset=utf-8"):
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path.startswith("/products/item"):
                body = f"""<!doctype html><button class='add-to-cart'>Add to cart</button>
                  <script>window.Shopify={{routes:{{root:'/'}}}};
                  function drawer() {{ document.body.insertAdjacentHTML('beforeend',
                    `<div role="dialog" aria-modal="true"><h2>{text_snippet}</h2><button name="checkout">CHECKOUT</button></div>`); }}
                  document.querySelector('.add-to-cart').onclick=()=>setTimeout(drawer, 100);
                  </script>"""
                self.reply(200, body)
            elif self.path == "/cart.js":
                self.reply(200, json.dumps({"item_count": 0, "items": []}), "application/json")
            else:
                self.reply(404, "missing", "text/plain")

        def do_POST(self):
            self.reply(200, "{}", "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", 0), PriceFormatStorefront)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            result = await ShopifyShopper().run({"page": page, "base_url": origin + "/products/item"})
            await context.close()
            await browser.close()
        # Price or float/negative expressions must not yield a positive item count -> WARN
        assert result.status == "WARN"
        assert result.details["cart_item_count"] in (None, 0)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retains_cart_evidence_and_sanitizes_logs_on_late_python_checkout_exception(caplog):
    secret_token = "SESSION_SECRET_TOKEN_99999_DO_NOT_LEAK"

    class CheckoutCrashStorefront(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, body, content_type="text/html; charset=utf-8"):
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path.startswith("/products/item"):
                # No visible checkout button in drawer, forces goto(cart_url)
                body = """<!doctype html><button class='add-to-cart'>Add to cart</button>
                  <script>window.Shopify={routes:{root:'/'}};
                  function drawer() { document.body.insertAdjacentHTML('beforeend',
                    `<div role="dialog" aria-modal="true"><h2>CART (1 ITEM)</h2></div>`); }
                  document.querySelector('.add-to-cart').onclick=()=>setTimeout(drawer, 100);
                  </script>"""
                self.reply(200, body)
            elif self.path == "/cart.js":
                self.reply(200, json.dumps({"item_count": 1, "items": [
                    {"key": "item-1", "variant_id": 10, "product_id": 20, "product_title": "Hat",
                     "variant_title": "Red", "quantity": 1, "handle": "hat"}
                ]}), "application/json")
            else:
                self.reply(404, "missing", "text/plain")

        def do_POST(self):
            self.reply(200, "{}", "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", 0), CheckoutCrashStorefront)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            # Mock page.goto to throw python exception when navigating to /cart or checkout
            original_goto = page.goto

            async def goto_with_secret(url, **kwargs):
                if "cart" in str(url) or "checkout" in str(url):
                    raise RuntimeError(f"Python Navigation Error with secret: {secret_token}")
                return await original_goto(url, **kwargs)

            page.goto = goto_with_secret

            with caplog.at_level("DEBUG"):
                result = await ShopifyShopper().run({"page": page, "base_url": origin + "/products/item"})

            await context.close()
            await browser.close()

        # Journey status FAIL
        assert result.status == "FAIL"
        # Evidence and metadata captured prior to Python exception retained
        assert len(result.details["cart_evidence"]) == 1
        assert result.details["cart_evidence"][0]["key"] == "item-1"
        assert result.details["cart_evidence_meta"]["status"] == "nonempty"
        # Snapshot collected exactly once
        assert result.details["cart_evidence_meta"]["api_line_count"] == 1

        # Secret indicator absent from caplog, result, and report text
        assert secret_token not in caplog.text
        assert secret_token not in str(result.details)
        assert secret_token not in str(result.summary)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
