"""Tests for Shopify Cart Consistency Auditor Skill (Phase 5).

Verifies PDP variant selection, request payload, Ajax cart state, Cart Drawer / Cart Page UI consistency,
selling plan matching, distinct line items, optional quantity updates, non-mutative PII masking, local 2-pass reproduction, and CLI exclusion.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from playwright.async_api import async_playwright

from nano_sre.skills.shopify_cart_consistency_auditor import ShopifyCartConsistencyAuditor
from nano_sre.cli import DEFAULT_SKILLS


@pytest.fixture(scope="module")
def local_cart_store():
    carts = {}

    class CartStoreHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path

            if path in [
                "/products/consistent.js",
                "/products/nonempty.js",
                "/products/diff-props.js",
                "/products/line-reorder.js",
                "/products/delayed-drawer.js",
                "/products/stale-drawer.js",
                "/products/cart-page.js",
                "/products/sale-price.js",
                "/products/auto-discount.js",
                "/products/price-mismatch.js",
                "/products/incomparable-price.js",
                "/products/bundle-gift.js",
                "/products/qty-update.js",
                "/products/pdp-mismatch.js",
                "/products/swatch-no-change.js",
                "/products/unrelated-plus.js",
                "/products/changing-repro.js",
                "/products/ambiguous-curr.js",
                "/products/wrong-ui-variant.js",
                "/products/two-lines-props.js",
                "/products/delayed-atc.js",
                "/products/selling-plan-mismatch.js",
                "/products/other-product-small-first.js",
                "/products/ambiguous-qty-update.js",
                "/products/plan-11-vs-211.js",
                "/products/number-11-in-title.js",
                "/products/delayed-ui-qty.js",
            ]:
                handle = path.split("/")[2].replace(".js", "")
                data = {
                    "id": 100,
                    "title": f"Product {handle}",
                    "handle": handle,
                    "options": [{"name": "Size", "values": ["Small", "Large"]}],
                    "variants": [
                        {"id": 1001, "title": "Small", "price": 2500, "compare_at_price": 3000, "available": True, "options": ["Small"]},
                        {"id": 1002, "title": "Large", "price": 3000, "compare_at_price": 3500, "available": True, "options": ["Large"]},
                    ],
                }
                self.send(200, json.dumps(data), "application/json")

            elif path == "/products/wrong-variant.js":
                data = {
                    "id": 200,
                    "title": "Product wrong-variant",
                    "handle": "wrong-variant",
                    "options": [],
                    "variants": [
                        {"id": 2001, "title": "Target Variant", "price": 2000, "available": True, "options": []},
                        {"id": 2002, "title": "Wrong Variant", "price": 2000, "available": True, "options": []},
                    ],
                }
                self.send(200, json.dumps(data), "application/json")

            elif path == "/products/qty-mismatch.js":
                data = {
                    "id": 300,
                    "title": "Product qty-mismatch",
                    "handle": "qty-mismatch",
                    "options": [],
                    "variants": [
                        {"id": 3001, "title": "Default", "price": 1500, "available": True, "options": []}
                    ],
                }
                self.send(200, json.dumps(data), "application/json")

            elif path == "/products/no-cart-change.js":
                data = {
                    "id": 400,
                    "title": "Product no-cart-change",
                    "handle": "no-cart-change",
                    "options": [],
                    "variants": [
                        {"id": 4001, "title": "Default", "price": 1500, "available": True, "options": []}
                    ],
                }
                self.send(200, json.dumps(data), "application/json")

            elif path == "/products/unfulfilled.js":
                data = {
                    "id": 500,
                    "title": "Product unfulfilled",
                    "handle": "unfulfilled",
                    "options": [],
                    "variants": [
                        {"id": 5001, "title": "Default", "price": 1500, "available": True, "options": []}
                    ],
                }
                self.send(200, json.dumps(data), "application/json")

            elif path == "/cart.js":
                cookie_hdr = self.headers.get("Cookie", "")
                session_id = "default"
                if "cart_session=" in cookie_hdr:
                    session_id = cookie_hdr.split("cart_session=")[1].split(";")[0]
                c = carts.get(session_id, [])
                self.send(200, json.dumps({"item_count": sum(i["quantity"] for i in c), "currency": "USD", "items": c}), "application/json")

            elif path.startswith("/products/consistent"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]"><option value="Small" selected>Small</option></select>
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:none;">
                  <div class="cart-item">
                    <span class="title">Product consistent - Small</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                  document.querySelector('.cart-drawer').style.display = 'block';
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/nonempty"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product nonempty - Small</span>
                    <input class="quantity" value="3">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                  document.querySelector('.cart-item .quantity').value = "3";
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/diff-props"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <input type="text" name="properties[Engraving]" value="Custom Text">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product diff-props - Small (Custom Text)</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1&properties[Engraving]=Custom Text'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/plan-11-vs-211"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <input type="hidden" name="selling_plan" value="11">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product plan-11-vs-211 - Small</span>
                    <span class="selling-plan">Subscription Plan 211</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1&selling_plan=11'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/number-11-in-title"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <input type="hidden" name="selling_plan" value="11">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product number-11-in-title 11 - Small</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1&selling_plan=11'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/delayed-ui-qty"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product delayed-ui-qty - Small</span>
                    <input class="quantity" value="0">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                  setTimeout(() => {
                    document.querySelector('.cart-item .quantity').value = "1";
                  }, 2200);
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/wrong-variant"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <input type="hidden" name="id" value="2001">
                  <span class="price">$20.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=2002&quantity=1'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/qty-mismatch"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <input type="hidden" name="id" value="3001">
                  <span class="price">$15.00 USD</span>
                  <input type="number" name="quantity" value="3">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product qty-mismatch</span>
                    <input class="quantity" value="1">
                    <span class="price">$15.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=3001&quantity=1'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/pdp-mismatch"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$99.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/ambiguous-curr"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product ambiguous-curr - Small</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/wrong-ui-variant"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product wrong-ui-variant - Large</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/two-lines-props"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <input type="text" name="properties[Engraving]" value="David">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product two-lines-props - Small (Alice)</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                  </div>
                  <div class="cart-item">
                    <span class="title">Product two-lines-props - Small (David)</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1&properties[Engraving]=David'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/delayed-atc"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:none;">
                  <div class="cart-item">
                    <span class="title">Product delayed-atc - Small</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await new Promise(r => setTimeout(r, 1400));
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                  document.querySelector('.cart-drawer').style.display = 'block';
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/selling-plan-mismatch"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <input type="hidden" name="selling_plan" value="11">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product selling-plan-mismatch - Small</span>
                    <span class="selling-plan">Plan 22</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1&selling_plan=11'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/other-product-small-first"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item" id="item-other-product">
                    <span class="title">Other Unrelated Shirt - Small</span>
                    <input class="quantity" value="5">
                    <span class="price">$10.00 USD</span>
                    <button name="plus" class="quantity__button" id="btn-other-plus">+</button>
                  </div>
                  <div class="cart-item" id="item-target-product">
                    <span class="title">Product other-product-small-first - Small</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                    <button name="plus" class="quantity__button" id="btn-target-plus">+</button>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                };
                document.querySelector('#btn-other-plus').onclick = async () => {
                  await fetch('/cart/add.js', {method:'POST', body:'id=9999&quantity=1'});
                };
                document.querySelector('#btn-target-plus').onclick = async () => {
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/ambiguous-qty-update"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product ambiguous-qty-update - Small</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                    <button name="plus" class="quantity__button">+</button>
                  </div>
                  <div class="cart-item">
                    <span class="title">Product ambiguous-qty-update - Small</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                    <button name="plus" class="quantity__button">+</button>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/swatch-no-change"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="button" class="swatch" data-value="Small">Small</button>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('.swatch').onclick = () => {};
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/unrelated-plus"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="unrelated-widget">
                  <button name="plus" class="quantity__button">+</button>
                </div>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product unrelated-plus - Small</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                    <button name="plus" class="quantity__button">+</button>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                };
                document.querySelector('.cart-item button[name="plus"]').onclick = async () => {
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/no-cart-change"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <input type="hidden" name="id" value="4001">
                  <span class="price">$15.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=4001&quantity=1'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/unfulfilled"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$15.00 USD</span>
                  <input type="text" name="properties[Mandatory]" required placeholder="Required note">
                  <button type="submit" name="add">Add to cart</button>
                </form></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/delayed-drawer"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:none;">
                  <div class="cart-item">
                    <span class="title">Product delayed-drawer - Small</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                  setTimeout(() => { document.querySelector('.cart-drawer').style.display='block'; }, 1500);
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/stale-drawer"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product stale-drawer - Small</span>
                    <input class="quantity" value="99">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/cart-page"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <a href="/cart" class="cart-link">Cart</a>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/sale-price"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price-item--sale">$25.00 USD</span>
                  <s class="compare-at-price">$30.00 USD</s>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product sale-price - Small</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/auto-discount"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product auto-discount - Small</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1&discount=500'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/price-mismatch"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1&price=9999'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/incomparable-price"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product incomparable-price - Small</span>
                    <input class="quantity" value="1">
                    <span class="price">$99.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/bundle-gift"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product bundle-gift - Small</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1&bundle=gift'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/qty-update"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product qty-update - Small</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                    <button name="plus" class="quantity__button">+</button>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                };
                document.querySelector('.cart-item button[name="plus"]').onclick = async () => {
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
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
                if "id=1001" in body and "price=9999" in body:
                    carts[session_id].append({"variant_id": 1001, "product_id": 100, "quantity": 1, "price": 9999, "final_price": 9999})
                    self.send_with_headers(200, json.dumps({"id": 1001, "quantity": 1, "price": 9999}), "application/json", headers)
                elif "id=1001" in body and "discount=500" in body:
                    carts[session_id].append({"variant_id": 1001, "product_id": 100, "quantity": 1, "price": 2500, "final_price": 2000, "original_price": 2500, "line_level_discount_allocations": [{"amount": 500}]})
                    self.send_with_headers(200, json.dumps({"id": 1001, "quantity": 1}), "application/json", headers)
                elif "id=1001" in body and "bundle=gift" in body:
                    carts[session_id].append({"variant_id": 1001, "product_id": 100, "quantity": 1, "price": 2500, "final_price": 2500})
                    carts[session_id].append({"variant_id": 9999, "product_id": 999, "quantity": 1, "price": 0, "final_price": 0, "properties": {"_bundle": "free_gift"}})
                    self.send_with_headers(200, json.dumps({"id": 1001, "quantity": 1}), "application/json", headers)
                elif "id=1001" in body and "selling_plan=11" in body:
                    carts[session_id].append({"variant_id": 1001, "product_id": 100, "quantity": 1, "price": 2500, "final_price": 2500, "selling_plan_id": 11})
                    self.send_with_headers(200, json.dumps({"id": 1001, "quantity": 1, "selling_plan": 11}), "application/json", headers)
                elif "id=1001" in body and "properties[Engraving]=Custom Text" in body:
                    carts[session_id].append({"variant_id": 1001, "product_id": 100, "quantity": 1, "price": 2500, "final_price": 2500, "properties": {"Engraving": "Custom Text"}})
                    self.send_with_headers(200, json.dumps({"id": 1001, "quantity": 1}), "application/json", headers)
                elif "id=1001" in body and "properties[Engraving]=David" in body:
                    carts[session_id].append({"variant_id": 1001, "product_id": 100, "quantity": 1, "price": 2500, "final_price": 2500, "properties": {"Engraving": "David"}})
                    self.send_with_headers(200, json.dumps({"id": 1001, "quantity": 1}), "application/json", headers)
                elif "id=1001" in body:
                    found = False
                    for item in carts[session_id]:
                        if item["variant_id"] == 1001 and not item.get("properties"):
                            item["quantity"] += 1
                            found = True
                            break
                    if not found:
                        carts[session_id].append({"variant_id": 1001, "product_id": 100, "quantity": 1, "price": 2500, "final_price": 2500})
                    self.send_with_headers(200, json.dumps({"id": 1001, "quantity": 1}), "application/json", headers)
                elif "id=2002" in body:
                    carts[session_id].append({"variant_id": 2002, "product_id": 200, "quantity": 1, "price": 2000, "final_price": 2000})
                    self.send_with_headers(200, json.dumps({"id": 2002, "quantity": 1}), "application/json", headers)
                elif "id=3001" in body:
                    carts[session_id].append({"variant_id": 3001, "product_id": 300, "quantity": 1, "price": 1500, "final_price": 1500})
                    self.send_with_headers(200, json.dumps({"id": 3001, "quantity": 1}), "application/json", headers)
                elif "id=4001" in body:
                    self.send_with_headers(200, json.dumps({"id": 4001}), "application/json", headers)
                elif "id=9999" in body:
                    carts[session_id].append({"variant_id": 9999, "product_id": 999, "quantity": 5, "price": 1000, "final_price": 1000})
                    self.send_with_headers(200, json.dumps({"id": 9999, "quantity": 5}), "application/json", headers)
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

    server = ThreadingHTTPServer(("127.0.0.1", 0), CartStoreHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{server.server_port}"

    server.shutdown()
    server.server_close()


@pytest.mark.asyncio
async def test_multi_variant_does_not_qualify_for_single_variant_bypass_cart():
    class MultiVariantCartStore(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == "/products/multi.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"id":903, "title":"Multi Variant", "handle":"multi", "options":[{"name":"Size", "values":["S", "M"]}], "variants":[{"id":9003, "title":"S", "price":1000, "available":true, "options":["S"]}, {"id":9004, "title":"M", "price":1200, "available":true, "options":["M"]}]}')
            elif self.path == "/products/multi":
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <input type="hidden" name="id" value="9003">
                  <button type="submit" name="add">Add to Cart</button>
                </form></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"item_count":0, "items":[], "currency":"USD"}')
            else:
                self.send_response(404)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), MultiVariantCartStore)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/products/multi"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reason_code"] in ("UNPROVEN_OPTION_PATTERN", "BUTTON_MISSING", "WARN")
        await browser.close()

    server.shutdown()
    server.server_close()


@pytest.mark.asyncio
async def test_matching_id_input_outside_main_form_rejected_cart():
    class OutsideFormCartStore(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == "/products/outside.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"id":904, "title":"Outside Form", "handle":"outside", "options":["Title"], "variants":[{"id":9005, "title":"Default Title", "price":1000, "available":true, "options":["Default Title"]}]}')
            elif self.path == "/products/outside":
                html = """<!doctype html><html><body>
                <form id="other-form"><input type="hidden" name="id" value="9005"></form>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to Cart</button>
                </form></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"item_count":0, "items":[], "currency":"USD"}')
            else:
                self.send_response(404)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), OutsideFormCartStore)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/products/outside"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reason_code"] in ("UNPROVEN_OPTION_PATTERN", "BUTTON_MISSING", "WARN")
        await browser.close()

    server.shutdown()
    server.server_close()


