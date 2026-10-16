"""Real-Chromium tests: popups are dismissed, real blockers are still reported."""

import pytest
from playwright.async_api import async_playwright

from nano_sre.skills.shopify_purchase_blocker_auditor import ShopifyPurchaseBlockerAuditor
from nano_sre.utils.shopify import click_with_overlay_retry

KLAVIYO_PAGE = """
<button id="atc" onclick="window.clicked=true">Add to cart</button>
<div data-kl-scroll-locking-modal style="position:fixed;inset:0;background:#fff;z-index:99">
  <input placeholder="email">
  <button id="signup" onclick="window.signup=true">Sign up</button>
  <button aria-label="Close dialog" onclick="this.parentElement.remove()">x</button>
</div>"""

COOKIE_PAGE = """
<button id="atc" onclick="window.clicked=true">Add to cart</button>
<div style="position:fixed;inset:0;background:#eee;z-index:99">
  <button id="onetrust-accept-btn-handler" onclick="this.parentElement.remove()">Accept</button>
</div>"""

BLOCKER_PAGE = """
<button id="atc" onclick="window.clicked=true">Add to cart</button>
<xgeo-widget style="position:fixed;inset:0;background:#fff;z-index:99;display:block">pick country</xgeo-widget>"""


async def _page(browser, html):
    page = await browser.new_page()
    await page.set_content(html)
    return page


async def test_klaviyo_popup_closed_without_pressing_signup():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await _page(browser, KLAVIYO_PAGE)
        info = await click_with_overlay_retry(page, page.locator("#atc"), timeout=1500)
        assert info["recovered"] is True
        assert await page.evaluate("window.clicked") is True
        assert await page.evaluate("window.signup") is None  # Subscribe was never pressed
        await browser.close()


async def test_cookie_banner_accepted():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await _page(browser, COOKIE_PAGE)
        info = await click_with_overlay_retry(page, page.locator("#atc"), timeout=1500)
        assert info["recovered"] is True and await page.evaluate("window.clicked") is True
        await browser.close()


async def test_undismissable_blocker_is_still_reported():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await _page(browser, BLOCKER_PAGE)
        try:
            await click_with_overlay_retry(page, page.locator("#atc"), timeout=1500)
            raised = None
        except Exception as e:
            raised = e
        assert raised is not None
        assert "xgeo-widget" in raised.overlay_info["intercepted_by"]
        assert await page.evaluate("window.clicked") is None  # no force-click happened
        await browser.close()


async def test_blocker_auditor_dismiss_does_not_press_signup():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await _page(browser, KLAVIYO_PAGE)
        await ShopifyPurchaseBlockerAuditor()._dismiss_overlays_if_any(page)
        assert await page.evaluate("window.signup") is None
        await browser.close()
