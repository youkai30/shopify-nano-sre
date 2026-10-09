"""Comprehensive test suite for ShopifyVariantAuditor covering all Phase 2 requirements."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import async_playwright

from nano_sre.skills.shopify_variant_auditor import ShopifyVariantAuditor, parse_price_amount


def test_parse_price_amount_cases():
    """Unit tests for strict price parsing logic."""
    assert parse_price_amount("$1,500.00") == 1500.0
    assert parse_price_amount("1,500.00 €") == 1500.0
    assert parse_price_amount("$15.99") == 15.99
    assert parse_price_amount("15.99") == 15.99
    assert parse_price_amount("$15") == 15.0
    assert parse_price_amount("1.500,00 €") == 1500.0
    assert parse_price_amount("15,99 €") == 15.99
    assert parse_price_amount(None) is None
    assert parse_price_amount("No price here") is None


def make_test_server(handler_factory):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture
def svg_image():
    return '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="red"/></svg>'


# 1. Non-default variant selection + valid ATC
@pytest.mark.asyncio
async def test_variant_auditor_pass_flow(svg_image):
    cart_items = []

    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small", "Large"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"],
                         "featured_image": {"src": "/red.svg"}},
                        {"id": 102, "title": "Large", "price": 2500, "available": True, "options": ["Large"],
                         "featured_image": {"src": "/blue.svg"}},
                        {"id": 103, "title": "XL", "price": 3000, "available": False, "options": ["XL"]}
                    ]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path in ("/red.svg", "/blue.svg"):
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.end_headers()
                self.wfile.write(svg_image.encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                data = {"item_count": sum(i["quantity"] for i in cart_items), "items": cart_items}
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post" class="prd-ProductOffers_Form">
                  <select name="options[Size]">
                    <option value="Small">Small</option>
                    <option value="Large">Large</option>
                    <option value="XL">XL</option>
                  </select>
                  <span class="price-item--regular">$15.99</span>
                  <img class="product-featured-media" src="/red.svg" width="100" height="100"/>
                  <button type="submit" name="add" class="add-to-cart">Add to cart</button>
                </form>
                <script>
                const select = document.querySelector('select');
                const price = document.querySelector('.price-item--regular');
                const img = document.querySelector('img');
                const btn = document.querySelector('button');
                select.onchange = () => {
                  if (select.value === 'Large') {
                    price.textContent = '$25.00';
                    img.src = '/blue.svg';
                    btn.disabled = false;
                    btn.textContent = 'Add to cart';
                  } else if (select.value === 'XL') {
                    btn.disabled = true;
                    btn.textContent = 'Sold Out';
                  } else {
                    price.textContent = '$15.99';
                    img.src = '/red.svg';
                    btn.disabled = false;
                    btn.textContent = 'Add to cart';
                  }
                };
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  const val = select.value;
                  const id = val === 'Large' ? 102 : 101;
                  await fetch('/cart/add.js', {method:'POST', body: 'id=' + id});
                };
                </script></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/cart/add.js":
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                vid = 102 if "102" in body else 101
                cart_items.append({"variant_id": vid, "product_id": 1, "quantity": 1})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{origin}/products/item")
            auditor = ShopifyVariantAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "PASS"
            assert res.details["checks"]["variant_identity"]["status"] == "PASS"
            assert res.details["checks"]["variant_price"]["status"] == "PASS"
            assert res.details["checks"]["variant_image"]["status"] == "PASS"
            assert res.details["checks"]["variant_availability_display"]["status"] == "PASS"
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 2. Visual selection vs wrong variant ID added -> FAIL
@pytest.mark.asyncio
async def test_variant_auditor_wrong_variant_id(svg_image):
    cart_items = []

    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small", "Large"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "options": ["Small"]},
                        {"id": 102, "title": "Large", "price": 2500, "options": ["Large"]}
                    ]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                data = {"item_count": len(cart_items), "items": cart_items}
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Small">Small</option>
                    <option value="Large">Large</option>
                  </select>
                  <span class="price-item--regular">$25.00</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  // Bug: Always adds variant 101 even when Large is selected!
                  await fetch('/cart/add.js', {method:'POST', body: 'id=101'});
                };
                </script></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

        def do_POST(self):
            if self.path == "/cart/add.js":
                cart_items.append({"variant_id": 101, "product_id": 1, "quantity": 1})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{origin}/products/item")
            auditor = ShopifyVariantAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "FAIL"
            assert res.details["checks"]["variant_identity"]["status"] == "FAIL"
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 3. Empty cart despite ATC request -> FAIL
@pytest.mark.asyncio
async def test_variant_auditor_empty_cart():
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small", "Large"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "options": ["Small"]},
                        {"id": 102, "title": "Large", "price": 2500, "options": ["Large"]}
                    ]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path == "/cart.js":
                # Always returns empty cart
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"item_count": 0, "items": []}).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Small">Small</option>
                    <option value="Large">Large</option>
                  </select>
                  <span class="price-item--regular">$25.00</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body: 'id=102'});
                };
                </script></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

        def do_POST(self):
            if self.path == "/cart/add.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{origin}/products/item")
            auditor = ShopifyVariantAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "FAIL"
            assert res.details["checks"]["variant_identity"]["status"] == "FAIL"
            assert "quantity did not increase" in res.details["checks"]["variant_identity"]["summary"]
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 4. Pre-existing variant without quantity increase -> FAIL
@pytest.mark.asyncio
async def test_variant_auditor_preexisting_variant_no_increase():
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small", "Large"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "options": ["Small"]},
                        {"id": 102, "title": "Large", "price": 2500, "options": ["Large"]}
                    ]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path == "/cart.js":
                # Pre-existing item with qty=1 that never increases
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                data = {"item_count": 1, "items": [{"variant_id": 102, "product_id": 1, "quantity": 1}]}
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Small">Small</option>
                    <option value="Large">Large</option>
                  </select>
                  <span class="price-item--regular">$25.00</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  // ATC fails silently or server rejects without quantity increase
                  await fetch('/cart/add.js', {method:'POST', body: 'id=102'});
                };
                </script></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

        def do_POST(self):
            if self.path == "/cart/add.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{origin}/products/item")
            auditor = ShopifyVariantAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "FAIL"
            assert res.details["checks"]["variant_identity"]["status"] == "FAIL"
            assert res.details["checks"]["variant_identity"]["delta_quantity"] == 0
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 5. Wrong PDP price with matching recommendation price -> FAIL
@pytest.mark.asyncio
async def test_variant_auditor_wrong_pdp_price_matching_recommendation():
    cart_items = []

    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small", "Large"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "options": ["Small"]},
                        {"id": 102, "title": "Large", "price": 2500, "options": ["Large"]}
                    ]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"item_count": len(cart_items), "items": cart_items}).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Small">Small</option>
                    <option value="Large">Large</option>
                  </select>
                  <!-- Main PDP displays $10.00 instead of expected $25.00 -->
                  <span class="price-item--regular">$10.00</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="recommendations">
                  <!-- Recommendation card has $25.00 -->
                  <span class="price-item--regular">$25.00</span>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body: 'id=102'});
                };
                </script></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

        def do_POST(self):
            if self.path == "/cart/add.js":
                cart_items.append({"variant_id": 102, "product_id": 1, "quantity": 1})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{origin}/products/item")
            auditor = ShopifyVariantAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "FAIL"
            assert res.details["checks"]["variant_price"]["status"] == "FAIL"
            assert res.details["checks"]["variant_price"]["displayed_price"] == 10.0
            assert res.details["checks"]["variant_price"]["expected_price"] == 25.0
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 6. Correct price parsing 1,500.00 -> PASS
@pytest.mark.asyncio
async def test_variant_auditor_price_formatting_thousands():
    cart_items = []

    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Default"]}],
                    "variants": [
                        {"id": 101, "title": "Default", "price": 150000, "options": ["Default"]}
                    ]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"item_count": len(cart_items), "items": cart_items}).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Default">Default</option>
                  </select>
                  <span class="price-item--regular">$1,500.00</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body: 'id=101'});
                };
                </script></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

        def do_POST(self):
            if self.path == "/cart/add.js":
                cart_items.append({"variant_id": 101, "product_id": 1, "quantity": 1})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{origin}/products/item")
            auditor = ShopifyVariantAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.details["checks"]["variant_price"]["status"] == "PASS"
            assert res.details["checks"]["variant_price"]["displayed_price"] == 1500.0
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 7. Price 15 instead of 15.99 -> FAIL
@pytest.mark.asyncio
async def test_variant_auditor_price_truncated():
    cart_items = []

    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Default"]}],
                    "variants": [
                        {"id": 101, "title": "Default", "price": 1599, "options": ["Default"]}
                    ]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"item_count": len(cart_items), "items": cart_items}).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Default">Default</option>
                  </select>
                  <span class="price-item--regular">$15</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body: 'id=101'});
                };
                </script></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

        def do_POST(self):
            if self.path == "/cart/add.js":
                cart_items.append({"variant_id": 101, "product_id": 1, "quantity": 1})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{origin}/products/item")
            auditor = ShopifyVariantAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "FAIL"
            assert res.details["checks"]["variant_price"]["status"] == "FAIL"
            assert res.details["checks"]["variant_price"]["displayed_price"] == 15.0
            assert res.details["checks"]["variant_price"]["expected_price"] == 15.99
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 8. Broken image or image present only inside srcset -> FAIL
@pytest.mark.asyncio
async def test_variant_auditor_broken_image_or_srcset():
    cart_items = []

    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Default"]}],
                    "variants": [
                        {"id": 101, "title": "Default", "price": 1599, "options": ["Default"],
                         "featured_image": {"src": "/variant.png"}}
                    ]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"item_count": len(cart_items), "items": cart_items}).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                # Active src is /main.png, but srcset has /variant.png -> FAIL
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Default">Default</option>
                  </select>
                  <span class="price-item--regular">$15.99</span>
                  <img class="product-featured-media" src="/main.png" srcset="/variant.png 2x" width="100" height="100"/>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body: 'id=101'});
                };
                </script></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/cart/add.js":
                cart_items.append({"variant_id": 101, "product_id": 1, "quantity": 1})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{origin}/products/item")
            auditor = ShopifyVariantAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "FAIL"
            assert res.details["checks"]["variant_image"]["status"] == "FAIL"
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 9. Partial or ambiguous option selection -> WARN
@pytest.mark.asyncio
async def test_variant_auditor_partial_ambiguous_options():
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["S", "L"]}, {"name": "Color", "values": ["Red", "Blue"]}],
                    "variants": [
                        {"id": 101, "title": "S / Red", "price": 1599, "options": ["S", "Red"]}
                    ]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"item_count": 0, "items": []}).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                # DOM only renders Size, missing Color option dropdown
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="S">S</option>
                  </select>
                  <span class="price-item--regular">$15.99</span>
                  <button type="submit" name="add">Add to cart</button>
                </form></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{origin}/products/item")
            auditor = ShopifyVariantAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "WARN"
            assert res.details["checks"]["variant_identity"]["status"] == "WARN"
            assert "Partial or ambiguous" in res.details["checks"]["variant_identity"]["summary"]
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 10. Delayed UI update within timeout -> PASS
@pytest.mark.asyncio
async def test_variant_auditor_delayed_ui_update(svg_image):
    cart_items = []

    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small", "Large"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "options": ["Small"]},
                        {"id": 102, "title": "Large", "price": 2500, "options": ["Large"]}
                    ]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"item_count": len(cart_items), "items": cart_items}).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Small">Small</option>
                    <option value="Large">Large</option>
                  </select>
                  <span class="price-item--regular">$15.99</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                const select = document.querySelector('select');
                const price = document.querySelector('.price-item--regular');
                select.onchange = () => {
                  // Delayed update after 300ms
                  setTimeout(() => {
                    if (select.value === 'Large') price.textContent = '$25.00';
                  }, 300);
                };
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body: 'id=102'});
                };
                </script></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

        def do_POST(self):
            if self.path == "/cart/add.js":
                cart_items.append({"variant_id": 102, "product_id": 1, "quantity": 1})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{origin}/products/item")
            auditor = ShopifyVariantAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.details["checks"]["variant_price"]["status"] == "PASS"
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 11. Selecting unavailable variant without any add-to-cart click -> PASS
@pytest.mark.asyncio
async def test_variant_auditor_unavailable_variant_no_click():
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small", "Large"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]},
                        {"id": 102, "title": "Large", "price": 2500, "available": False, "options": ["Large"]}
                    ]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"item_count": 0, "items": []}).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Small">Small</option>
                    <option value="Large">Large</option>
                  </select>
                  <span class="price-item--regular">$15.99</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                const select = document.querySelector('select');
                const btn = document.querySelector('button');
                select.onchange = () => {
                  if (select.value === 'Large') {
                    btn.disabled = true;
                    btn.textContent = 'Sold Out';
                  }
                };
                    document.querySelector('form').onsubmit = async (e) => {
                      e.preventDefault();
                      await fetch('/cart/add.js', {method:'POST', body: 'id=101'});
                    };
                </script></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{origin}/products/item")
            auditor = ShopifyVariantAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.details["checks"]["variant_availability_display"]["status"] == "PASS"
            assert "Sold Out" in res.details["checks"]["variant_availability_display"]["summary"] or "disabled" in res.details["checks"]["variant_availability_display"]["summary"]
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 12. Unresolved pricing context -> WARN
@pytest.mark.asyncio
async def test_variant_auditor_ambiguous_pricing():
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Default"]}],
                    "variants": [
                        {"id": 101, "title": "Default", "price": 1599, "options": ["Default"]}
                    ]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"item_count": 0, "items": []}).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                # Conflicting prices displayed without active pricing selector
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$15.99</span>
                  <span class="price">$12.00</span>
                  <button type="submit" name="add">Add to cart</button>
                </form></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{origin}/products/item")
            auditor = ShopifyVariantAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "WARN"
            assert res.details["checks"]["variant_price"]["status"] == "WARN"
            assert "ambiguous" in res.details["checks"]["variant_price"]["summary"].lower()
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 13. Preserving default skill behavior when --skill is omitted in CLI
def test_cli_default_skills_exclude_variant_auditor():
    """Verify that running CLI without --skill does not run shopify_variant_auditor by default."""
    from nano_sre.cli import _build_skills, _resolve_skill_names
    from nano_sre.config.settings import Settings

    settings = Settings(store_url="https://example.com")
    skills = _build_skills(settings, update_baseline=False)

    # When no --skill option is provided (requested = None)
    resolved = _resolve_skill_names(None, skills.keys())
    assert "shopify_variant_auditor" not in resolved
    assert "shopify_shopper" in resolved
    assert "shopify_doctor" in resolved