@pytest.mark.asyncio
async def test_atc_button_prd_product_offers_submit_discovered_cart():
    cart_state = [0]
    class PrdSubmitCartStore(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == "/products/prd-submit.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"id":905, "title":"Prd Submit", "handle":"prd-submit", "options":["Title"], "variants":[{"id":9006, "title":"Default Title", "price":1000, "available":true, "options":["Default Title"]}]}')
            elif self.path == "/products/prd-submit":
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post" class="prd-ProductOffers_Form">
                  <span class="price-item--regular">10.00 USD</span>
                  <input type="hidden" name="id" value="9006">
                  <button type="submit" class="prd-ProductOffers_Submit">Add to Bag</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Prd Submit</span>
                    <input class="quantity" value="1">
                    <span class="price">10.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=9006&quantity=1'});
                };
                </script></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                cnt = cart_state[0]
                if cnt == 0:
                    self.wfile.write(b'{"item_count":0, "items":[], "currency":"USD"}')
                else:
                    self.wfile.write(b'{"item_count":1, "currency":"USD", "items":[{"variant_id":9006, "product_id":905, "quantity":1, "price":1000, "final_price":1000}]}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/cart/add.js" or self.path == "/cart/add":
                cart_state[0] = 1
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"id":9006, "variant_id":9006, "quantity":1}')
            else:
                self.send_response(404)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), PrdSubmitCartStore)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/products/prd-submit"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        await browser.close()

    server.shutdown()
    server.server_close()


