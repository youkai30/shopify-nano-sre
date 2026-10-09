"""Shopify Purchase Blocker Auditor Skill - Stage 3 Purchase Blockers Detection."""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from nano_sre.agent.core import Skill, SkillResult

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_locale_prefix_and_origin(url: str) -> tuple[str, str]:
    """Extract origin and locale prefix from URL."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    parts = [p for p in parsed.path.split("/") if p]
    locale = ""
    if parts and re.fullmatch(r"[a-zA-Z]{2}(?:-[a-zA-Z]{2})?", parts[0]):
        locale = f"/{parts[0]}"
    return origin, locale


def _extract_handle_from_url(url: str) -> Optional[str]:
    """Extract product handle from URL."""
    path = urlparse(url).path
    match = re.search(r"/products/([a-zA-Z0-9_-]+)", path)
    if match:
        return match.group(1)
    return None


def _is_exact_atc_path(path: str, locale: str) -> bool:
    """Check if the URL path matches an allowed add-to-cart endpoint strictly."""
    clean_path = path.rstrip("/")
    allowed = {
        "/cart/add",
        "/cart/add.js",
        f"{locale}/cart/add" if locale else "/cart/add",
        f"{locale}/cart/add.js" if locale else "/cart/add.js",
    }
    return clean_path in allowed


def _is_valid_int(val: Any) -> bool:
    """Check if val is an int and NOT a boolean."""
    return isinstance(val, int) and not isinstance(val, bool)


def _sanitize_url(url: str) -> str:
    """Sanitize URL to remove credentials or tokens."""
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc += f":{parsed.port}"
        return f"{parsed.scheme}://{netloc}{parsed.path}"
    return url


class ShopifyPurchaseBlockerAuditor(Skill):
    """Audits Shopify PDPs for Purchase Blockers after proving conditions via UI interaction."""

    def name(self) -> str:
        return "shopify_purchase_blocker_auditor"

    async def run(self, context: dict[str, Any]) -> SkillResult:
        page = context.get("page")
        base_url: str = context.get("base_url", "")

        if not page or not base_url:
            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary="Missing page or base_url in context",
                details={
                    "reason_code": "MISSING_CONTEXT",
                    "steps": ["Initialization failed: missing page or base_url"],
                    "proven_purchase_conditions": {},
                    "expected": "Valid page and base_url in context",
                    "observed": "Page or base_url missing",
                    "timestamp": _now(),
                    "reproduction_steps": ["Run auditor without page context"],
                    "redacted_screenshot": None,
                    "network_cart_evidence": {},
                },
            )

        steps = []
        js_errors: list[str] = []

        def on_page_error(error):
            js_errors.append(str(error))

        page.on("pageerror", on_page_error)

        try:
            # Stage 3 desktop flow
            try:
                await page.set_viewport_size({"width": 1280, "height": 800})
            except Exception as e:
                logger.debug("Failed setting viewport to desktop: %s", e)

            current_url = page.url or base_url
            origin, locale = _get_locale_prefix_and_origin(current_url)
            handle = _extract_handle_from_url(current_url)

            # Discover product if not already on PDP
            if not handle and "/products/" not in current_url.lower():
                await page.goto(base_url, wait_until="commit", timeout=60000)
                await asyncio.sleep(1)

                product_links = page.locator('a[href*="/products/"]')
                count = await product_links.count()
                for i in range(min(count, 10)):
                    href = await product_links.nth(i).get_attribute("href")
                    if href:
                        handle = _extract_handle_from_url(href)
                        if handle:
                            product_url = href if href.startswith("http") else urljoin(base_url, href)
                            await page.goto(product_url, wait_until="commit", timeout=60000)
                            await asyncio.sleep(1)
                            current_url = page.url
                            origin, locale = _get_locale_prefix_and_origin(current_url)
                            break

            if not handle:
                try:
                    res = await page.request.get(f"{origin}{locale}/products.json?limit=5")
                    if res.ok:
                        data = await res.json()
                        products = data.get("products", [])
                        if products:
                            handle = products[0].get("handle")
                            product_url = f"{origin}{locale}/products/{handle}"
                            await page.goto(product_url, wait_until="commit", timeout=60000)
                            await asyncio.sleep(1)
                            current_url = page.url
                            origin, locale = _get_locale_prefix_and_origin(current_url)
                except Exception as e:
                    logger.debug("Failed products.json lookup: %s", e)

            if not handle:
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary="Could not discover a product handle to audit purchase blockers",
                    details={
                        "reason_code": "PRODUCT_NOT_DISCOVERED",
                        "steps": steps,
                        "proven_purchase_conditions": {},
                        "expected": "Discover a product handle on PDP",
                        "observed": "No product handle discovered",
                        "timestamp": _now(),
                        "reproduction_steps": [f"Navigate to {base_url}"],
                        "redacted_screenshot": None,
                        "network_cart_evidence": {},
                    },
                )

            steps.append(f"Auditing product handle: {handle}")

            # Fetch product JSON data safely
            ajax_url = f"{origin}{locale}/products/{handle}.js"
            product_data = None
            try:
                ajax_res = await page.request.get(ajax_url, timeout=15000)
                if ajax_res.ok:
                    product_data = await ajax_res.json()
            except Exception as e:
                logger.debug("Ajax product fetch failed: %s", e)

            if not product_data:
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary=f"Could not fetch product JSON from {ajax_url}",
                    details={
                        "reason_code": "PRODUCT_JSON_UNAVAILABLE",
                        "steps": steps,
                        "proven_purchase_conditions": {},
                        "expected": f"Fetch JSON from {ajax_url}",
                        "observed": "Request failed or invalid response",
                        "timestamp": _now(),
                        "reproduction_steps": [f"GET {ajax_url}"],
                        "redacted_screenshot": None,
                        "network_cart_evidence": {},
                    },
                )

            variants_list = product_data.get("variants", [])
            options_list = product_data.get("options", [])

            # Pick an available variant
            target_variant = None
            for v in variants_list:
                if v.get("available", True):
                    target_variant = v
                    break

            if not target_variant:
                # All variants unavailable - false positive prevention (not a purchase blocker for available product)
                return SkillResult(
                    skill_name=self.name(),
                    status="PASS",
                    summary="Product has no available variants to purchase; non-blocker for unavailable inventory",
                    details={
                        "reason_code": "NONE",
                        "steps": steps + ["All product variants are marked unavailable"],
                        "proven_purchase_conditions": {"available_variants_count": 0},
                        "expected": "No purchase attempt on out-of-stock product",
                        "observed": "Product is entirely out of stock",
                        "timestamp": _now(),
                        "reproduction_steps": [f"View product {handle}"],
                        "redacted_screenshot": None,
                        "network_cart_evidence": {},
                    },
                )

            # Locate main form
            main_form = page.locator(
                'form[action*="/cart/add"], form.prd-ProductOffers_Form, [data-type="add-to-cart-form"]'
            ).first
            if await main_form.count() == 0:
                main_form = page.locator('form:has(button[name="add"]), section[data-product-single-media-group]').first

            # Dismiss modal/popups if present before interacting
            dismissed_popup = await self._dismiss_overlays_if_any(page)
            if dismissed_popup:
                steps.append("Dismissed modal overlay/popup")

            # 1. Option selection & satisfaction
            option_success, option_error = await self._select_variant_options_in_dom(
                page, main_form, target_variant, options_list
            )
            satisfied_consents = await self._satisfy_required_consents(page, main_form)
            if satisfied_consents:
                steps.append(f"Satisfied {satisfied_consents} required consent/terms input(s)")

            proven_conditions = {
                "product_handle": handle,
                "target_variant_id": target_variant.get("id"),
                "selected_options": target_variant.get("options", []),
                "required_consents_satisfied": satisfied_consents > 0,
            }

            if not option_success:
                screenshot_path = await self._take_screenshot(page, "option_unselectable")
                return SkillResult(
                    skill_name=self.name(),
                    status="FAIL",
                    summary=f"Mandatory product option could not be selected in UI: {option_error}",
                    details={
                        "reason_code": "MANDATORY_OPTION_UNSELECTABLE",
                        "steps": steps + [f"Failed option selection: {option_error}"],
                        "proven_purchase_conditions": proven_conditions,
                        "expected": "Mandatory option can be interacted with and selected",
                        "observed": f"Option selection failed: {option_error}",
                        "timestamp": _now(),
                        "reproduction_steps": [f"Navigate to {current_url}", f"Select option values for variant {target_variant.get('id')}"],
                        "redacted_screenshot": screenshot_path,
                        "network_cart_evidence": {},
                    },
                )

            steps.append("Successfully selected required variant options")

            # 2. Locate Add to Cart or Buy button
            atc_button = await self._find_atc_button(page, main_form)
            if not atc_button:
                screenshot_path = await self._take_screenshot(page, "button_missing")
                return SkillResult(
                    skill_name=self.name(),
                    status="FAIL",
                    summary="Add to Cart / Buy button missing or disabled for available product",
                    details={
                        "reason_code": "BUTTON_MISSING_OR_DISABLED",
                        "steps": steps + ["Could not find an enabled Add to Cart button"],
                        "proven_purchase_conditions": proven_conditions,
                        "expected": "Visible enabled Add to Cart button on PDP",
                        "observed": "No enabled Add to Cart button found",
                        "timestamp": _now(),
                        "reproduction_steps": [f"Navigate to {current_url}", "Inspect Add to Cart button state"],
                        "redacted_screenshot": screenshot_path,
                        "network_cart_evidence": {},
                    },
                )

            # Check if button is off-screen and scroll to it
            try:
                await atc_button.scroll_into_view_if_needed(timeout=2000)
                steps.append("Scrolled to Add to Cart button")
            except Exception as e:
                logger.debug("Scroll to ATC button failed/not needed: %s", e)

            # Check for non-dismissable overlay blocking the button
            is_blocked, overlay_desc = await self._is_button_blocked_by_overlay(page, atc_button)
            if is_blocked:
                screenshot_path = await self._take_screenshot(page, "button_blocked_overlay")
                return SkillResult(
                    skill_name=self.name(),
                    status="FAIL",
                    summary=f"Add to Cart button blocked by un-dismissable overlay ({overlay_desc})",
                    details={
                        "reason_code": "BUTTON_BLOCKED_BY_OVERLAY",
                        "steps": steps + [f"Add to Cart button blocked by overlay: {overlay_desc}"],
                        "proven_purchase_conditions": proven_conditions,
                        "expected": "Add to Cart button directly clickable",
                        "observed": f"Pointer events blocked by overlay element ({overlay_desc})",
                        "timestamp": _now(),
                        "reproduction_steps": [f"Navigate to {current_url}", "Attempt to click Add to Cart button"],
                        "redacted_screenshot": screenshot_path,
                        "network_cart_evidence": {},
                    },
                )

            # 3. Pre-cart snapshot
            meta_before, items_before = await self._fetch_cart_snapshot(page, origin, locale)

            # Prepare ATC request listener
            add_request_data = {"captured": False, "status": None, "res_variant_id": None, "error": None}
            captured_req_holder = [None]

            def on_request(request):
                if request.method.upper() != "POST":
                    return
                parsed_req = urlparse(request.url)
                req_origin = f"{parsed_req.scheme}://{parsed_req.netloc}"
                if origin and req_origin != origin:
                    return
                if not _is_exact_atc_path(parsed_req.path, locale):
                    return
                add_request_data["captured"] = True
                captured_req_holder[0] = request

            async def on_response(response):
                if captured_req_holder[0] and response.request == captured_req_holder[0]:
                    add_request_data["status"] = response.status
                    try:
                        if response.ok and "application/json" in (response.headers.get("content-type") or ""):
                            res_json = await response.json()
                            if isinstance(res_json, dict):
                                r_vid = res_json.get("id") or res_json.get("variant_id")
                                if _is_valid_int(r_vid):
                                    add_request_data["res_variant_id"] = r_vid
                    except Exception as e:
                        logger.debug("Failed parsing ATC response json: %s", e)

            page.on("request", on_request)
            page.on("response", on_response)

            errors_before_click_count = len(js_errors)

            # Click Add to Cart
            click_success = False
            click_error_msg = None
            try:
                await atc_button.click(timeout=10000)
                click_success = True
                steps.append("Clicked Add to Cart button")
            except Exception as e:
                click_error_msg = str(e)
                logger.debug("ATC click failed: %s", e)

            # Wait bounded time (up to 5s) for network response / state change
            start_poll = datetime.now()
            response_resolved = False
            while (datetime.now() - start_poll).total_seconds() < 5.0:
                if add_request_data["status"] is not None:
                    response_resolved = True
                    break
                await asyncio.sleep(0.1)

            # Post-cart snapshot
            meta_after, items_after = await self._fetch_cart_snapshot(page, origin, locale)

            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)

            # Calculate item quantity deltas
            expected_vid = target_variant.get("id")
            q_before = sum(item["quantity"] for item in items_before if item["variant_id"] == expected_vid)
            q_after = sum(item["quantity"] for item in items_after if item["variant_id"] == expected_vid)
            delta = q_after - q_before

            total_q_before = meta_before.get("item_count", 0) if meta_before["status"] in ("empty", "nonempty") else 0
            total_q_after = meta_after.get("item_count", 0) if meta_after["status"] in ("empty", "nonempty") else 0
            total_delta = total_q_after - total_q_before

            cart_evidence = {
                "meta_before": meta_before,
                "meta_after": meta_after,
                "add_request_summary": {
                    "captured": add_request_data["captured"],
                    "status": add_request_data["status"],
                },
                "delta_quantity": delta,
                "total_delta_quantity": total_delta,
            }

            errors_after_click = js_errors[errors_before_click_count:]

            # 4. Diagnostic evaluation
            screenshot_path = await self._take_screenshot(page, "atc_result")

            # Case 4A: Quantity increased or response succeeded with quantity addition -> PASS
            if delta > 0 or total_delta > 0 or (add_request_data["captured"] and add_request_data["status"] == 200 and delta >= 0):
                return SkillResult(
                    skill_name=self.name(),
                    status="PASS",
                    summary="Purchase blocker audit passed: product added to cart successfully",
                    details={
                        "reason_code": "NONE",
                        "steps": steps + ["Confirmed product addition to cart"],
                        "proven_purchase_conditions": proven_conditions,
                        "expected": "Product successfully added to cart",
                        "observed": f"Added variant ID {expected_vid} (quantity delta = {delta})",
                        "timestamp": _now(),
                        "reproduction_steps": [f"Navigate to {current_url}", "Click Add to Cart"],
                        "redacted_screenshot": screenshot_path,
                        "network_cart_evidence": cart_evidence,
                    },
                )

            # Case 4B: Uncaught JS error triggered during click causing failure -> FAIL
            if errors_after_click and not (delta > 0 or total_delta > 0):
                return SkillResult(
                    skill_name=self.name(),
                    status="FAIL",
                    summary=f"JavaScript error causally blocked purchase completion: {errors_after_click[0]}",
                    details={
                        "reason_code": "JS_ERROR_BLOCKING_PURCHASE",
                        "steps": steps + [f"JavaScript error encountered during purchase: {errors_after_click[0]}"],
                        "proven_purchase_conditions": proven_conditions,
                        "expected": "Clean execution without purchase-blocking JS errors",
                        "observed": f"JS error: {errors_after_click[0]}",
                        "timestamp": _now(),
                        "reproduction_steps": [f"Navigate to {current_url}", "Click Add to Cart"],
                        "redacted_screenshot": screenshot_path,
                        "network_cart_evidence": cart_evidence,
                    },
                )

            # Case 4C: ATC click failed or was intercepted without network request -> FAIL
            if not click_success or not add_request_data["captured"]:
                if not click_success:
                    return SkillResult(
                        skill_name=self.name(),
                        status="FAIL",
                        summary=f"Clicking Add to Cart failed: {click_error_msg}",
                        details={
                            "reason_code": "ATC_CLICK_FAILED",
                            "steps": steps + [f"ATC click exception: {click_error_msg}"],
                            "proven_purchase_conditions": proven_conditions,
                            "expected": "Add to Cart button successfully clicked",
                            "observed": f"Click threw exception: {click_error_msg}",
                            "timestamp": _now(),
                            "reproduction_steps": [f"Navigate to {current_url}", "Click Add to Cart"],
                            "redacted_screenshot": screenshot_path,
                            "network_cart_evidence": cart_evidence,
                        },
                    )

                # Click succeeded in Playwright, but no request captured and no quantity increase
                return SkillResult(
                    skill_name=self.name(),
                    status="FAIL",
                    summary="Add to Cart click executed but failed to trigger request or increase cart quantity",
                    details={
                        "reason_code": "ATC_CLICK_FAILED",
                        "steps": steps + ["ATC button clicked, but no network request was captured and cart quantity did not increase"],
                        "proven_purchase_conditions": proven_conditions,
                        "expected": "ATC click triggers add request and increases cart quantity",
                        "observed": "Click produced no network request and no cart quantity update",
                        "timestamp": _now(),
                        "reproduction_steps": [f"Navigate to {current_url}", "Click Add to Cart"],
                        "redacted_screenshot": screenshot_path,
                        "network_cart_evidence": cart_evidence,
                    },
                )

            # Case 4D: Request captured but HTTP error or response unresolved -> WARN
            if add_request_data["status"] is None or add_request_data["status"] >= 400:
                status_desc = f"HTTP {add_request_data['status']}" if add_request_data["status"] else "unresolved response / timeout"
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary=f"Add to Cart network request was inconclusive ({status_desc})",
                    details={
                        "reason_code": "NETWORK_OR_RESPONSE_UNRESOLVED",
                        "steps": steps + [f"Network request result: {status_desc}"],
                        "proven_purchase_conditions": proven_conditions,
                        "expected": "Successful 200 OK network response from cart endpoint",
                        "observed": f"Network request status: {status_desc}",
                        "timestamp": _now(),
                        "reproduction_steps": [f"Navigate to {current_url}", "Click Add to Cart"],
                        "redacted_screenshot": screenshot_path,
                        "network_cart_evidence": cart_evidence,
                    },
                )

            # Default fallback if no quantity increase occurred despite HTTP 200
            return SkillResult(
                skill_name=self.name(),
                status="WARN",
                summary="Add to Cart request succeeded but cart quantity update could not be confirmed",
                details={
                    "reason_code": "NETWORK_OR_RESPONSE_UNRESOLVED",
                    "steps": steps + ["Cart quantity delta remained 0"],
                    "proven_purchase_conditions": proven_conditions,
                    "expected": "Cart quantity incremented by at least 1",
                    "observed": f"Quantity delta = {delta}",
                    "timestamp": _now(),
                    "reproduction_steps": [f"Navigate to {current_url}", "Click Add to Cart"],
                    "redacted_screenshot": screenshot_path,
                    "network_cart_evidence": cart_evidence,
                },
            )

        except Exception as e:
            logger.exception("Error in shopify_purchase_blocker_auditor: %s", e)
            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary=f"Shopify purchase blocker auditor error: {str(e)}",
                error=str(e),
                details={
                    "reason_code": "INTERNAL_ERROR",
                    "steps": steps + [f"Unhandled exception: {type(e).__name__}"],
                    "proven_purchase_conditions": {},
                    "expected": "Audit completes without unhandled Python exceptions",
                    "observed": f"Exception: {str(e)}",
                    "timestamp": _now(),
                    "reproduction_steps": [f"Run auditor on {base_url}"],
                    "redacted_screenshot": None,
                    "network_cart_evidence": {},
                },
            )
        finally:
            page.remove_listener("pageerror", on_page_error)

    async def _dismiss_overlays_if_any(self, page) -> bool:
        """Attempt to dismiss modals/popups/cookie banners naturally."""
        dismiss_selectors = [
            '[aria-label*="close" i]:visible',
            'button.close:visible',
            '.modal-close:visible',
            'button:has-text("Accept"):visible',
            'button:has-text("Close"):visible',
            'button:has-text("Dismiss"):visible',
            'button:has-text("Agree"):visible',
            '.cookie-banner button:visible',
            '[id*="popup"] button.close:visible',
        ]
        dismissed = False
        for sel in dismiss_selectors:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                try:
                    await loc.click(timeout=1000)
                    await asyncio.sleep(0.5)
                    dismissed = True
                except Exception:
                    pass
        return dismissed

    async def _select_variant_options_in_dom(
        self, page, main_form, target_variant: dict[str, Any], options_list: list[Any]
    ) -> tuple[bool, str]:
        """Select option values required for target_variant via UI interaction."""
        form_loc = main_form if await main_form.count() > 0 else page
        variant_options = target_variant.get("options", [])

        option_names = []
        for opt in options_list:
            if isinstance(opt, dict):
                option_names.append(opt.get("name", ""))
            elif isinstance(opt, str):
                option_names.append(opt)

        for idx, val in enumerate(variant_options):
            opt_name = option_names[idx] if idx < len(option_names) else f"Option{idx+1}"
            selected = False

            # Try select element
            select_loc = form_loc.locator(
                f'select[name*="{opt_name}" i], select[name*="option{idx+1}" i], select[id*="{opt_name}" i], select'
            )
            select_count = await select_loc.count()
            for i in range(select_count):
                sel = select_loc.nth(i)
                if not await sel.is_visible():
                    continue
                try:
                    await sel.select_option(label=val, timeout=1000)
                    selected = True
                    break
                except Exception:
                    try:
                        await sel.select_option(value=val, timeout=1000)
                        selected = True
                        break
                    except Exception:
                        pass

            if selected:
                continue

            # Try radio or button/swatch
            btn_loc = form_loc.locator(
                f'input[type="radio"][value="{val}" i], button:has-text("{val}"), [data-value="{val}"], label:has-text("{val}")'
            ).first
            if await btn_loc.count() > 0 and await btn_loc.is_visible():
                try:
                    await btn_loc.click(timeout=2000)
                    selected = True
                except Exception:
                    pass

            # If selector was present but unclickable/disabled
            if not selected and len(variant_options) > 0:
                # Check if element exists in disabled state or throws click error
                disabled_elem = form_loc.locator(f'[value="{val}"][disabled], button:has-text("{val}")[disabled]').first
                if await disabled_elem.count() > 0:
                    return False, f"Option element '{opt_name}={val}' is disabled or non-interactive in UI"

        return True, ""

    async def _satisfy_required_consents(self, page, main_form) -> int:
        """Check mandatory terms/consent checkboxes inside product form or page."""
        form_loc = main_form if await main_form.count() > 0 else page
        checkboxes = form_loc.locator('input[type="checkbox"][required], input[type="checkbox"][name*="terms" i], input[type="checkbox"][name*="agree" i]')
        count = await checkboxes.count()
        satisfied = 0
        for i in range(count):
            cb = checkboxes.nth(i)
            if await cb.is_visible() and not (await cb.is_checked()):
                try:
                    await cb.check(timeout=1000)
                    satisfied += 1
                except Exception:
                    try:
                        await cb.click(timeout=1000)
                        satisfied += 1
                    except Exception:
                        pass
        return satisfied

    async def _find_atc_button(self, page, main_form):
        """Locate visible enabled Add to Cart button."""
        form_loc = main_form if await main_form.count() > 0 else page
        selectors = [
            'button[name="add"]:visible:not([disabled])',
            'button.add-to-cart:visible:not([disabled])',
            'button[type="submit"]:has-text("add"):visible:not([disabled])',
            'button[type="submit"]:has-text("bag"):visible:not([disabled])',
            'button:has-text("ADD TO CART"):visible:not([disabled])',
            'button:has-text("ADD TO BAG"):visible:not([disabled])',
            '[data-add-to-cart]:visible:not([disabled])',
        ]
        for sel in selectors:
            btn = form_loc.locator(sel).first
            if await btn.count() > 0:
                return btn
        # Fallback to page level if main_form level didn't match
        for sel in selectors:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                return btn
        return None

    async def _is_button_blocked_by_overlay(self, page, atc_button) -> tuple[bool, str]:
        """Check if pointer events on ATC button are intercepted by an overlay element."""
        try:
            blocked_info = await atc_button.evaluate("""
                el => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) return {blocked: false, element: ''};
                    const cx = rect.left + rect.width / 2;
                    const cy = rect.top + rect.height / 2;
                    const topEl = document.elementFromPoint(cx, cy);
                    if (!topEl) return {blocked: false, element: ''};
                    if (el === topEl || el.contains(topEl) || topEl.contains(el)) {
                        return {blocked: false, element: ''};
                    }
                    const tag = topEl.tagName.toLowerCase();
                    const cls = topEl.className || '';
                    const id = topEl.id || '';
                    return {blocked: true, element: `${tag}#${id}.${cls}`};
                }
            """)
            if isinstance(blocked_info, dict) and blocked_info.get("blocked"):
                return True, str(blocked_info.get("element", "unknown_overlay"))
        except Exception as e:
            logger.debug("Overlay check failed: %s", e)
        return False, ""

    async def _fetch_cart_snapshot(self, page, origin: str, locale: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Fetch safe cart.js snapshot with strict schema validation."""
        meta = {
            "status": "failed",
            "captured_at": _now(),
            "item_count": 0,
        }
        items = []

        try:
            res = await page.request.get(f"{origin}{locale}/cart.js", timeout=10000)
            if res.ok:
                data = await res.json()
                if not isinstance(data, dict):
                    meta["status"] = "inconsistent_payload"
                    return meta, []

                item_count = data.get("item_count")
                if not _is_valid_int(item_count) or item_count < 0:
                    meta["status"] = "inconsistent_payload"
                    return meta, []

                raw_items = data.get("items", [])
                if not isinstance(raw_items, list):
                    meta["status"] = "inconsistent_payload"
                    return meta, []

                calculated_total = 0
                for item in raw_items:
                    if not isinstance(item, dict):
                        meta["status"] = "inconsistent_payload"
                        return meta, []

                    qty = item.get("quantity")
                    vid = item.get("variant_id")
                    pid = item.get("product_id")

                    if not _is_valid_int(qty) or qty <= 0 or not _is_valid_int(vid) or vid <= 0:
                        meta["status"] = "inconsistent_payload"
                        return meta, []

                    if pid is not None and (not _is_valid_int(pid) or pid <= 0):
                        meta["status"] = "inconsistent_payload"
                        return meta, []

                    calculated_total += qty
                    items.append({
                        "variant_id": vid,
                        "product_id": pid,
                        "quantity": qty,
                    })

                if calculated_total != item_count:
                    meta["status"] = "inconsistent_payload"
                    return meta, []

                meta["status"] = "empty" if item_count == 0 else "nonempty"
                meta["item_count"] = item_count
            else:
                meta["status"] = f"http_{res.status}"
        except Exception as e:
            logger.debug("Cart snapshot fetch failed: %s", e)
            meta["status"] = "request_error"

        return meta, items

    async def _take_screenshot(self, page, tag: str) -> Optional[str]:
        """Take screenshot and return path."""
        try:
            os.makedirs("reports/screenshots", exist_ok=True)
            filename = f"blocker_audit_{tag}_{int(datetime.now().timestamp())}.png"
            path = os.path.join("reports/screenshots", filename)
            await page.screenshot(path=path, full_page=False)
            return path
        except Exception as e:
            logger.debug("Failed taking screenshot: %s", e)
            return None
