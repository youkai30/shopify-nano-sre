"""Comprehensive test suite for ShopifyVariantAuditor covering all Phase 2 requirements and regression cases."""

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

from nano_sre.skills.shopify_variant_auditor import ShopifyVariantAuditor, parse_price_cents


def test_parse_price_cents_cases():
    """Unit tests for strict integer cents price parsing logic using Decimal."""
    assert parse_price_cents("$1,500.00") == 150000
    assert parse_price_cents("1,500.00 €") == 150000
    assert parse_price_cents("$15.99") == 1599
    assert parse_price_cents("15.99") == 1599
    assert parse_price_cents("$16.00") == 1600
    assert parse_price_cents("$15") == 1500
    assert parse_price_cents("1.500,00 €") == 150000
    assert parse_price_cents("15,99 €") == 1599
    assert parse_price_cents(None) is None
    assert parse_price_cents("No price here") is None


def make_test_server(handler_factory):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture
def svg_image():
    return '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="red"/></svg>'


# 1. Non-default variant selection + valid ATC -> PASS
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


# Regression 1: Displayed 15.99 vs Expected 16.00 returns FAIL
@pytest.mark.asyncio
async def test_price_1599_vs_1600_returns_fail():
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
                    "variants": [{"id": 101, "title": "Default", "price": 1600, "options": ["Default"]}]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                data = {"item_count": sum(i["quantity"] for i in cart_items), "items": cart_items}
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]"><option value="Default">Default</option></select>
                  <span class="price-item--regular">$15.99</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=101'});
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
            assert res.details["checks"]["variant_price"]["displayed_cents"] == 1599
            assert res.details["checks"]["variant_price"]["expected_cents"] == 1600
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# Regression 2: Strict product-form scoping (missing main form + recommendation card -> WARN)
@pytest.mark.asyncio
async def test_missing_main_form_with_unrelated_button_returns_warn():
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Default"]}],
                    "variants": [{"id": 101, "title": "Default", "price": 2500, "options": ["Default"]}]
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
                # No product form, only recommendation card button!
                html = """<!doctype html><html><body>
                <div class="recommendations">
                  <span class="price-item--regular">$25.00</span>
                  <button type="button" class="add-to-cart">Add recommendation</button>
                </div></body></html>"""
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
            assert "Could not establish main product form binding" in res.summary
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# Regression 3: Tight Cart Payload Validation
@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_cart_payload", [
    {"item_count": True, "items": []}, # bool item_count
    {"item_count": 1, "items": [{"variant_id": True, "quantity": 1}]}, # bool variant_id
    {"item_count": 1, "items": [{"variant_id": 101, "quantity": True}]}, # bool quantity
    {"item_count": 1, "items": [{"variant_id": 101, "quantity": 1, "selling_plan_id": True}]}, # bool selling_plan_id
    {"item_count": 1, "items": ["invalid_string_item"]}, # non-dict item
    {"item_count": 5, "items": [{"variant_id": 101, "quantity": 1}]}, # inconsistent item count (5 != 1)
])
async def test_invalid_cart_payload_returns_warn(invalid_cart_payload):
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Default"]}],
                    "variants": [{"id": 101, "title": "Default", "price": 1599, "options": ["Default"]}]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(invalid_cart_payload).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]"><option value="Default">Default</option></select>
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
            assert "inconsistent_payload" in res.details["checks"]["variant_identity"]["meta_before"]["status"]
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# Regression 4: Precise Add to Cart Request Matching (Cross-origin & Misleading paths)
@pytest.mark.asyncio
async def test_precise_request_matching():
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
                    "variants": [{"id": 101, "title": "Default", "price": 1599, "options": ["Default"]}]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                data = {"item_count": sum(i["quantity"] for i in cart_items), "items": cart_items}
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]"><option value="Default">Default</option></select>
                  <span class="price-item--regular">$15.99</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  // Fire a misleading path first
                  fetch('/cart/add_recommendation', {method:'POST', body:'id=999'});
                  // Fire real ATC path
                  await fetch('/cart/add.js', {method:'POST', body:'id=101'});
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
            else:
                self.send_response(200)
                self.end_headers()

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
            assert res.details["checks"]["variant_identity"]["expected_variant_id"] == 101
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# Preservation of legacy CLI command behavior
def test_cli_default_skills_exclude_variant_auditor():
    """Verify that running CLI without --skill does not run shopify_variant_auditor by default."""
    from nano_sre.cli import _build_skills, _resolve_skill_names
    from nano_sre.config.settings import Settings

    settings = Settings(store_url="https://example.com")
    skills = _build_skills(settings, update_baseline=False)

    resolved = _resolve_skill_names(None, skills.keys())
    assert "shopify_variant_auditor" not in resolved
    assert "shopify_shopper" in resolved
    assert "shopify_doctor" in resolved