@pytest.mark.asyncio
async def test_buy_now_and_unknown_generic_submit_buttons_rejected_cart():
    class ExpressSubmitCartStore(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == "/products/express.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"id":906, "title":"Express", "handle":"express", "options":["Title"], "variants":[{"id":9007, "title":"Default Title", "price":1000, "available":true, "options":["Default Title"]}]}')
            elif self.path == "/products/express":
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <input type="hidden" name="id" value="9007">
                  <button type="submit" class="shopify-payment-button__button">Buy it now</button>
                </form></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"item_count":0, "items":[], "currency":"USD"}')
            else:
                self.send_response(404)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), ExpressSubmitCartStore)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/products/express"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reason_code"] in ("BUTTON_MISSING", "UNPROVEN_OPTION_PATTERN")
        await browser.close()

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.mark.asyncio
async def test_direct_select_variant_options_cart_available_missing_returns_false():
    auditor = ShopifyCartConsistencyAuditor()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('<form action="/cart/add" method="post"><input type="hidden" name="id" value="101"></form>')
        form = page.locator("form").first

        target_variant = {"id": 101, "options": ["Default Title"]} # missing 'available' key
        variants_list = [{"id": 101, "options": ["Default Title"]}] # missing 'available' key
        options_list = [{"name": "Title"}]

        ok, err, dom_opts = await auditor._select_variant_options_in_dom(page, form, target_variant, options_list, variants_list)
        assert ok is False
        assert "available" in err.lower()
        await browser.close()


