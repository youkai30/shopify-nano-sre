"""Comprehensive test suite for ShopifyVariantAuditor covering all Phase 2 requirements and accumulated regression cases."""

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

from nano_sre.skills.shopify_variant_auditor import (
    ShopifyVariantAuditor,
    _extract_canonical_filename,
    parse_price_cents,
)


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
    assert parse_price_cents("Save 10% — $25.00") == 2500
    assert parse_price_cents("20% off $50.00") == 5000
    assert parse_price_cents("-$15.99") is None  # Negative price rejected
    assert parse_price_cents("-15.99") is None
    assert parse_price_cents(None) is None
    assert parse_price_cents("No price here") is None


def test_canonical_filename_extraction():
    """Unit tests for Shopify CDN canonical image filename extraction."""
    assert _extract_canonical_filename("https://cdn.shopify.com/blue_100x100.png?v=123") == "blue.png"
    assert _extract_canonical_filename("https://cdn.shopify.com/blue_large.jpg") == "blue.jpg"
    assert _extract_canonical_filename("https://cdn.shopify.com/dark-blue.svg") == "dark-blue.svg"
    assert _extract_canonical_filename("https://cdn.shopify.com/blue.svg") == "blue.svg"
    assert _extract_canonical_filename("blue.svg") != _extract_canonical_filename("dark-blue.svg")


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
            if self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"item_count": sum(i["quantity"] for i in cart_items), "items": cart_items}).encode("utf-8"))
            elif self.path == "/cart/add.js":
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                vid = 102 if "102" in body else 101
                cart_items.append({"variant_id": vid, "product_id": 1, "quantity": 1})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"id": vid, "quantity": 1}).encode("utf-8"))

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


# 2. Inactive button.swatch ignored (only active swatch read)
@pytest.mark.asyncio
async def test_inactive_swatch_button_ignored():
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
                data = {"item_count": sum(i["quantity"] for i in cart_items), "items": cart_items}
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <button type="button" class="swatch active" data-option-name="Size" data-value="Small">Small</button>
                  <button type="button" class="swatch" data-option-name="Size" data-value="Large">Large</button>
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
                self.wfile.write(json.dumps({"id": 101, "quantity": 1}).encode("utf-8"))

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{origin}/products/item")
            auditor = ShopifyVariantAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.details["checks"]["variant_identity"]["expected_variant_id"] == 101
            assert res.details["checks"]["variant_identity"]["status"] == "PASS"
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 3. Literal price container: <div class="price"><s class="price-item--regular">$20.00</s><span class="price-item--sale">$15.00</span></div> -> PASS
@pytest.mark.asyncio
async def test_price_container_with_strikethrough_sale_price():
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
                    "variants": [{"id": 101, "title": "Default", "price": 1500, "options": ["Default"]}] # Expected $15.00
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
                # Literal DOM structure specified in prompt
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]"><option value="Default">Default</option></select>
                  <div class="price">
                    <s class="price-item--regular">$20.00</s>
                    <span class="price-item--sale">$15.00</span>
                  </div>
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
                self.wfile.write(json.dumps({"id": 101, "quantity": 1}).encode("utf-8"))

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
            assert res.details["checks"]["variant_price"]["displayed_cents"] == 1500
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 4. Request declares 102, but response/cart adds 101 -> FAIL with before/after/deltas
@pytest.mark.asyncio
async def test_request_declares_102_cart_adds_101_returns_fail():
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
                data = {"item_count": sum(i["quantity"] for i in cart_items), "items": cart_items}
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Small">Small</option>
                    <option value="Large" selected>Large</option>
                  </select>
                  <span class="price-item--regular">$25.00</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=102'});
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
                self.wfile.write(json.dumps({"id": 101, "quantity": 1}).encode("utf-8"))

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
            assert res.details["checks"]["variant_identity"]["added_variant_id"] == 101
            assert res.details["checks"]["variant_identity"]["expected_variant_id"] == 102
            assert "deltas" in res.details["checks"]["variant_identity"]
            assert "meta_before" in res.details["checks"]["variant_identity"]
            assert "meta_after" in res.details["checks"]["variant_identity"]
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 5. Delayed 8-second response returning HTTP 422 timing out -> WARN with add_response_timeout
@pytest.mark.asyncio
async def test_8s_delayed_response_times_out_returns_warn():
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
                self.wfile.write(json.dumps({"item_count": 0, "items": []}).encode("utf-8"))
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
                # Delay 8 seconds (longer than 5s timeout) then return 422
                import time
                time.sleep(8)
                self.send_response(422)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":422,"message":"Unprocessable Entity"}')

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
            assert "add_response_timeout" in res.details["checks"]["variant_identity"]["summary"]
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
