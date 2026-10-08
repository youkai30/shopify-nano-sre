"""Standalone local synthetic Shopify storefront server for Phase 2 Variant Logic audits."""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = sys.argv[1] if len(sys.argv) > 1 else "pass"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 9880


class VariantSyntheticStorefront(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def reply(self, status, body, content_type="text/html; charset=utf-8"):
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/products/item.js" or self.path == "/products/item.json":
            # Official Ajax Product JS API response
            product_data = {
                "id": 9991,
                "title": "Variant Test Shirt",
                "handle": "item",
                "images": [
                    {"id": 501, "src": "http://127.0.0.1/products/shirt-red.jpg"},
                    {"id": 502, "src": "http://127.0.0.1/products/shirt-blue.jpg"}
                ],
                "variants": [
                    {
                        "id": 1001,
                        "title": "Small / Red",
                        "price": 2000,
                        "available": True,
                        "featured_image": {"id": 501, "src": "http://127.0.0.1/products/shirt-red.jpg"}
                    },
                    {
                        "id": 1002,
                        "title": "Large / Blue",
                        "price": 2500,
                        "available": True if MODE != "availability_fail" else False,
                        "featured_image": {"id": 502, "src": "http://127.0.0.1/products/shirt-blue.jpg"}
                    }
                ]
            }
            self.reply(200, json.dumps(product_data), "application/json")

        elif self.path.startswith("/products/item"):
            if MODE == "identity_fail":
                # Form sends stale ID 1001 when Large / Blue (1002) is chosen
                body = """<!doctype html><html><body>
                  <form action="/cart/add" method="post">
                    <input type="hidden" name="id" value="1001">
                    <button type="button" class="swatch" data-option-value="Large / Blue">Large / Blue</button>
                    <span class="price">$25.00</span>
                    <img class="product-single__photo" src="http://127.0.0.1/products/shirt-blue.jpg" />
                    <button type="submit" name="add">ADD TO CART</button>
                  </form>
                  <script>
                  document.querySelector('form').onsubmit = async (e) => {
                    e.preventDefault();
                    await fetch('/cart/add.js', {method: 'POST', body: new URLSearchParams(new FormData(e.target))});
                  };
                  </script></body></html>"""

            elif MODE == "price_fail":
                # Stale price $20.00 displayed when Large / Blue ($25.00 expected) chosen
                body = """<!doctype html><html><body>
                  <form action="/cart/add" method="post">
                    <input type="hidden" name="id" value="1002">
                    <button type="button" class="swatch" data-option-value="Large / Blue">Large / Blue</button>
                    <span class="price">$20.00</span>
                    <img class="product-single__photo" src="http://127.0.0.1/products/shirt-blue.jpg" />
                    <button type="submit" name="add">ADD TO CART</button>
                  </form>
                  <script>
                  document.querySelector('form').onsubmit = async (e) => {
                    e.preventDefault();
                    await fetch('/cart/add.js', {method: 'POST', body: new URLSearchParams(new FormData(e.target))});
                  };
                  </script></body></html>"""

            elif MODE == "image_fail":
                # Stale shirt-red.jpg image displayed when Large / Blue (shirt-blue.jpg expected) chosen
                body = """<!doctype html><html><body>
                  <form action="/cart/add" method="post">
                    <input type="hidden" name="id" value="1002">
                    <button type="button" class="swatch" data-option-value="Large / Blue">Large / Blue</button>
                    <span class="price">$25.00</span>
                    <img class="product-single__photo" src="http://127.0.0.1/products/shirt-red.jpg" />
                    <button type="submit" name="add">ADD TO CART</button>
                  </form>
                  <script>
                  document.querySelector('form').onsubmit = async (e) => {
                    e.preventDefault();
                    await fetch('/cart/add.js', {method: 'POST', body: new URLSearchParams(new FormData(e.target))});
                  };
                  </script></body></html>"""

            elif MODE == "availability_fail":
                # Variant 1002 is Sold Out in JSON, but button remains enabled ADD TO CART
                body = """<!doctype html><html><body>
                  <form action="/cart/add" method="post">
                    <input type="hidden" name="id" value="1002">
                    <button type="button" class="swatch" data-option-value="Large / Blue">Large / Blue</button>
                    <span class="price">$25.00</span>
                    <img class="product-single__photo" src="http://127.0.0.1/products/shirt-blue.jpg" />
                    <button type="submit" name="add">ADD TO CART</button>
                  </form>
                  <script>
                  document.querySelector('form').onsubmit = async (e) => {
                    e.preventDefault();
                    await fetch('/cart/add.js', {method: 'POST', body: new URLSearchParams(new FormData(e.target))});
                  };
                  </script></body></html>"""

            else: # PASS mode
                body = """<!doctype html><html><body>
                  <form action="/cart/add" method="post">
                    <input type="hidden" name="id" value="1002">
                    <button type="button" class="swatch" data-option-value="Large / Blue">Large / Blue</button>
                    <span class="price">$25.00</span>
                    <img class="product-single__photo" src="http://127.0.0.1/products/shirt-blue.jpg" />
                    <button type="submit" name="add">ADD TO CART</button>
                  </form>
                  <script>
                  document.querySelector('form').onsubmit = async (e) => {
                    e.preventDefault();
                    await fetch('/cart/add.js', {method: 'POST', body: new URLSearchParams(new FormData(e.target))});
                  };
                  </script></body></html>"""

            self.reply(200, body)

        elif self.path == "/cart.js":
            if MODE == "identity_fail":
                self.reply(200, json.dumps({
                    "item_count": 1,
                    "items": [{"variant_id": 1001, "id": 1001, "quantity": 1}]
                }), "application/json")
            else:
                self.reply(200, json.dumps({
                    "item_count": 1,
                    "items": [{"variant_id": 1002, "id": 1002, "quantity": 1}]
                }), "application/json")

        else:
            self.reply(404, "not found", "text/plain")

    def do_POST(self):
        self.reply(200, "{}", "application/json")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), VariantSyntheticStorefront)
    print(f"Variant Storefront running on port {PORT} in mode {MODE}", flush=True)
    server.serve_forever()
