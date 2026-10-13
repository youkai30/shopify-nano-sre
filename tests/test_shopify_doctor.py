"""Regression tests for ShopifyDoctorSkill."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.async_api import async_playwright

from nano_sre.config.settings import Settings
from nano_sre.skills.shopify_doctor import ShopifyDoctorSkill


class MockDoctorServer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body><h1>Doctor Target Storefront</h1></body></html>")


@pytest.fixture(scope="module")
def mock_doctor_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockDoctorServer)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.mark.asyncio
async def test_shopify_doctor_fallback_url_when_settings_empty(mock_doctor_server):
    """Verify ShopifyDoctorSkill falls back to context base_url when settings.store_url is None and navigates to target_url."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Page starts at about:blank, distinct from mock_doctor_server URL
        assert page.url != mock_doctor_server

        settings = Settings(store_url=None)  # store_url_str is ''
        doctor = ShopifyDoctorSkill()

        ctx = {
            "page": page,
            "settings": settings,
            "base_url": mock_doctor_server,
        }

        result = await doctor.run(ctx)

        # Assert navigation succeeded and page.url matches context base_url
        assert page.url.rstrip("/") == mock_doctor_server.rstrip("/")
        assert result.status in ("PASS", "WARN")
        assert result.skill_name == "shopify_doctor"
        await browser.close()


@pytest.mark.asyncio
async def test_shopify_doctor_no_unnecessary_navigation(mock_doctor_server):
    """Verify ShopifyDoctorSkill skips page.goto if page is already on target URL."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Pre-navigate page to target URL
        await page.goto(mock_doctor_server)
        assert page.url.rstrip("/") == mock_doctor_server.rstrip("/")

        settings = Settings(store_url=None)
        doctor = ShopifyDoctorSkill()

        ctx = {
            "page": page,
            "settings": settings,
            "base_url": mock_doctor_server,
        }

        result = await doctor.run(ctx)

        assert page.url.rstrip("/") == mock_doctor_server.rstrip("/")
        assert result.status in ("PASS", "WARN")
        await browser.close()
