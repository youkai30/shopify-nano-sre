"""Shopify Shopper Skill - Simulates a real user journey through the store."""

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import unquote, urljoin, urlparse
from typing import Any

from nano_sre.agent.core import Skill, SkillResult

logger = logging.getLogger(__name__)


class ShopifyShopper(Skill):

    def name(self) -> str:
        return "shopify_shopper"

    async def run(self, context: dict[str, Any]) -> SkillResult:
        page = context.get("page")
        base_url: str = context.get("base_url", "")
        steps = []
        cart_evidence: list[dict[str, Any]] = []
        cart_evidence_meta: dict[str, Any] = _empty_cart_meta()
        cart_collector = _CartEvidenceCollector(cart_evidence, cart_evidence_meta)
        selected_product_url = None
        item_count = None
        cart_verified = False

        if not page or not base_url:
            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary="Missing page or base_url in context",
                details={"steps": steps, "cart_evidence": cart_evidence,
                         "cart_evidence_meta": cart_evidence_meta},
            )

        try:
            # 1. Home
            await page.goto(
                base_url,
                wait_until="commit",
                timeout=60000,
            )
            steps.append("Visited Home Page")

            # 2. Determine product source
            if "/products/" in base_url.lower():
                # The supplied URL is already a product page.
                product_urls = [base_url]
                steps.append("Using supplied product page")

            else:
                # Store homepage: discover products from the new-in collection.
                collection_url = (
                    f"{base_url.rstrip('/')}/collections/new-in"
                )

                await page.goto(
                    collection_url,
                    wait_until="commit",
                    timeout=60000,
                )

                steps.append("Visited Product Collection")

                # Give client-side rendered product links a moment to appear.
                await asyncio.sleep(2)

                product_links = page.locator(
                    'a[href*="/products/"]'
                )

                excluded = (
                    "gift-card",
                    "gift_card",
                    "giftcard",
                    "donation",
                )

                count = await product_links.count()
                product_urls = []

                for i in range(min(count, 30)):
                    try:
                        href = await product_links.nth(i).get_attribute(
                            "href",
                            timeout=5000,
                        )
                    except Exception:
                        continue

                    if not href:
                        continue

                    if any(term in href.lower() for term in excluded):
                        continue

                    product_url = href

                    # Absolute URL
                    if product_url.startswith("http"):
                        pass

                    # Protocol-relative URL
                    elif product_url.startswith("//"):
                        product_url = "https:" + product_url

                    # Relative URL
                    elif product_url.startswith("/"):
                        product_url = (
                            base_url.rstrip("/") + product_url
                        )

                    else:
                        product_url = (
                            base_url.rstrip("/") + "/" + product_url
                        )

                    if product_url not in product_urls:
                        product_urls.append(product_url)

            # If /collections/new-in returned no products, discover products
            # directly from the store homepage instead.
            if not product_urls:
                await page.goto(
                    base_url,
                    wait_until="commit",
                    timeout=60000,
                )

                steps.append(
                    "New-in collection yielded no products; "
                    "falling back to homepage product discovery"
                )

                await asyncio.sleep(2)

                product_links = page.locator(
                    'a[href*="/products/"]'
                )

                count = await product_links.count()

                for i in range(min(count, 30)):
                    try:
                        href = await product_links.nth(i).get_attribute(
                            "href",
                            timeout=5000,
                        )
                    except Exception:
                        continue

                    if not href:
                        continue

                    if any(term in href.lower() for term in excluded):
                        continue

                    product_url = href

                    if product_url.startswith("http"):
                        pass
                    elif product_url.startswith("//"):
                        product_url = "https:" + product_url
                    elif product_url.startswith("/"):
                        product_url = (
                            base_url.rstrip("/") + product_url
                        )
                    else:
                        product_url = (
                            base_url.rstrip("/") + "/" + product_url
                        )

                    if product_url not in product_urls:
                        product_urls.append(product_url)

            # If homepage discovery also yielded no products, use the
            # standard Shopify products.json endpoint as a final fallback.
            if not product_urls:
                products_json_url = (
                    f"{base_url.rstrip('/')}/products.json?limit=30"
                )

                try:
                    response = await page.request.get(
                        products_json_url,
                        timeout=30000,
                    )

                    if response.ok:
                        data = await response.json()
                        products = data.get("products", [])

                        steps.append(
                            f"Shopify products.json returned "
                            f"{len(products)} product(s)"
                        )

                        for product in products:
                            handle = product.get("handle")

                            if not handle:
                                continue

                            if any(
                                term in handle.lower()
                                for term in excluded
                            ):
                                continue

                            product_url = (
                                f"{base_url.rstrip('/')}/products/{handle}"
                            )

                            if product_url not in product_urls:
                                product_urls.append(product_url)

                    else:
                        steps.append(
                            "Shopify products.json fallback returned "
                            f"HTTP {response.status}"
                        )

                except Exception as e:
                    steps.append(
                        "Shopify products.json fallback failed: "
                        f"{type(e).__name__}"
                    )

            # 3. Find a product with a genuinely enabled Add to Cart button
            tested_products = []
            atc_button = None
            selected_product_url = None

            for product_url in product_urls[:30]:
                if product_url in tested_products:
                    continue

                tested_products.append(product_url)

                try:
                    await page.goto(
                        product_url,
                        wait_until="commit",
                        timeout=60000,
                    )

                    # Allow hydration / Alpine / theme JS to initialize.
                    await asyncio.sleep(2)

                    # --------------------------------------------------
                    # 3A. Prefer the actual Shopify product offer form.
                    #
                    # Sophie Allport uses:
                    #
                    # form.prd-ProductOffers_Form
                    #   button.prd-ProductOffers_Submit
                    #
                    # This is much safer than searching the entire page.
                    # --------------------------------------------------
                    product_form = page.locator(
                        "form.prd-ProductOffers_Form"
                    ).first

                    if await product_form.count() > 0:
                        try:
                            await product_form.wait_for(
                                state="visible",
                                timeout=5000,
                            )
                        except Exception:
                            pass

                        form_button = product_form.locator(
                            'button[type="submit"][name="add"]'
                        ).first

                        if await form_button.count() > 0:
                            try:
                                is_visible = await form_button.is_visible()
                                is_enabled = await form_button.is_enabled()

                                if is_visible and is_enabled:
                                    atc_button = form_button
                                    selected_product_url = _safe_url(product_url)

                                    steps.append("Selected purchasable product")

                                    logger.info(
                                        "Found product ATC inside product "
                                        "form: %s",
                                        product_url,
                                    )

                                    break

                            except Exception as form_button_error:
                                logger.debug(
                                    "Could not validate product form "
                                    "button on %s: %s",
                                    product_url,
                                    form_button_error,
                                )

                    # --------------------------------------------------
                    # 3B. Generic Shopify fallbacks.
                    # --------------------------------------------------
                    generic_selectors = [
                        (
                            'button.add-to-cart'
                            ':not([disabled]):visible'
                        ),
                        (
                            'button[name="add"]'
                            ':not([disabled]):visible'
                            ':not([data-quick-add-btn])'
                        ),
                        (
                            '[data-testid="add-to-cart"]'
                            ':not([disabled]):visible'
                        ),
                        (
                            'button.btn-transaction'
                            ':not([disabled]):visible'
                            ':has-text("ADD TO BAG")'
                        ),
                        (
                            'button:has-text("ADD TO CART")'
                            ':not([disabled]):visible'
                        ),
                        (
                            'button:has-text("ADD TO BAG")'
                            ':not([disabled]):visible'
                        ),
                    ]

                    for selector in generic_selectors:
                        candidate = page.locator(selector).first

                        try:
                            if await candidate.count() == 0:
                                continue

                            if not await candidate.is_visible():
                                continue

                            if not await candidate.is_enabled():
                                continue

                            atc_button = candidate
                            selected_product_url = _safe_url(product_url)

                            steps.append("Selected purchasable product")

                            logger.info(
                                "Found generic product ATC using "
                                "selector '%s': %s",
                                selector,
                                product_url,
                            )

                            break

                        except Exception as selector_error:
                            logger.debug(
                                "Selector failed on %s: %s",
                                product_url,
                                selector_error,
                            )

                    if atc_button is not None:
                        break

                except Exception as product_error:
                    logger.debug(
                        "Skipping product %s: %s",
                        product_url,
                        product_error,
                    )

            # No purchasable product found.
            if atc_button is None:
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary=(
                        "Could not find a product with an enabled "
                        "Add to Cart button"
                    ),
                    details={
                        "steps": steps,
                        "products_tested": len(tested_products),
                        "product_urls_found": len(product_urls),
                        "cart_evidence": cart_evidence,
                        "cart_evidence_meta": cart_evidence_meta,
                    },
                )

            # Extract target product info strictly from the form owning the clicked ATC button
            target_info = await _extract_target_product_info(page, atc_button, selected_product_url or product_url)
            before_lines, before_total, before_status, _ = await _fetch_cart_snapshot(page, timeout=2000)

            # 4. Add to Cart
            logger.info(
                "Clicking Add to Cart for product: %s",
                selected_product_url,
            )

            await atc_button.click(timeout=15000)
            steps.append("Clicked Add to Cart")

            # 5. Verify cart addition against baseline with strict overall 5.0s timeout
            cart_verified = False
            after_lines = []
            after_total = None
            after_meta = _empty_cart_meta()
            trigger = "api_only_fallback"

            async def _poll_cart():
                nonlocal trigger, cart_verified, after_lines, after_total, after_meta
                start_time = asyncio.get_event_loop().time()
                while True:
                    now = asyncio.get_event_loop().time()
                    elapsed = now - start_time
                    if elapsed >= 5.0:
                        break

                    remaining_sec = 5.0 - elapsed

                    if trigger == "api_only_fallback" and remaining_sec > 0.2:
                        try:
                            cart_dialog = page.locator('[role="dialog"][aria-modal="true"]:visible').first
                            if await cart_dialog.count() > 0:
                                if "Cart drawer detected" not in steps:
                                    steps.append("Cart drawer detected")
                                trigger = "auto_open_drawer"
                            else:
                                open_cart = page.locator(
                                    'button[aria-label*="cart" i]:visible, '
                                    'a[aria-label*="cart" i]:visible, '
                                    'button[aria-label*="Open cart" i]:visible'
                                ).first
                                if await open_cart.count() > 0:
                                    await open_cart.click(timeout=min(1000, int(remaining_sec * 1000)))
                                    cart_dialog = page.locator(
                                        '[role="dialog"][aria-modal="true"]:visible'
                                    ).first
                                    if await cart_dialog.count() > 0:
                                        if "Opened cart drawer" not in steps:
                                            steps.append("Opened cart drawer")
                                        trigger = "shopper_opened_drawer"
                        except Exception:
                            pass

                    now = asyncio.get_event_loop().time()
                    elapsed = now - start_time
                    if elapsed >= 5.0:
                        break

                    remaining_sec = 5.0 - elapsed
                    fetch_timeout_ms = max(50, int(min(2.0, remaining_sec) * 1000))
                    cur_lines, cur_total, cur_status, cur_meta = await _fetch_cart_snapshot(
                        page, timeout=fetch_timeout_ms
                    )

                    cur_meta["trigger"] = trigger
                    after_lines, after_total, after_status, after_meta = cur_lines, cur_total, cur_status, cur_meta

                    if _verify_cart_addition(before_lines, before_status, after_lines, after_status, target_info):
                        cart_verified = True
                        break

                    now_after = asyncio.get_event_loop().time()
                    if now_after - start_time >= 5.0:
                        break

                    sleep_duration = min(0.5, max(0.01, 5.0 - (now_after - start_time)))
                    if sleep_duration <= 0.02:
                        break
                    await asyncio.sleep(sleep_duration)

            try:
                await asyncio.wait_for(_poll_cart(), timeout=5.2)
            except (TimeoutError, asyncio.TimeoutError, Exception) as poll_err:
                logger.debug("Cart polling ended: %s", type(poll_err).__name__)

            cart_collector.attempted = True
            cart_evidence.clear()
            cart_evidence.extend(after_lines)
            cart_evidence_meta.clear()
            cart_evidence_meta.update(after_meta)
            after_status = after_meta.get("status", "failed")
            item_count = after_total if after_status != "failed" else None

            # Neither UI nor API confirmed valid target addition to cart.
            if not cart_verified or item_count is None or item_count <= 0:
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary=(
                        "Add to Cart was clicked but cart state "
                        "could not be verified"
                    ),
                    details={
                        "steps": steps,
                        "cart_item_count": item_count,
                        "selected_product": selected_product_url,
                        "cart_evidence": cart_evidence,
                        "cart_evidence_meta": cart_evidence_meta,
                    },
                )

            steps.append(
                f"Cart verified: {item_count} item(s)"
            )

            # If isolated cart evidence check was requested, return PASS directly without checkout navigation.
            if context.get("cart_evidence_only"):
                return SkillResult(
                    skill_name=self.name(),
                    status="PASS",
                    summary="Cart addition verified successfully via evidence",
                    details={
                        "steps": steps,
                        "cart_item_count": item_count,
                        "selected_product": selected_product_url,
                        "cart_evidence": cart_evidence,
                        "cart_evidence_meta": cart_evidence_meta,
                    },
                )

            # 6. Find visible checkout button in cart drawer or page.
            checkout_selectors = (
                'button:has-text("CHECKOUT"):visible, '
                'a:has-text("CHECKOUT"):visible, '
                'button:has-text("Check out"):visible, '
                'a:has-text("Check out"):visible, '
                'input[name="checkout"]:visible, '
                'button[name="checkout"]:visible, '
                'button[type="submit"]:has-text("Check Out"):visible, '
                'input[type="submit"][value*="Check Out"]:visible, '
                'a[href*="/checkouts"]:visible'
            )

            checkout_button = page.locator(checkout_selectors).first

            # Retry up to 3 seconds for dynamically injected checkout buttons in drawer
            if await checkout_button.count() == 0:
                for _ in range(10):
                    await asyncio.sleep(0.3)
                    checkout_button = page.locator(checkout_selectors).first
                    if await checkout_button.count() > 0:
                        break

            # Traditional /cart fallback.
            if await checkout_button.count() == 0:
                cart_url = await _cart_root(page) + "cart"

                try:
                    await page.goto(
                        cart_url,
                        wait_until="commit",
                        timeout=60000,
                    )

                    await asyncio.sleep(2)
                    steps.append("Visited Cart Page")

                    checkout_button = page.locator(checkout_selectors).first

                except Exception as cart_page_error:
                    logger.debug(
                        "Could not visit traditional cart page: %s",
                        type(cart_page_error).__name__,
                    )
                    raise cart_page_error

            # Checkout button missing.
            if await checkout_button.count() == 0:
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary="Visible checkout button not found in cart",
                    details={
                        "steps": steps,
                        "cart_item_count": item_count,
                        "selected_product": selected_product_url,
                        "cart_evidence": cart_evidence,
                        "cart_evidence_meta": cart_evidence_meta,
                    },
                )

            logger.info("Clicking visible checkout button")

            try:
                await checkout_button.click(timeout=15000)
            except Exception as checkout_click_error:
                logger.debug("Checkout button click failed: %s", type(checkout_click_error).__name__)
                raise checkout_click_error

            # Wait briefly for navigation to commit
            try:
                await page.wait_for_url(lambda u: "/checkout" in u.lower(), timeout=5000)
            except Exception:
                await asyncio.sleep(3)

            final_url = page.url

            checkout_reached = bool(re.search(r"/checkouts?(?:/|$)", urlparse(final_url).path, re.I))
            if checkout_reached:
                steps.append("Visited Checkout")

            # 7. Verify checkout was reached.
            if checkout_reached:
                return SkillResult(
                    skill_name=self.name(),
                    status="PASS",
                    summary=(
                        "Shopper journey reached checkout successfully "
                        "(Product -> Cart -> Checkout)"
                    ),
                    details={
                        "steps": steps,
                        "cart_item_count": item_count,
                        "checkout_url": _safe_url(final_url),
                        "selected_product": selected_product_url,
                        "cart_evidence": cart_evidence,
                        "cart_evidence_meta": cart_evidence_meta,
                    },
                )

            return SkillResult(
                skill_name=self.name(),
                status="WARN",
                summary="Checkout navigation did not reach an expected checkout URL",
                details={
                    "steps": steps,
                    "cart_item_count": item_count,
                    "current_url": _safe_url(final_url),
                    "selected_product": selected_product_url,
                    "cart_evidence": cart_evidence,
                    "cart_evidence_meta": cart_evidence_meta,
                },
            )

        except Exception as e:
            logger.debug("Shopper journey failed with exception: %s", type(e).__name__)

            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary="Shopper journey failed",
                error=f"shopper_journey_error:{type(e).__name__}",
                details={"steps": steps, "selected_product": selected_product_url,
                         "cart_item_count": item_count, "cart_evidence": cart_evidence,
                         "cart_evidence_meta": cart_evidence_meta},
            )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _drawer_item_count(page, drawer_locator) -> int | None:
    """Read item count structurally from cart drawer UI or aria-labels.

    Returns non-negative int or None when undetermined or ambiguous.
    """
    drawer_text_found = False

    try:
        text = await drawer_locator.inner_text(timeout=3000)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            drawer_text_found = True

        # 1. Line-by-line Structural Header Search:
        # Evaluate headers strictly within their line boundaries so subsequent lines do not invalidate valid headers.
        for line_str in lines:
            header_match = re.search(
                r"^(?:CART|YOUR CART|BAG|YOUR BAG)\s*"
                r"(?:\(\s*(\d+)\s*(?:ITEMS?|PRODUCTS?)?\s*\)|:\s*(\d+)\b|\s+(\d+)\s+(?:ITEMS?|PRODUCTS?)\b)$",
                line_str,
                re.IGNORECASE,
            )
            if header_match:
                for g in header_match.groups():
                    if g is not None:
                        return int(g)

        # 2. Standalone item counter line search (e.g., line consists solely of "2 ITEMS" or "0 ITEMS")
        for line_str in lines:
            # Skip promotional lines, product description sentences, or threshold lines
            if re.search(r"\b(?:add|spend|more|free|shipping|get|save|discount|pack|set|box|bundle|of|for|with)\b", line_str, re.IGNORECASE):
                continue

            item_match = re.search(
                r"^(\d+)\s+(?:ITEMS?|PRODUCTS?)$",
                line_str,
                re.IGNORECASE,
            )
            if item_match:
                return int(item_match.group(1))

    except Exception:
        logger.debug("Could not read drawer inner_text for item count")

    # Check aria-label on drawer itself or visible opener if inner_text didn't contain an explicit header
    try:
        drawer_aria = await drawer_locator.get_attribute("aria-label") or ""
        aria_match = re.search(
            r"(?<![-\d.,a-zA-Z])(\d+)\s*(?:items?|products?)\b(?!\s*(?:[A-Z]{3}|[\$£€]|[a-zA-Z\d.,]))",
            drawer_aria,
            re.IGNORECASE,
        )
        if aria_match:
            return int(aria_match.group(1))

        # Check opener button aria-label if drawer inner_text was completely unreadable or contained no valid counter
        if not drawer_text_found:
            opener = page.locator('button[aria-label*="cart" i]:visible, a[aria-label*="cart" i]:visible').first
            if await opener.count() > 0:
                aria = await opener.get_attribute("aria-label") or ""
                aria_match = re.search(
                    r"(?<![-\d.,a-zA-Z])(\d+)\s*(?:items?|products?)\b(?!\s*(?:[A-Z]{3}|[\$£€]|[a-zA-Z\d.,]))",
                    aria,
                    re.IGNORECASE,
                )
                if aria_match:
                    return int(aria_match.group(1))
    except Exception:
        pass

    try:
        # 3. Fallback to checking aria-label on VISIBLE opener or drawer ONLY when inner_text is unreadable/unavailable
        for loc in (
            page.locator('button[aria-label*="cart" i]:visible, a[aria-label*="cart" i]:visible').first,
            drawer_locator,
        ):
            if await loc.count() > 0:
                aria = await loc.get_attribute("aria-label") or ""
                aria_match = re.search(
                    r"(?<![-\d.,a-zA-Z])(\d+)\s*(?:items?|products?)\b(?!\s*(?:[A-Z]{3}|[\$£€]|[a-zA-Z\d.,]))",
                    aria,
                    re.IGNORECASE,
                )
                if aria_match:
                    return int(aria_match.group(1))
    except Exception:
        logger.debug("Could not read aria-label for item count")

    return None


