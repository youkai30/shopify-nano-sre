"""Stateful local synthetic Shopify storefront server for Phase 2 Variant Logic audits."""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

MODE = sys.argv[1] if len(sys.argv) > 1 else "pass"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 9880

# Server-side stateful cart state per mode/session
CART_STATE = {"items": []}


class StatefulVariantStorefront(BaseHTTPRequestHandler):
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
        if self.path.endswith("products/item.js") or self.path.endswith("products/item.json"):
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
                body = """<!doctype html><html><body>
                  <form action="/cart/add" method="post">
                    <input type="hidden" name="id" value="1001">
                    <button type="button" class="swatch" data-option-value="Large / Blue" onclick="document.querySelector('input[name=id]').value='1001'">Large / Blue</button>
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

            else:  # PASS mode
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

        elif self.path.endswith("cart.js"):
            # Return stateful cart items
            total_qty = sum(item.get("quantity", 0) for item in CART_STATE["items"])
            self.reply(200, json.dumps({
                "item_count": total_qty,
                "items": CART_STATE["items"]
            }), "application/json")

        else:
            self.reply(404, "not found", "text/plain")

    def do_POST(self):
        if self.path.endswith("cart/add.js") or self.path.endswith("cart/add"):
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length).decode("utf-8")
            parsed_form = parse_qs(post_data)
            added_id = parsed_form.get("id", ["1002"])[0]
            if added_id.isdigit():
                vid = int(added_id)
                # Update stateful cart
                found = False
                for item in CART_STATE["items"]:
                    if item.get("variant_id") == vid:
                        item["quantity"] += 1
                        found = True
                        break
                if not found:
                    CART_STATE["items"].append({"variant_id": vid, "id": vid, "quantity": 1})

            self.reply(200, json.dumps({"status": "added", "items": CART_STATE["items"]}), "application/json")
        else:
            self.reply(200, "{}", "application/json")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), StatefulVariantStorefront)
    print(f"Stateful Variant Storefront running on port {PORT} in mode {MODE}", flush=True)
    server.serve_forever()