@pytest.mark.asyncio
async def test_direct_select_variant_options_cart_conflicting_id_fields_returns_false():
    auditor = ShopifyCartConsistencyAuditor()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''
        <form action="/cart/add" method="post">
          <input type="hidden" name="id" value="101">
          <input type="hidden" name="id" value="999">
        </form>
        ''')
        form = page.locator("form").first

        target_variant = {"id": 101, "available": True, "options": ["Default Title"]}
        variants_list = [{"id": 101, "available": True, "options": ["Default Title"]}]
        options_list = [{"name": "Title"}]

        ok, err, dom_opts = await auditor._select_variant_options_in_dom(page, form, target_variant, options_list, variants_list)
        assert ok is False
        assert "conflicting" in err.lower()
        await browser.close()


@pytest.mark.asyncio
async def test_direct_select_variant_options_cart_no_id_input_and_no_controls_returns_false():
    auditor = ShopifyCartConsistencyAuditor()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('<form action="/cart/add" method="post"><button type="submit">Add</button></form>')
        form = page.locator("form").first

        target_variant = {"id": 101, "available": True, "options": ["Default Title"]}
        variants_list = [{"id": 101, "available": True, "options": ["Default Title"]}]
        options_list = [{"name": "Title"}]

        ok, err, dom_opts = await auditor._select_variant_options_in_dom(page, form, target_variant, options_list, variants_list)
        assert ok is False
        assert "no variant id input" in err.lower()
        await browser.close()


@pytest.mark.asyncio
async def test_direct_select_variant_options_cart_valid_single_variant_returns_true():
    auditor = ShopifyCartConsistencyAuditor()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('<form action="/cart/add" method="post"><input type="hidden" name="id" value="101"></form>')
        form = page.locator("form").first

        target_variant = {"id": 101, "available": True, "options": ["Default Title"]}
        variants_list = [{"id": 101, "available": True, "options": ["Default Title"]}]
        options_list = [{"name": "Title"}]

        ok, err, dom_opts = await auditor._select_variant_options_in_dom(page, form, target_variant, options_list, variants_list)
        assert ok is True
        assert dom_opts == {"Title": "Default Title"}
        await browser.close()


@pytest.mark.asyncio
async def test_external_form_attribute_binding_accepted_cart():
    auditor = ShopifyCartConsistencyAuditor()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''
        <form action="/cart/add" method="post" id="prd-form-1"></form>
        <input type="hidden" name="id" value="101" form="prd-form-1">
        <button type="submit" name="add" form="prd-form-1">Add to Cart</button>
        ''')
        form = await auditor._find_main_product_form(page, target_variant_id=101)
        btn = await auditor._find_atc_button(page, form)
        assert btn is not None

        target_variant = {"id": 101, "available": True, "options": ["Default Title"]}
        variants_list = [{"id": 101, "available": True, "options": ["Default Title"]}]
        options_list = [{"name": "Title"}]

        ok, err, dom_opts = await auditor._select_variant_options_in_dom(page, form, target_variant, options_list, variants_list)
        assert ok is True
        await browser.close()


