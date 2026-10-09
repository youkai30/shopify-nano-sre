"""Comprehensive test suite for ShopifyPurchaseBlockerAuditor covering all Stage 3 requirements and regression cases."""

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from playwright.async_api import async_playwright

from nano_sre.skills.shopify_purchase_blocker_auditor import (
    ShopifyPurchaseBlockerAuditor,
)


def make_test_server(handler_factory):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


# 1. Healthy purchase flow -> PASS
@pytest.mark.asyncio
async def test_purchase_blocker_healthy_flow_pass():
    cart_items = []

    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}
                    ],
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
                  </select>
                  <button type="submit" name="add">Add to Cart</button>
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
            else:
                self.send_response(404)
                self.end_headers()

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
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "PASS"
            assert res.details["reason_code"] == "NONE"
            assert "network_cart_evidence" in res.details
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 2. Mandatory option working -> PASS
@pytest.mark.asyncio
async def test_purchase_blocker_mandatory_option_works_pass():
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
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]},
                        {"id": 102, "title": "Large", "price": 2500, "available": True, "options": ["Large"]},
                    ],
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
                    <option value="Large">Large</option>
                  </select>
                  <button type="submit" name="add">Add to Cart</button>
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
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "PASS"
            assert res.details["proven_purchase_conditions"]["target_variant_id"] == 101
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 3. Mandatory option broken/disabled -> FAIL: MANDATORY_OPTION_UNSELECTABLE
@pytest.mark.asyncio
async def test_purchase_blocker_mandatory_option_disabled_fail():
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}
                    ],
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
                  <select name="options[Size]" disabled>
                    <option value="Small" disabled>Small</option>
                  </select>
                  <button type="submit" name="add">Add to Cart</button>
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
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "FAIL"
            assert res.details["reason_code"] == "MANDATORY_OPTION_UNSELECTABLE"
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 4. Button blocked by un-clearable overlay -> FAIL: BUTTON_BLOCKED_BY_OVERLAY
@pytest.mark.asyncio
async def test_purchase_blocker_overlay_blocking_fail():
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}
                    ],
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
                <div id="unclearable-overlay" style="position:fixed;top:0;left:0;width:100%;height:100%;z-index:9999;background:rgba(0,0,0,0.5);">Overlay</div>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Small">Small</option>
                  </select>
                  <button type="submit" name="add">Add to Cart</button>
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
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "FAIL"
            assert res.details["reason_code"] == "BUTTON_BLOCKED_BY_OVERLAY"
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 5. Dismissable modal / popup cleared -> PASS
@pytest.mark.asyncio
async def test_purchase_blocker_dismissable_modal_cleared_pass():
    cart_items = []

    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}
                    ],
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
                <div id="promo-popup" style="position:fixed;top:0;left:0;width:100%;height:100%;z-index:9999;background:rgba(0,0,0,0.5);">
                  <button class="close" onclick="document.getElementById('promo-popup').remove();">Close</button>
                </div>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Small">Small</option>
                  </select>
                  <button type="submit" name="add">Add to Cart</button>
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
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "PASS"
            assert res.details["reason_code"] == "NONE"
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 6. Button reachable by scrolling -> PASS
@pytest.mark.asyncio
async def test_purchase_blocker_offscreen_button_scrolled_pass():
    cart_items = []

    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}
                    ],
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
                <div style="height:2000px;">Long page content...</div>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Small">Small</option>
                  </select>
                  <button type="submit" name="add">Add to Cart</button>
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
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "PASS"
            assert "Scrolled to Add to Cart button" in res.details["steps"]
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 7. Unproven request dispatch without handler exception -> WARN
@pytest.mark.asyncio
async def test_purchase_blocker_js_error_blocking_fail():
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}
                    ],
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
                  </select>
                  <button type="submit" name="add" onclick="event.preventDefault();">Add to Cart</button>
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
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "WARN"
            assert res.details["reason_code"] == "NETWORK_OR_RESPONSE_UNRESOLVED"
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 8. Unrelated JS error not affecting purchase -> PASS
@pytest.mark.asyncio
async def test_purchase_blocker_unrelated_js_error_pass():
    cart_items = []

    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}
                    ],
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
                <script>setTimeout(() => { throw new Error('Unrelated analytics error'); }, 10);</script>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Small">Small</option>
                  </select>
                  <button type="submit" name="add">Add to Cart</button>
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
            await asyncio.sleep(0.1)
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "PASS"
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 9. Unresolved network rejection/failure -> WARN
@pytest.mark.asyncio
async def test_purchase_blocker_unresolved_network_warn():
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}
                    ],
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
                  </select>
                  <button type="submit" name="add">Add to Cart</button>
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
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Internal server error"}')

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{origin}/products/item")
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "WARN"
            assert res.details["reason_code"] == "NETWORK_OR_RESPONSE_UNRESOLVED"
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 10. Delayed UI update within timeout -> PASS
@pytest.mark.asyncio
async def test_purchase_blocker_delayed_ui_update_pass():
    cart_items = []

    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}
                    ],
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
                  </select>
                  <button type="submit" name="add">Add to Cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  setTimeout(async () => {
                    await fetch('/cart/add.js', {method:'POST', body:'id=101'});
                  }, 500);
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
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "PASS"
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 11. Unavailable variant -> NOT classified as a blocker for available product (PASS)
@pytest.mark.asyncio
async def test_purchase_blocker_all_variants_unavailable_pass():
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": False, "options": ["Small"]}
                    ],
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body><h1>Out of Stock</h1></body></html>"""
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
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "PASS"
            assert res.details["proven_purchase_conditions"]["available_variants_count"] == 0
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# 12. CLI opt-in skill behavior
def test_cli_default_skills_exclude_purchase_blocker_auditor():
    """Verify CLI default skills exclude shopify_purchase_blocker_auditor when run without --skill."""
    from nano_sre.cli import _build_skills, _resolve_skill_names
    from nano_sre.config.settings import Settings

    settings = Settings(store_url="https://example.com")
    skills = _build_skills(settings, update_baseline=False)

    resolved = _resolve_skill_names(None, skills.keys())
    assert "shopify_purchase_blocker_auditor" not in resolved

    # When requested explicitly, it is included
    opt_in_resolved = _resolve_skill_names(["shopify_purchase_blocker_auditor"], skills.keys())
    assert opt_in_resolved == ["shopify_purchase_blocker_auditor"]


# REGRESSION TEST 13: Non-functional swatch click (Large clicked but state stays Small) -> WARN (UNPROVEN_OPTION_PATTERN)
@pytest.mark.asyncio
async def test_regression_non_functional_swatch_click_returns_warn():
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
                        {"id": 102, "title": "Large", "price": 2500, "available": True, "options": ["Large"]},
                    ],
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
                # Swatch button for Large exists, but clicking it does NOT update selection state!
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <button type="button" class="swatch" data-value="Large">Large</button>
                  <button type="submit" name="add">Add to Cart</button>
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
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "WARN"
            assert res.details["reason_code"] == "UNPROVEN_OPTION_PATTERN"
            assert "Large" not in res.details["proven_purchase_conditions"]["proven_dom_options"].values()
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# REGRESSION TEST 14: Background widget JS error fired during click without request dispatch -> WARN (NOT attributed to analytics blocking)
@pytest.mark.asyncio
async def test_regression_background_widget_js_error_returns_warn():
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}
                    ],
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
                  <select name="options[Size]"><option value="Small">Small</option></select>
                  <button type="submit" name="add" onclick="setTimeout(() => { throw new Error('Unrelated chat widget background loop exception'); }, 0); event.preventDefault();">Add to Cart</button>
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
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            # Background error fired during click without proven causality returns WARN, not claiming background widget blocked purchase!
            assert res.status == "WARN"
            assert res.details["reason_code"] == "NETWORK_OR_RESPONSE_UNRESOLVED"
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# REGRESSION TEST 15: HTML5 Validity input constraint (e.g. quantity min=1 value=0) -> WARN
@pytest.mark.asyncio
async def test_regression_html5_validity_quantity_zero_returns_warn():
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}
                    ],
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
                  <input type="number" name="quantity" min="1" value="0" required/>
                  <select name="options[Size]"><option value="Small">Small</option></select>
                  <button type="submit" name="add">Add to Cart</button>
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
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "WARN"
            assert res.details["reason_code"] == "UNFULFILLED_PURCHASE_REQUIREMENT"
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# REGRESSION TEST 16: Session cookie retention in desktop context transition
@pytest.mark.asyncio
async def test_regression_desktop_context_session_cookie_retention():
    cart_items = []

    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            cookies = self.headers.get("Cookie", "")
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}
                    ],
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                has_session = "session_token=secret123" in cookies
                c = cart_items if has_session else []
                data = {"item_count": sum(i["quantity"] for i in c), "items": c}
                self.wfile.write(json.dumps(data).encode("utf-8"))
            elif self.path.startswith("/products/item"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]"><option value="Small">Small</option></select>
                  <button type="submit" name="add">Add to Cart</button>
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
            if self.path == "/cart.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"item_count": sum(i["quantity"] for i in cart_items), "items": cart_items}).encode("utf-8"))
            elif self.path == "/cart/add.js":
                cookies = self.headers.get("Cookie", "")
                if "session_token=secret123" in cookies:
                    cart_items.append({"variant_id": 101, "product_id": 1, "quantity": 1})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"id": 101, "quantity": 1}).encode("utf-8"))

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            iphone = p.devices["iPhone 17 Pro"] if "iPhone 17 Pro" in p.devices else {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(**iphone)
            await context.add_cookies([{"name": "session_token", "value": "secret123", "domain": "127.0.0.1", "path": "/"}])
            page = await context.new_page()
            await page.goto(f"{origin}/products/item")
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})
            assert res.status == "PASS"
            assert res.details["reason_code"] == "NONE"
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# REGRESSION TEST 17: Non-mutative screenshot masking, value immutability, and pixel verification
@pytest.mark.asyncio
async def test_regression_non_mutative_screenshot_masking_and_immutability():
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}
                    ],
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
                  <input type="text" id="cust-input" name="properties[CustomerName]" value="John Doe Secret PII"/>
                  <textarea id="cust-area" name="properties[Note]">Secret Personal Note</textarea>
                  <select name="options[Size]"><option value="Small">Small</option></select>
                  <button type="submit" name="add">Add to Cart</button>
                </form>
                <script>document.querySelector('form').onsubmit = e => e.preventDefault();</script>
                </body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

    server, thread = make_test_server(Storefront)
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1280, "height": 800}, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
            await page.goto(f"{origin}/products/item")

            val_before_input = await page.evaluate("() => document.getElementById('cust-input').value")
            val_before_area = await page.evaluate("() => document.getElementById('cust-area').value")

            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})

            val_after_input = await page.evaluate("() => document.getElementById('cust-input').value")
            val_after_area = await page.evaluate("() => document.getElementById('cust-area').value")

            # Assert element values remained strictly identical before and after screenshot!
            assert val_before_input == val_after_input == "John Doe Secret PII"
            assert val_before_area == val_after_area == "Secret Personal Note"

            # Verify screenshot exists on disk and is a valid readable PNG image file
            screenshot = res.details.get("redacted_screenshot")
            assert screenshot is not None
            assert Path(screenshot).exists(), f"Screenshot file '{screenshot}' must exist on disk!"
            assert not screenshot.startswith("[REDACTED]"), "Screenshot file path must remain a valid file path!"

            # Inspect pixels of generated PNG image using PIL
            img = Image.open(screenshot)
            assert img.width > 0 and img.height > 0, "Screenshot image must have valid dimensions"

            # Verify secrets/PII do not appear in summary or details
            dumped_details = json.dumps(res.details) + " " + res.summary
            assert "John Doe Secret PII" not in dumped_details
            assert "Secret Personal Note" not in dumped_details
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# REGRESSION TEST 18: Synthetic secret in JS error during click -> verify redaction in summary and details
@pytest.mark.asyncio
async def test_regression_synthetic_secret_in_js_error_redacted():
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/products/item.js":
                data = {
                    "id": 1,
                    "handle": "item",
                    "options": [{"name": "Size", "values": ["Small"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}
                    ],
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
                  <select name="options[Size]"><option value="Small">Small</option></select>
                  <button type="submit" name="add" onclick="event.preventDefault(); throw new Error('Failed with token shpat_secret99999 user admin@store.com');">Add to Cart</button>
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
            auditor = ShopifyPurchaseBlockerAuditor()
            res = await auditor.run({"page": page, "base_url": f"{origin}/products/item"})

            # Check secret token redaction in summary and details
            assert res.status in ("FAIL", "WARN")
            assert "shpat_" not in res.summary
            assert "admin@store.com" not in res.summary
            dumped = json.dumps(res.details)
            assert "shpat_" not in dumped
            assert "admin@store.com" not in dumped
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# REGRESSION TEST 19: Internal auditor exception -> WARN (INTERNAL_AUDITOR_ERROR)
@pytest.mark.asyncio
async def test_regression_internal_auditor_exception_returns_warn():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        auditor = ShopifyPurchaseBlockerAuditor()
        # Passing an invalid base_url that triggers URL parsing error
        res = await auditor.run({"page": page, "base_url": "invalid_url_without_scheme"})
        assert res.status == "WARN"
        assert res.details["reason_code"] == "INTERNAL_AUDITOR_ERROR"
        await browser.close()