def _empty_cart_meta() -> dict[str, Any]:
    return {"status": "not_attempted", "source": "none", "trigger": None,
            "attempted_at": None, "captured_at": None, "api_total_quantity": None,
            "api_line_count": None, "http_status": None, "error_code": None}


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc or parsed.username or parsed.password:
        return ""
    path = parsed.path
    checkout_match = re.match(r"^(.*?/checkouts?)(?:/.*)?$", path, re.IGNORECASE)
    if checkout_match:
        # Shopify checkout path segments can contain the checkout session token.
        # Keep the route for evidence while never persisting the token itself.
        path = checkout_match.group(1) + "/[redacted]"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


async def _cart_root(page) -> str:
    current = urlparse(page.url)
    if current.scheme not in ("http", "https") or not current.hostname or current.username or current.password:
        raise ValueError("unsafe_store_url")
    candidate = None
    try:
        candidate = await page.evaluate(
            "() => window.Shopify && window.Shopify.routes && window.Shopify.routes.root"
        )
    except Exception:
        pass
    if candidate is not None:
        if not isinstance(candidate, str) or not candidate:
            raise ValueError("unsafe_shopify_root")

        # Reject control characters (e.g. \n, \r, \t) in raw string
        if re.search(r"[\x00-\x1f\x7f]", candidate):
            raise ValueError("unsafe_shopify_root")

        # Check for backslash or percent-encoded traversal prior to unquoting
        if "\\" in candidate or r"%5c" in candidate.lower() or r"%5C" in candidate:
            raise ValueError("unsafe_shopify_root")

        # Perform unquoting up to 3 times to catch double/multi-encoding
        unquoted = candidate
        for _ in range(3):
            prev = unquoted
            unquoted = unquote(unquoted)
            if prev == unquoted:
                break

        # Reject control chars or unallowed whitespace after unquoting
        if re.search(r"[\x00-\x1f\x7f\s]", unquoted):
            raise ValueError("unsafe_shopify_root")

        # If percent encoding remains or unquoted string contains traversal/backslash, reject
        if "%" in unquoted or "\\" in unquoted or "/../" in unquoted or unquoted.endswith("/..") or unquoted.startswith("../"):
            raise ValueError("unsafe_shopify_root")

        raw_root = urlparse(unquoted)
        if ".." in raw_root.path.split("/"):
            raise ValueError("unsafe_shopify_root")

        root = urlparse(urljoin(f"{current.scheme}://{current.netloc}/", unquoted))
        if (root.scheme != current.scheme or root.netloc != current.netloc or root.username
                or root.password or ".." in root.path.split("/") or root.query or root.fragment
                or not root.path.endswith("/")):
            raise ValueError("unsafe_shopify_root")
        return f"{root.scheme}://{root.netloc}{root.path}"

    parts = [part for part in current.path.split("/") if part]
    locale = parts[0] + "/" if parts and re.fullmatch(r"[a-zA-Z]{2}(?:-[a-zA-Z]{2})?", parts[0]) else ""
    return f"{current.scheme}://{current.netloc}/{locale}"