@pytest.mark.asyncio
async def test_external_form_attribute_linked_to_other_form_rejected_cart():
    auditor = ShopifyCartConsistencyAuditor()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''
        <form action="/cart/add" method="post" id="prd-form-1"></form>
        <form id="other-form"></form>
        <input type="hidden" name="id" value="101" form="other-form">
        <button type="submit" name="add" form="prd-form-1">Add to Cart</button>
        ''')
        form = await auditor._find_main_product_form(page, target_variant_id=101)

        target_variant = {"id": 101, "available": True, "options": ["Default Title"]}
        variants_list = [{"id": 101, "available": True, "options": ["Default Title"]}]
        options_list = [{"name": "Title"}]

        ok, err, dom_opts = await auditor._select_variant_options_in_dom(page, form, target_variant, options_list, variants_list)
        assert ok is False
        assert ("no variant id input" in err.lower() or "main product form missing" in err.lower())
        await browser.close()


@pytest.mark.asyncio
async def test_button_formaction_checkout_override_rejected_cart():
    auditor = ShopifyCartConsistencyAuditor()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''
        <form action="/cart/add" method="post" id="prd-form-1">
          <input type="hidden" name="id" value="101">
          <button type="submit" name="add" formaction="/checkout">Buy Now Checkout</button>
        </form>
        ''')
        form = await auditor._find_main_product_form(page, target_variant_id=101)
        btn = await auditor._find_atc_button(page, form)
        assert btn is None
        await browser.close()


@pytest.mark.asyncio
async def test_installments_form_precedes_correct_product_form_cart():
    auditor = ShopifyCartConsistencyAuditor()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''
        <form action="/cart/add" method="post" id="ProductInstallmentsForm" class="prd-ProductPaymentTerms">
          <input type="hidden" name="id" value="101">
          <button type="button">Payment terms info</button>
        </form>
        <form action="/cart/add" method="post" id="MainProductForm" class="prd-ProductOffers_Form">
          <input type="hidden" name="id" value="101">
          <button type="submit" name="add">Add to Bag</button>
        </form>
        ''')
        form = await auditor._find_main_product_form(page, target_variant_id=101)
        form_id = await form.get_attribute("id")
        assert form_id == "MainProductForm"
        btn = await auditor._find_atc_button(page, form)
        assert btn is not None
        await browser.close()


@pytest.mark.asyncio
async def test_element_inside_form1_with_form_attr_other_not_attributed_to_form1_cart():
    auditor = ShopifyCartConsistencyAuditor()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''
        <form id="other-form"></form>
        <form action="/cart/add" method="post" id="form-1">
          <input type="hidden" name="id" value="101" form="other-form">
          <button type="submit" name="add" form="other-form">Add to Cart</button>
        </form>
        ''')
        form = page.locator("#form-1").first

        target_variant = {"id": 101, "available": True, "options": ["Default Title"]}
        variants_list = [{"id": 101, "available": True, "options": ["Default Title"]}]
        options_list = [{"name": "Title"}]

        ok, err, dom_opts = await auditor._select_variant_options_in_dom(page, form, target_variant, options_list, variants_list)
        assert ok is False
        assert "no variant id input found owned by main product form" in err.lower()

        btn = await auditor._find_atc_button(page, form)
        assert btn is None
        await browser.close()


