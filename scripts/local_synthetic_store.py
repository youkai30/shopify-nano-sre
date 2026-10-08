"""Standalone local synthetic Shopify storefront server for Nano-SRE CLI audits."""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = sys.argv[1] if len(sys.argv) > 1 else "success"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 9876


class SyntheticStorefront(BaseHTTPRequestHandler):
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
        if self.path.startswith("/products/"):
            if MODE == "success":
                body = """<!doctype html><button class='add-to-cart'>Add to cart</button>
                  <script>window.Shopify={routes:{root:'/'}};
                  function drawer() { document.body.insertAdjacentHTML('beforeend',
                    `<div role="dialog" aria-modal="true"><h2>CART (1 ITEM)</h2><a href="/checkouts/test-token-secret-12345" name="checkout">CHECKOUT</a></div>`); }
                  document.querySelector('.add-to-cart').onclick=async()=>{
                    await fetch('/cart/add.js',{method:'POST',body:'item'});
                    setTimeout(drawer, 100);
                  };
                  </script>"""
            else:
                # Empty cart drawer mode
                body = """<!doctype html><button class='add-to-cart'>Add to cart</button>
                  <script>window.Shopify={routes:{root:'/'}};
                  function drawer() { document.body.insertAdjacentHTML('beforeend',
                    `<div role="dialog" aria-modal="true"><h2>CART (0 ITEMS)</h2><a href="/checkouts/test-token-secret-12345" name="checkout">CHECKOUT</a></div>`); }
                  document.querySelector('.add-to-cart').onclick=async()=>{
                    await fetch('/cart/add.js',{method:'POST',body:'item'});
                    setTimeout(drawer, 100);
                  };
                  </script>"""
            self.reply(200, body)

        elif self.path == "/cart.js":
            if MODE == "success":
                self.reply(200, json.dumps({
                    "item_count": 1,
                    "items": [{
                        "key": "synthetic-item-key-1",
                        "variant_id": 101,
                        "product_id": 202,
                        "product_title": "Synthetic T-Shirt",
                        "variant_title": "Medium / Blue",
                        "quantity": 1,
                        "handle": "synthetic-t-shirt"
                    }]
                }), "application/json")
            else:
                self.reply(200, json.dumps({"item_count": 0, "items": []}), "application/json")

        elif self.path.startswith("/checkouts"):
            self.reply(200, "<html><body><h1>Shopify Local Checkout Page</h1></body></html>")

        elif self.path == "/cart":
            self.reply(200, "<html><body><a href='/checkouts/test-token-secret-12345' name='checkout'>CHECKOUT</a></body></html>")

        else:
            self.reply(404, "not found", "text/plain")

    def do_POST(self):
        self.reply(200, "{}", "application/json")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), SyntheticStorefront)
    print(f"Server running on port {PORT} in mode {MODE}", flush=True)
    server.serve_forever()
