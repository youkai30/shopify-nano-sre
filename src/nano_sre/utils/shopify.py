"""Shopify specific utilities for Nano-SRE."""

import asyncio
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


# Word-boundary patterns (matched against handle/title/product_type after hyphens and
# underscores are turned into spaces). Plain substrings such as "card" or "gift" would
# wrongly exclude real products (cardigan, hot-sauce-gift-set, tags like "gift").
EXCLUDED_PATTERNS = [
    ("gift card", r"\bgift ?(cards?|certificates?)\b"),
    ("e-gift", r"\be ?gift\b"),
    ("voucher", r"\bvouchers?\b"),
    ("shipping", r"\bshipping\b"),
    ("repair", r"\brepairs?\b"),
    ("service", r"\bservices?\b"),
    ("sample", r"\bsamples?\b"),
    ("donation", r"\bdonations?\b"),
    ("gift wrap", r"\bgift ?wrap(ping)?\b"),
]
# Tags only count when they are exactly one of these (a "gift" tag is common on real products).
EXCLUDED_TAGS = {"gift card", "gift-card", "giftcard", "voucher", "service", "sample", "shipping"}
# Kept for backward compatibility with older imports/tests.
EXCLUDED_KEYWORDS = [kw for kw, _ in EXCLUDED_PATTERNS]
# Build-your-own / personalised products have unusual option UIs: usable, but last choice.
DEPRIORITIZED_PATTERN = re.compile(
    r"\b(custom(ise|ize|ised|ized)?|build your own|personali[sz]ed|bundle builder)\b"
)


def _norm(value: Any) -> str:
    return re.sub(r"[-_/]+", " ", str(value or "").lower())


def is_deprioritized_product(product: Dict[str, Any]) -> bool:
    """True for build-your-own / personalised products (valid, but a poor representative)."""
    text = f"{_norm(product.get('handle'))} {_norm(product.get('title'))}"
    return bool(DEPRIORITIZED_PATTERN.search(text))


def is_excluded_product(product: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Determine if a product dictionary should be excluded from selection as a representative product.
    Returns (is_excluded, reason).
    """
    fields = (
        ("Handle", _norm(product.get("handle"))),
        ("Product type", _norm(product.get("product_type"))),
        ("Title", _norm(product.get("title"))),
    )
    for label, text in fields:
        for kw, pattern in EXCLUDED_PATTERNS:
            if re.search(pattern, text):
                return True, f"{label} contains excluded keyword '{kw}'"

    tags_raw = product.get("tags", [])
    if isinstance(tags_raw, str):
        tags_raw = tags_raw.split(",")
    for tag in tags_raw or []:
        if str(tag).strip().lower() in EXCLUDED_TAGS:
            return True, f"Tag contains excluded keyword '{str(tag).strip().lower()}'"

    variants = product.get("variants", [])
    if not variants or not isinstance(variants, list):
        return True, "Product has no variants"

    available_variants = [v for v in variants if isinstance(v, dict) and v.get("available", True)]
    if not available_variants:
        return True, "Product has no available variants"

    def _price(v: Dict[str, Any]) -> float:
        try:
            return float(v.get("price", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    if not any(_price(v) > 0 for v in available_variants):
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

    eligible_multi.sort(key=is_deprioritized_product)
    eligible_single.sort(key=is_deprioritized_product)
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


# Selectors that only ever target a "close"/"accept" control. Never a generic button:
# popups such as newsletter modals also contain a Subscribe button we must not press.
SAFE_DISMISS_SELECTORS = [
    '[role="dialog"] [aria-label*="close" i]',
    '[role="dialog"] button:has-text("Close")',
    '[aria-modal="true"] [aria-label*="close" i]',
    '[aria-modal="true"] button:has-text("Close")',
    '[data-kl-scroll-locking-modal] [aria-label*="close" i]',
    '[class*="klaviyo" i] [aria-label*="close" i]',
    '[id*="popup" i] button[class*="close" i]',
    '[class*="popup" i] button[class*="close" i]',
    '[class*="modal" i] button[class*="close" i]',
    '#onetrust-accept-btn-handler',
    '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
    '.cookie-banner button:has-text("Accept")',
    'button:has-text("Accept all")',
    'button:has-text("Accept All")',
    'button:has-text("Accept cookies")',
]


async def dismiss_overlays(page: Any) -> list:
    """Try to close ordinary popups/cookie banners. Returns the list of actions taken."""
    actions = []
    try:
        await page.keyboard.press("Escape")
        actions.append("Escape")
        await asyncio.sleep(0.3)
    except Exception:
        pass
    for sel in SAFE_DISMISS_SELECTORS:
        try:
            loc = page.locator(f"{sel}:visible").first
            if await loc.count() > 0:
                await loc.click(timeout=1500)
                actions.append(f"clicked {sel}")
                await asyncio.sleep(0.5)
        except Exception:
            continue
    return actions


def _interceptor_from_error(message: str) -> str:
    m = re.search(r"-\s+(<[^\n]{0,300}?)\s+intercepts pointer events", message)
    return m.group(1)[:300] if m else ""


async def click_with_overlay_retry(page: Any, locator: Any, timeout: int = 10000) -> Dict[str, Any]:
    """
    Click `locator`. If it fails (typically "<x> intercepts pointer events"), dismiss
    ordinary popups and retry once. No force-click: a blocker that survives dismissal
    is a real finding. On final failure the original exception is re-raised with an
    `overlay_info` attribute (interceptor, dismissal actions tried).
    """
    info: Dict[str, Any] = {"intercepted_by": "", "dismissal_actions": [], "recovered": False}
    try:
        await locator.click(timeout=timeout)
        return info
    except Exception as first_error:
        message = str(first_error)
        info["intercepted_by"] = _interceptor_from_error(message)
        info["first_error"] = message[:300]
        info["dismissal_actions"] = await dismiss_overlays(page)
        try:
            await locator.click(timeout=min(timeout, 5000))
            info["recovered"] = True
            return info
        except Exception as second_error:
            info["final_error"] = str(second_error)[:300]
            info["intercepted_by"] = info["intercepted_by"] or _interceptor_from_error(str(second_error))
            second_error.overlay_info = info  # type: ignore[attr-defined]
            raise