@pytest.mark.asyncio
async def test_unproven_relationship_returns_warn_cart():
    auditor = ShopifyCartConsistencyAuditor()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''
        <div>
          <input type="hidden" name="id" value="101">
          <button type="submit" name="add">Add to Cart</button>
        </div>
        ''')
        form = await auditor._find_main_product_form(page, target_variant_id=101)
        btn = await auditor._find_atc_button(page, form)
        assert btn is None
        await browser.close()


@pytest.mark.asyncio
async def test_consistent_journey_empty_cart(local_cart_store):
    """Empty cart consistent add journey -> PASS."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/consistent"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["checks"]["variant_identity"]["status"] == "PASS"
        assert result.details["checks"]["quantity_consistency"]["status"] == "PASS"
        assert result.details["checks"]["price_consistency"]["status"] == "PASS"
        assert result.details["checks"]["cart_ui_consistency"]["status"] == "PASS"
        await browser.close()


@pytest.mark.asyncio
async def test_non_empty_cart_final_quantity(local_cart_store):
    """Pre-existing items in cart -> Cart UI displays final quantity (3) -> PASS."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/nonempty"
        await page.goto(url)

        # Pre-populate cart via API with 2 items
        await page.request.post(f"{local_cart_store}/cart/add.js", data="id=1001&quantity=1")
        await page.request.post(f"{local_cart_store}/cart/add.js", data="id=1001&quantity=1")

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["checks"]["cart_ui_consistency"]["expected_quantity"] == 3
        await browser.close()


@pytest.mark.asyncio
async def test_distinct_line_items_different_properties(local_cart_store):
    """Same variant with different line properties treated as distinct lines -> PASS."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/diff-props"
        await page.goto(url)

        await page.request.post(f"{local_cart_store}/cart/add.js", data="id=1001&quantity=1")

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["checks"]["variant_identity"]["status"] == "PASS"
        await browser.close()


@pytest.mark.asyncio
async def test_plan_11_vs_211_ui_fail(local_cart_store):
    """Plan 11 requested, but UI displays Plan 211 -> FAIL (WRONG_CART_UI_SELLING_PLAN)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/plan-11-vs-211"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["checks"]["cart_ui_consistency"]["status"] == "FAIL"
        assert result.details["checks"]["cart_ui_consistency"]["reason_code"] == "WRONG_CART_UI_SELLING_PLAN"
        await browser.close()


@pytest.mark.asyncio
async def test_number_11_in_title_unproven_warn(local_cart_store):
    """Number 11 appears in title/price without plan label -> WARN (CART_UI_SELLING_PLAN_UNPROVEN)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/number-11-in-title"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["checks"]["cart_ui_consistency"]["reason_code"] == "CART_UI_SELLING_PLAN_UNPROVEN"
        await browser.close()


@pytest.mark.asyncio
async def test_delayed_ui_qty_pass(local_cart_store):
    """Cart UI quantity starts at 0 and becomes 1 after 2.2 seconds -> PASS."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/delayed-ui-qty"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["checks"]["cart_ui_consistency"]["status"] == "PASS"
        await browser.close()


@pytest.mark.asyncio
async def test_selling_plan_mismatch_ui_fail(local_cart_store):
    """Target requested Plan 11, but UI displays Plan 22 -> FAIL (WRONG_CART_UI_SELLING_PLAN)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/selling-plan-mismatch"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["checks"]["cart_ui_consistency"]["status"] == "FAIL"
        assert result.details["checks"]["cart_ui_consistency"]["reason_code"] == "WRONG_CART_UI_SELLING_PLAN"
        await browser.close()


@pytest.mark.asyncio
async def test_other_product_small_first_quantity_update_unmutated(local_cart_store):
    """Another product with 'Small' listed first in UI -> target line clicked, other product NOT mutated -> PASS."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/other-product-small-first"
        await page.goto(url)

        # Pre-add other product in browser session context
        await page.evaluate("""async () => {
            await fetch('/cart/add.js', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: 'id=9999&quantity=5'});
        }""")

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url, "enable_quantity_update": True})

        assert result.status == "PASS"
        assert result.details["checks"]["quantity_update"]["status"] == "PASS"

        # Verify other product's quantity in API remained 5
        res = await page.request.get(f"{local_cart_store}/cart.js")
        data = await res.json()
        other_item = [i for i in data["items"] if i["variant_id"] == 9999][0]
        assert other_item["quantity"] == 5, "Unrelated product was mutated!"
        await browser.close()


@pytest.mark.asyncio
async def test_ambiguous_qty_update_warn_no_click(local_cart_store):
    """Ambiguous line binding in UI -> returns WARN without clicking -> WARN."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/ambiguous-qty-update"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url, "enable_quantity_update": True})

        assert result.status == "WARN"
        assert result.details["checks"]["quantity_update"]["reason_code"] == "QUANTITY_UPDATE_LINE_UNPROVEN"
        await browser.close()


@pytest.mark.asyncio
async def test_wrong_variant_added_fail(local_cart_store):
    """Adding wrong variant returns FAIL."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/wrong-variant"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["checks"]["variant_identity"]["status"] == "FAIL"
        assert result.details["checks"]["variant_identity"]["reason_code"] == "WRONG_VARIANT_ADDED"
        await browser.close()


@pytest.mark.asyncio
async def test_request_quantity_mismatch_fail(local_cart_store):
    """UI requested 3, but POST payload requested 1 -> FAIL with REQUEST_QUANTITY_MISMATCH."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/qty-mismatch"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["checks"]["quantity_consistency"]["status"] == "FAIL"
        assert result.details["checks"]["quantity_consistency"]["reason_code"] == "REQUEST_QUANTITY_MISMATCH"
        await browser.close()


@pytest.mark.asyncio
async def test_pdp_dom_price_mismatch_fail(local_cart_store):
    """PDP DOM displayed price ($99.00 USD) differs from cart price ($25.00) even if JSON says $25.00 -> FAIL."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/pdp-mismatch"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["checks"]["price_consistency"]["status"] == "FAIL"
        assert result.details["checks"]["price_consistency"]["reason_code"] == "UNEXPLAINED_PRICE_DISCREPANCY"
        await browser.close()


@pytest.mark.asyncio
async def test_ambiguous_currency_pdp_warn(local_cart_store):
    """PDP price contains generic '$' symbol without ISO currency code -> WARN (CURRENCY_UNPROVEN)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/ambiguous-curr"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["checks"]["price_consistency"]["status"] == "WARN"
        assert result.details["checks"]["price_consistency"]["reason_code"] == "CURRENCY_UNPROVEN"
        await browser.close()


@pytest.mark.asyncio
async def test_wrong_cart_ui_variant_fail(local_cart_store):
    """Target variant is Small, but Cart UI displays Large for same product -> FAIL (WRONG_CART_UI_VARIANT)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/wrong-ui-variant"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["checks"]["cart_ui_consistency"]["status"] == "FAIL"
        assert result.details["checks"]["cart_ui_consistency"]["reason_code"] == "WRONG_CART_UI_VARIANT"
        await browser.close()


@pytest.mark.asyncio
async def test_two_lines_same_product_different_properties(local_cart_store):
    """Two lines for same product with different properties in UI -> matches correct target line -> PASS."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/two-lines-props"
        await page.goto(url)

        # Pre-add line item for Alice via evaluate to share cookies
        await page.evaluate("""async () => {
            await fetch('/cart/add.js', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: 'id=1001&quantity=1&properties[Engraving]=Alice'});
        }""")

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["checks"]["cart_ui_consistency"]["status"] == "PASS"
        await browser.close()


@pytest.mark.asyncio
async def test_delayed_atc_response_pass(local_cart_store):
    """Delayed successful ATC response (1.4s) -> waits for network completion and passes -> PASS."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/delayed-atc"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["checks"]["variant_identity"]["status"] == "PASS"
        await browser.close()


@pytest.mark.asyncio
async def test_swatch_no_active_change_warn(local_cart_store):
    """Swatch click produces no active selection state in DOM -> WARN."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/swatch-no-change"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reason_code"] == "UNPROVEN_OPTION_PATTERN" or result.status == "WARN"
        await browser.close()


@pytest.mark.asyncio
async def test_unrelated_plus_button_ignored(local_cart_store):
    """Plus button in unrelated widget ignored; target line plus button clicked -> PASS."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/unrelated-plus"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url, "enable_quantity_update": True})

        assert result.status == "PASS"
        assert result.details["checks"]["quantity_update"]["status"] == "PASS"
        await browser.close()


@pytest.mark.asyncio
async def test_http200_no_cart_change_warn(local_cart_store):
    """HTTP 200 returned but quantity did not increase -> WARN."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/no-cart-change"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["checks"]["variant_identity"]["reason_code"] == "HTTP_200_NO_CART_CHANGE"
        await browser.close()


@pytest.mark.asyncio
async def test_unfulfilled_customization_warn(local_cart_store):
    """Mandatory required customization input unfulfilled -> WARN."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/unfulfilled"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reason_code"] == "UNFULFILLED_CUSTOMIZATION_REQUIREMENT"
        await browser.close()


@pytest.mark.asyncio
async def test_delayed_cart_drawer_pass(local_cart_store):
    """Delayed cart drawer appearing within 1.5s -> PASS."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/delayed-drawer"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["checks"]["cart_ui_consistency"]["status"] == "PASS"
        await browser.close()


@pytest.mark.asyncio
async def test_stale_cart_drawer_fail(local_cart_store):
    """Cart drawer displays stale quantity (99) after stabilization -> FAIL."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/stale-drawer"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["checks"]["cart_ui_consistency"]["status"] == "FAIL"
        assert result.details["checks"]["cart_ui_consistency"]["reason_code"] == "STALE_CART_UI_QUANTITY"
        await browser.close()


@pytest.mark.asyncio
async def test_cart_page_fallback_pass(local_cart_store):
    """Cart drawer absent, fallback cart link checked -> PASS/WARN scope documented."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/cart-page"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status in ("PASS", "WARN")
        assert "verified_scope" in result.details["checks"]["cart_ui_consistency"]
        await browser.close()


@pytest.mark.asyncio
async def test_sale_price_compare_at_pass(local_cart_store):
    """Sale price matched, compare-at price ignored for unit comparison -> PASS."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/sale-price"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["checks"]["price_consistency"]["status"] == "PASS"
        await browser.close()


@pytest.mark.asyncio
async def test_auto_discount_explained_price_pass(local_cart_store):
    """Price difference explained by automatic discount allocation -> PASS."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/auto-discount"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        assert result.details["checks"]["price_consistency"]["status"] == "PASS"
        assert result.details["checks"]["price_consistency"]["reason_code"] == "EXPLAINED_DISCOUNT"
        await browser.close()


@pytest.mark.asyncio
async def test_unexplained_price_mismatch_fail(local_cart_store):
    """Unexplained price difference ($25.00 vs $99.99) -> FAIL."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/price-mismatch"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "FAIL"
        assert result.details["checks"]["price_consistency"]["status"] == "FAIL"
        assert result.details["checks"]["price_consistency"]["reason_code"] == "UNEXPLAINED_PRICE_DISCREPANCY"
        await browser.close()


@pytest.mark.asyncio
async def test_incomparable_price_warn(local_cart_store):
    """Cart UI price ($99.00 USD) differs from PDP price ($25.00 USD) -> WARN status in cart_ui_consistency check."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/incomparable-price"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["checks"]["cart_ui_consistency"]["reason_code"] == "CART_UI_PRICE_MISMATCH"
        await browser.close()


@pytest.mark.asyncio
async def test_bundle_gift_addition_separated(local_cart_store):
    """Automatic free gift side addition separated from target variant -> PASS."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/bundle-gift"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "PASS"
        side_adds = result.details["checks"]["variant_identity"]["side_additions"]
        assert len(side_adds) == 1
        assert side_adds[0]["variant_id"] == 9999
        await browser.close()


@pytest.mark.asyncio
async def test_optional_line_quantity_update_pass(local_cart_store):
    """Opt-in quantity update via UI button updates cart without mutating other lines -> PASS."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        url = f"{local_cart_store}/products/qty-update"
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url, "enable_quantity_update": True})

        assert result.status == "PASS"
        assert "quantity_update" in result.details["checks"]
        assert result.details["checks"]["quantity_update"]["status"] == "PASS"
        await browser.close()


@pytest.mark.asyncio
async def test_non_mutative_pii_masking_restoration():
    """Verify CSS PII masking does not mutate input or textarea values."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.set_content("""
        <!doctype html><html><body>
        <form action="/cart/add" method="post" class="cart-drawer">
          <input type="text" id="cust-note" value="John Doe Secret Note">
          <button type="submit" name="add">Add to cart</button>
        </form>
        </body></html>
        """)

        auditor = ShopifyCartConsistencyAuditor()
        screenshot_path = await auditor._take_screenshot(page, "pii_masking_check")

        val_after = await page.evaluate("() => document.getElementById('cust-note').value")
        assert val_after == "John Doe Secret Note", f"Input value mutated! Got: {val_after}"

        has_orig_attr = await page.evaluate("() => document.querySelector('[data-orig-bg]') !== null")
        assert has_orig_attr is False, "Temporary dataset attributes were not restored in finally!"

        await browser.close()


def test_skill_excluded_from_default_skills():
    """Verify shopify_cart_consistency_auditor is excluded from CLI DEFAULT_SKILLS."""
    assert "shopify_cart_consistency_auditor" not in DEFAULT_SKILLS

@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_single_variant_matching_id_input_cart_pass():
    cart_state = [0]
    class SingleVariantPassCartStore(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == "/products/single-pass.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"id":901, "title":"Single Pass", "handle":"single-pass", "options":["Title"], "variants":[{"id":9001, "title":"Default Title", "price":1000, "available":true, "options":["Default Title"]}]}')
            elif self.path == "/products/single-pass":
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price-item--regular">10.00 USD</span>
                  <input type="hidden" name="id" value="9001">
                  <button type="submit" name="add">Add to Cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=9001&quantity=1'});
                };
                </script></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                cnt = cart_state[0]
                if cnt == 0:
                    self.wfile.write(b'{"item_count":0, "items":[], "currency":"USD"}')
                else:
                    self.wfile.write(b'{"item_count":1, "currency":"USD", "items":[{"variant_id":9001, "product_id":901, "quantity":1, "price":1000, "final_price":1000}]}')
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/cart/add.js" or self.path == "/cart/add":
                cart_state[0] = 1
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"id":9001, "variant_id":9001, "quantity":1}')
            else:
                self.send_response(404)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), SingleVariantPassCartStore)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/products/single-pass"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.details["checks"]["variant_identity"]["status"] == "PASS"
        assert result.details["checks"]["quantity_consistency"]["status"] == "PASS"
        await browser.close()

    server.shutdown()
    server.server_close()


@pytest.mark.asyncio
async def test_wrong_form_precedes_correct_product_form_cart():
    auditor = ShopifyCartConsistencyAuditor()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''
        <form action="/cart/add" method="post" id="wrong-form">
          <input type="hidden" name="id" value="9999">
          <button type="submit" name="add">Add Wrong</button>
        </form>
        <form action="/cart/add" method="post" id="correct-form">
          <input type="hidden" name="id" value="101">
          <button type="submit" name="add">Add Correct</button>
        </form>
        ''')
        form = await auditor._find_main_product_form(page, target_variant_id=101)
        assert form is not None
        form_id = await form.get_attribute("id")
        assert form_id == "correct-form"
        btn = await auditor._find_atc_button(page, form)
        assert btn is not None
        await browser.close()