def _validate_cart_payload(data: Any) -> tuple[list[dict[str, Any]], int, int]:
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("invalid_schema")
    item_count = data.get("item_count")
    if isinstance(item_count, bool) or not isinstance(item_count, int) or item_count < 0:
        raise ValueError("invalid_item_count")
    lines, total = [], 0
    for item in data["items"]:
        if not isinstance(item, dict):
            raise ValueError("invalid_line")
        quantity = item.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("invalid_quantity")
        total += quantity

        line: dict[str, Any] = {}

        # Required numeric identifiers: strictly positive int (> 0, rejecting None, bool, float, str, <= 0)
        for num_id in ("variant_id", "product_id"):
            val = item.get(num_id)
            if val is None or isinstance(val, bool) or not isinstance(val, int) or val <= 0:
                raise ValueError(f"invalid_{num_id}_type")
            line[num_id] = val

        # Text fields: str or None (strictly rejecting numbers, dicts, lists, bools)
        for txt_key in ("key", "product_title", "variant_title", "handle"):
            val = item.get(txt_key)
            if val is not None:
                if not isinstance(val, str):
                    raise ValueError(f"invalid_{txt_key}_type")
                line[txt_key] = val
            else:
                line[txt_key] = None

        line["quantity"] = quantity

        # Selling plan ID when present: positive int (> 0, rejecting bool, float, str, <= 0)
        plan = item.get("selling_plan_id")
        if plan is not None:
            if isinstance(plan, bool) or not isinstance(plan, int) or plan <= 0:
                raise ValueError("invalid_selling_plan_id_type")
            line["selling_plan_id"] = plan

        lines.append(line)

    if total != item_count:
        raise ValueError("inconsistent_item_count")
    return lines, total, len(lines)


