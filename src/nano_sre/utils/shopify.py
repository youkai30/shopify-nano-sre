"""Shopify specific utilities for Nano-SRE."""

import logging

from playwright.async_api import Page

logger = logging.getLogger(__name__)


async def bypass_shopify_password(page: Page, password: str) -> bool:
    """
    Attempt to bypass Shopify password protection page if present.

    Returns:
        True if password was entered (or not needed), False if it failed.
    """
    try:
        # Check if we are on a password page
        # Elements to look for: entry form, password input, or "Enter store using password" link

        # Shopify often has a "Enter store using password" button first
        enter_link = page.get_by_role("link", name="Enter store using password")
        if await enter_link.is_visible():
            await enter_link.click()

        password_input = page.locator('input[type="password"], input[name="password"]')
        if await password_input.is_visible():
            logger.info("Shopify password page detected, entering password...")
            await password_input.fill(password)
            await password_input.press("Enter")
            await page.wait_for_load_state("domcontentloaded")
            return True

        return False
    except Exception as e:
        logger.warning(f"Failed to bypass Shopify password: {e}")
        return False