@pytest.mark.asyncio
async def test_form_itself_forbidden_destination_action_checkout_rejected_cart():
    auditor = ShopifyCartConsistencyAuditor()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''
        <form action="/checkout" method="post" id="checkout-form">
          <input type="hidden" name="id" value="101">
          <button type="submit" name="add">Buy Now Direct</button>
        </form>
        ''')
        form = page.locator("#checkout-form").first
        btn = await auditor._find_atc_button(page, form)
        assert btn is None
        await browser.close()


@pytest.mark.asyncio
async def test_main_form_none_returns_warn_directly_cart():
    class NoFormCartStore(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == "/products/no-form.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"id":908, "title":"No Form", "handle":"no-form", "options":["Title"], "variants":[{"id":9008, "title":"Default Title", "price":1000, "available":true, "options":["Default Title"]}]}')
            elif self.path == "/products/no-form":
                html = """<!doctype html><html><body><h1>Product without form</h1></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"item_count":0, "items":[], "currency":"USD"}')
            else:
                self.send_response(404)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), NoFormCartStore)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/products/no-form"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reason_code"] == "BUTTON_MISSING"
        await browser.close()

    server.shutdown()
    server.server_close()


@pytest.mark.asyncio
async def test_new_url_document_baseuri_destination_and_method_inspection_cart():
    auditor = ShopifyCartConsistencyAuditor()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''
        <base href="https://example.com/store/">
        <form action="../checkout" method="post" id="relative-checkout-form">
          <input type="hidden" name="id" value="101">
          <button type="submit" name="add">Checkout</button>
        </form>
        ''')
        form = page.locator("#relative-checkout-form").first
        btn = await auditor._find_atc_button(page, form)
        assert btn is None
        await browser.close()


@pytest.mark.asyncio
async def test_single_variant_mismatched_id_input_cart_warn():
    class SingleVariantMismatchCartStore(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == "/products/single-mismatch.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"id":902, "title":"Single Mismatch", "handle":"single-mismatch", "options":["Title"], "variants":[{"id":9002, "title":"Default Title", "price":1000, "available":true, "options":["Default Title"]}]}')
            elif self.path == "/products/single-mismatch":
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <input type="hidden" name="id" value="9999">
                  <button type="submit" name="add">Add to Cart</button>
                </form></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"item_count":0, "items":[], "currency":"USD"}')
            else:
                self.send_response(404)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), SingleVariantMismatchCartStore)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/products/single-mismatch"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(url)

        auditor = ShopifyCartConsistencyAuditor()
        result = await auditor.run({"page": page, "base_url": url})

        assert result.status == "WARN"
        assert result.details["reason_code"] in ("BUTTON_MISSING_OR_DISABLED", "BUTTON_MISSING", "UNPROVEN_OPTION_PATTERN")
        await browser.close()

    server.shutdown()
    server.server_close()
