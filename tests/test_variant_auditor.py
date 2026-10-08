"""Unit and integration tests for ShopifyVariantAuditor skill."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.async_api import async_playwright

from nano_sre.skills.variant_auditor import ShopifyVariantAuditor


class DummyVariantServer(BaseHTTPRequestHandler):
    mode = "pass"

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
        if self.path == "/products/item.js":
            data = {
                "id": 1,
                "title": "Test Product",
                "images": [
                    {"id": 10, "src": "http://127.0.0.1/products/red.jpg"},
                    {"id": 20, "src": "http://127.0.0.1/products/blue.jpg"}
                ],
                "variants": [
                    {"id": 101, "title": "Red", "price": 1000, "available": True, "featured_image": {"id": 10, "src": "http://127.0.0.1/products/red.jpg"}},
                        {"id": 102, "title": "Blue", "price": 1500, "available": True, "featured_image": {"id": 20, "src": "http://127.0.0.1/products/blue.jpg"}},
                        {"id": 103, "title": "Green", "price": 1500, "available": False if self.mode in ("pass", "avail_fail") else True, "featured_image": {"id": 20, "src": "http://127.0.0.1/products/blue.jpg"}}
                ]
            }
            self.reply(200, json.dumps(data), "application/json")
        elif self.path.startswith("/products/item"):
            price = "10.00" if self.mode == "price_fail" else "15.00"
            img = "red.jpg" if self.mode == "image_fail" else "blue.jpg"
            var_id = "101" if self.mode == "identity_fail" else "102"
            dis = ""
            txt = "ADD TO CART"
            body = f"""<!doctype html><html><body>
              <form action="/cart/add" method="post">
                <input type="hidden" name="id" value="{var_id}">
                <button type="button" class="swatch" data-option-value="Blue">Blue</button>
                <button type="button" class="swatch" data-option-value="Green">Green</button>
                <span class="price">${price}</span>
                <img class="product-single__photo" src="http://127.0.0.1/products/{img}">
                <button type="submit" name="add" {dis}>{txt}</button>
              </form>
              <script>
              document.querySelector('.swatch[data-option-value="Green"]').onclick=()=>{{
                const btn = document.querySelector('button[name="add"]');
                if ("{self.mode}" === "avail_fail") {{
                  btn.removeAttribute("disabled");
                  btn.innerText = "ADD TO CART";
                }} else {{
                  btn.setAttribute("disabled", "true");
                  btn.innerText = "SOLD OUT";
                }}
              }};
              document.querySelector('form').onsubmit=async(e)=>{{
                e.preventDefault();
                await fetch('/cart/add.js',{{method:'POST', body:'id={var_id}'}});
              }};
              </script></body></html>"""
            self.reply(200, body)
        elif self.path == "/cart.js":
            v_id = 101 if self.mode == "identity_fail" else 102
            self.reply(200, json.dumps({"item_count": 1, "items": [{"variant_id": v_id, "id": v_id, "quantity": 1}]}), "application/json")
        else:
            self.reply(404, "missing", "text/plain")

    def do_POST(self):
        self.reply(200, "{}", "application/json")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_variant_auditor_pass_scenario():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DummyVariantServer)
    server.RequestHandlerClass.mode = "pass"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            result = await ShopifyVariantAuditor().run({"page": page, "base_url": origin + "/products/item"})
            await context.close()
            await browser.close()
        assert result.status == "PASS"
        assert result.details["findings"]["identity"]["status"] == "PASS"
        assert result.details["findings"]["price"]["status"] == "PASS"
        assert result.details["findings"]["image"]["status"] == "PASS"
        assert result.details["findings"]["availability"]["status"] == "PASS"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_variant_auditor_identity_fail_scenario():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DummyVariantServer)
    server.RequestHandlerClass.mode = "identity_fail"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            result = await ShopifyVariantAuditor().run({"page": page, "base_url": origin + "/products/item"})
            await context.close()
            await browser.close()
        assert result.status == "FAIL"
        assert result.details["findings"]["identity"]["status"] == "FAIL"
        assert result.details["findings"]["identity"]["reason_code"] == "form_id_mismatch"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_variant_auditor_price_fail_scenario():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DummyVariantServer)
    server.RequestHandlerClass.mode = "price_fail"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            result = await ShopifyVariantAuditor().run({"page": page, "base_url": origin + "/products/item"})
            await context.close()
            await browser.close()
        assert result.status == "FAIL"
        assert result.details["findings"]["price"]["status"] == "FAIL"
        assert result.details["findings"]["price"]["reason_code"] == "stale_variant_price"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_variant_auditor_image_fail_scenario():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DummyVariantServer)
    server.RequestHandlerClass.mode = "image_fail"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            result = await ShopifyVariantAuditor().run({"page": page, "base_url": origin + "/products/item"})
            await context.close()
            await browser.close()
        assert result.status == "FAIL"
        assert result.details["findings"]["image"]["status"] == "FAIL"
        assert result.details["findings"]["image"]["reason_code"] == "stale_variant_image"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_variant_auditor_availability_fail_scenario():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DummyVariantServer)
    server.RequestHandlerClass.mode = "avail_fail"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            result = await ShopifyVariantAuditor().run({"page": page, "base_url": origin + "/products/item"})
            await context.close()
            await browser.close()
        assert result.status == "FAIL"
        assert result.details["findings"]["availability"]["status"] == "FAIL"
        assert result.details["findings"]["availability"]["reason_code"] == "sold_out_variant_enabled"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