async def _extract_target_product_info(page, atc_button, product_url: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "variant_id": None,
        "quantity": None,
    }

    # 1. Resolve the target form strictly associated with the clicked button
    target_form = None
    try:
        if atc_button and await atc_button.count() > 0:
            # First check HTML5 form attribute ownership (takes precedence over DOM tree nesting)
            form_id = await atc_button.get_attribute("form")
            if form_id:
                form_by_id = page.locator(f'form#{form_id}').first
                if await form_by_id.count() > 0:
                    target_form = form_by_id

            if not target_form or await target_form.count() == 0:
                # Ancestor form in DOM tree
                ancestor_form = atc_button.locator("xpath=ancestor::form").first
                if await ancestor_form.count() > 0:
                    target_form = ancestor_form
    except Exception:
        target_form = None

    # 2. If no owning form exists for the clicked button, return unproven info (WARN)
    if not target_form or await target_form.count() == 0:
        return info

    # 3. Inspect controls bound strictly to the target form
    try:
        id_elem = target_form.locator('select[name="id"], input[name="id"], select[name="id[]"], input[name="id[]"]').first
        if await id_elem.count() > 0:
            val = await id_elem.input_value()
            if val and val.isdigit() and int(val) > 0:
                info["variant_id"] = int(val)

        qty_elem = target_form.locator('input[name="quantity"]').first
        if await qty_elem.count() > 0:
            val = await qty_elem.input_value()
            if val and val.isdigit() and int(val) > 0:
                info["quantity"] = int(val)
        else:
            # Standard form without quantity input defaults to 1 requested item
            info["quantity"] = 1
    except Exception:
        pass

    return info


