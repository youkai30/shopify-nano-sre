"""Real Playwright shopper journey against an isolated local storefront."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.async_api import async_playwright

from nano_sre.skills.shopify_shopper import ShopifyShopper


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("api_mode, expected_evidence, expected_trigger", [
    ("valid", "nonempty", "auto_open_drawer"),
    ("http_error", "failed", "auto_open_drawer"),
    ("api_only", "nonempty", "api_only_fallback"),
])
async def test_real_browser_cart_journey_and_shared_session(api_mode, expected_evidence, expected_trigger):
    observed = {"cart_cookie": None, "drawer_shown": False}

    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, status, body, content_type="text/html; charset=utf-8", cookie=False):
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            if cookie:
                self.send_header("Set-Cookie", "fixture_cart_session=only-local; Path=/; SameSite=Lax")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path.startswith("/products/item"):
                body = """<!doctype html><button class='add-to-cart'>Add to cart</button>
                  <script>window.Shopify={routes:{root:'/'}};
                  document.querySelector('button').onclick=async()=>{
                    await fetch('/cart/add.js',{method:'POST',body:'fixture'});
                    setTimeout(()=>{
                      if (AUTO_DRAWER) {
                      document.body.insertAdjacentHTML('beforeend',
                        `<div role="dialog" aria-modal="true"><h2>CART (1 ITEM)</h2><button name="checkout">CHECKOUT</button></div>`);
                      document.querySelector('button[name="checkout"]').onclick=()=>
                        window.location.assign('/checkouts/fixture-session-secret?session=secret');
                      }
                    },500);
                  };</script>"""
                self.send(200, body.replace("AUTO_DRAWER", str(api_mode != "api_only").lower()), cookie=True)
            elif self.path == "/cart.js":
                observed["cart_cookie"] = self.headers.get("Cookie")
                if api_mode == "http_error":
                    self.send(503, "unavailable", "text/plain")
                else:
                    payload = {"item_count": 2, "items": [{
                        "key": "fixture-line", "variant_id": 4, "product_id": 8,
                        "product_title": "Fixture product", "variant_title": "Default",
                        "quantity": 2, "handle": "item", "properties": {"private": "drop"},
                    }]}
                    self.send(200, json.dumps(payload), "application/json")
            elif self.path.startswith("/checkouts/fixture"):
                self.send(200, "checkout fixture")
            elif self.path == "/cart":
                self.send(200, "<button name='checkout' onclick=\"location.href='/checkouts/fixture-session-secret?session=secret'\">CHECKOUT</button>")
            else:
                self.send(200, "fixture")

        def do_POST(self):
            if self.path == "/cart/add.js":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send(200, "{}", "application/json")
            else:
                self.send(404, "missing", "text/plain")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Storefront)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    browser = None
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            result = await ShopifyShopper().run({"page": page, "base_url": origin + "/products/item"})
            assert result.details["cart_evidence_meta"]["status"] == expected_evidence
            assert result.details["cart_evidence_meta"]["trigger"] == expected_trigger
            assert result.details["cart_evidence_meta"]["source"] == "api"
            assert observed["cart_cookie"] and "fixture_cart_session=only-local" in observed["cart_cookie"]
            if api_mode == "valid":
                assert result.details["cart_evidence"][0]["quantity"] == 2
                assert "properties" not in result.details["cart_evidence"][0]
                assert "session=secret" not in result.details["checkout_url"]
                assert "fixture-session-secret" not in result.details["checkout_url"]
                assert result.details["checkout_url"].endswith("/checkouts/[redacted]")
                assert result.status == "PASS"
            await context.close()
    finally:
        if browser:
            await browser.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
