"""Shopify specific utilities for Nano-SRE."""

import logging
import re
from typing import Any, Optional, Tuple, Dict
from urllib.parse import urlparse

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


EXCLUDED_KEYWORDS = [
    "gift", "voucher", "e-gift", "egift", "card",
    "shipping", "repair", "service", "sample", "custom-box", "custom box"
]


def is_excluded_product(product: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Determine if a product dictionary should be excluded from selection as a representative product.
    Returns (is_excluded, reason).
    """
    handle = str(product.get("handle", "")).lower()
    product_type = str(product.get("product_type", "")).lower()
    title = str(product.get("title", "")).lower()
    tags_raw = product.get("tags", [])
    if isinstance(tags_raw, list):
        tags = [str(t).lower() for t in tags_raw]
    else:
        tags = [str(tags_raw).lower()]

    for kw in EXCLUDED_KEYWORDS:
        if kw in handle:
            return True, f"Handle contains excluded keyword '{kw}'"
        if kw in product_type:
            return True, f"Product type contains excluded keyword '{kw}'"
        if kw in title:
            return True, f"Title contains excluded keyword '{kw}'"
        if any(kw in tag for tag in tags):
            return True, f"Tag contains excluded keyword '{kw}'"

    variants = product.get("variants", [])
    if not variants or not isinstance(variants, list):
        return True, "Product has no variants"

    available_variants = [v for v in variants if isinstance(v, dict) and v.get("available", True)]
    if not available_variants:
        return True, "Product has no available variants"

    non_zero_variants = [
        v for v in available_variants
        if float(v.get("price", 0) or 0) > 0
    ]
    if not non_zero_variants:
        return True, "Product price is zero"

    return False, ""


async def pick_representative_product(
    page: Any,
    origin: str,
    locale: str,
    base_url: str,
    prefer_multi_variant: bool = True
) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Shared representative product picker.
    Fetches /products.json?limit=50 via page.request, falls back to DOM product links,
    filters out gift cards/services/free items, and selects a deterministic lead candidate.

    Returns:
        (chosen_handle, selection_metadata)
    """
    candidates_considered = []
    exclusions = []
    eligible_multi = []
    eligible_single = []

    json_url = f"{origin}{locale}/products.json?limit=50"
    products = []
    try:
        res = await page.request.get(json_url, timeout=15000)
        if res.ok:
            data = await res.json()
            if isinstance(data, dict):
                products = data.get("products", [])
    except Exception as e:
        logger.debug(f"Failed fetching {json_url}: {e}")

    if products:
        for p in products:
            handle = p.get("handle")
            if not handle:
                continue
            candidates_considered.append(handle)
            excluded, reason = is_excluded_product(p)
            if excluded:
                exclusions.append({"handle": handle, "reason": reason})
            else:
                variants = [v for v in p.get("variants", []) if v.get("available", True)]
                if len(variants) > 1:
                    eligible_multi.append(p)
                else:
                    eligible_single.append(p)
    else:
        # Fallback to DOM product links if /products.json failed or returned empty
        try:
            links = page.locator('a[href*="/products/"]')
            count = await links.count()
            seen_handles = set()
            for i in range(min(count, 30)):
                href = await links.nth(i).get_attribute("href")
                if href:
                    match = re.search(r"/products/([a-zA-Z0-9_-]+)", href)
                    if match:
                        h = match.group(1)
                        if h not in seen_handles:
                            seen_handles.add(h)
                            candidates_considered.append(h)
                            dummy_prod = {
                                "handle": h,
                                "title": h,
                                "product_type": "",
                                "tags": [],
                                "variants": [{"id": 1, "available": True, "price": 10.0}]
                            }
                            excluded, reason = is_excluded_product(dummy_prod)
                            if excluded:
                                exclusions.append({"handle": h, "reason": reason})
                            else:
                                eligible_single.append(dummy_prod)
        except Exception as e:
            logger.debug(f"DOM link fallback failed: {e}")

    chosen_product = None
    selection_reason = ""

    if prefer_multi_variant:
        if eligible_multi:
            chosen_product = eligible_multi[0]
            selection_reason = "First eligible multi-variant product"
        elif eligible_single:
            chosen_product = eligible_single[0]
            selection_reason = "First eligible single-variant product (no multi-variant available)"
    else:
        if eligible_single:
            chosen_product = eligible_single[0]
            selection_reason = "First eligible single-variant product"
        elif eligible_multi:
            chosen_product = eligible_multi[0]
            selection_reason = "First eligible multi-variant product (no single-variant available)"

    chosen_handle = chosen_product.get("handle") if chosen_product else None

    metadata = {
        "candidates_considered": candidates_considered,
        "exclusions": exclusions,
        "eligible_multi_count": len(eligible_multi),
        "eligible_single_count": len(eligible_single),
        "chosen_handle": chosen_handle,
        "selection_reason": selection_reason,
    }

    return chosen_handle, metadata