async def _fetch_cart_snapshot(page, timeout: int = 2000) -> tuple[list[dict[str, Any]], int, str, dict[str, Any]]:
    meta = _empty_cart_meta()
    meta["source"] = "api"
    lines = []
    total = 0
    response = None
    try:
        root = await _cart_root(page)
        response = await page.request.get(root + "cart.js", timeout=timeout, max_redirects=0)
        meta["http_status"] = response.status
        if not response.ok:
            meta["error_code"] = f"http_{response.status}"
            meta["status"] = "failed"
            return lines, total, "failed", meta
        lines, total, line_count = _validate_cart_payload(await response.json())
        meta.update(
            status="nonempty" if lines else "empty",
            error_code=None,
            api_total_quantity=total,
            api_line_count=line_count,
            captured_at=_now(),
        )
        return lines, total, meta["status"], meta
    except Exception:
        meta["error_code"] = "request_error"
        meta["status"] = "failed"
        return lines, total, "failed", meta
    finally:
        if response is not None:
            try:
                await response.dispose()
            except Exception:
                pass


def _verify_cart_addition(
    before_lines: list[dict[str, Any]],
    before_status: str,
    after_lines: list[dict[str, Any]],
    after_status: str,
    target_info: dict[str, Any],
) -> bool:
    # Rule 1: Fail closed if baseline read failed or after cart is not nonempty.
    if before_status == "failed" or after_status != "nonempty":
        return False

    # Rule 2: Require explicit target variant ID and valid requested quantity.
    variant_id = target_info.get("variant_id")
    if not variant_id or not isinstance(variant_id, int) or variant_id <= 0:
        return False

    req_qty = target_info.get("quantity")
    if req_qty is None or not isinstance(req_qty, int) or req_qty <= 0:
        return False

    before_qty = sum(item.get("quantity", 0) for item in before_lines if item.get("variant_id") == variant_id)
    after_qty = sum(item.get("quantity", 0) for item in after_lines if item.get("variant_id") == variant_id)

    # Rule 3: Target variant quantity in cart must increase strictly by requested quantity.
    return after_qty == before_qty + req_qty and after_qty > 0


class _CartEvidenceCollector:
    def __init__(self, evidence: list[dict[str, Any]], meta: dict[str, Any]):
        self.evidence, self.meta = evidence, meta
        self.attempted = False

    async def collect(self, page, trigger: str) -> None:
        if self.attempted:
            return
        self.attempted = True
        lines, total, status, meta = await _fetch_cart_snapshot(page)
        meta["trigger"] = trigger
        meta["attempted_at"] = meta.get("captured_at") or _now()
        self.meta.update(meta)
        self.evidence.clear()
        self.evidence.extend(lines)
