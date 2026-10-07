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
            elif self.path.startswith("/checkouts/"):
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
