"""Shopify Mobile Purchase Blocker Auditor Skill - Phase 4 Mobile Purchase Blockers Detection."""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from nano_sre.agent.core import Skill, SkillResult
from nano_sre.agent.privacy import Redactor

logger = logging.getLogger(__name__)

_redactor = Redactor()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_value(val: Any) -> Any:
    """Recursively redact strings, dicts, and lists without corrupting local screenshot file paths."""
    if isinstance(val, str):
        if (
            val.startswith("reports/")
            or val.startswith("phase4_")
            or val.startswith("stage3_")
            or val.startswith("verification_output/")
            or ("/" in val and val.endswith(".png"))
        ):
            return val
        return _redactor.redact_text(val)
    if isinstance(val, list):
        return [_redact_value(item) for item in val]
    if isinstance(val, dict):
        return {k: _redact_value(v) for k, v in val.items()}
    return val


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
    """Sanitize URL to remove credentials or tokens without breaking scheme/host/path."""
    if not url:
        return ""
    parsed = urlparse(url)
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc += f":{parsed.port}"
    scheme = parsed.scheme or "http"
    path = parsed.path or "/"
    return f"{scheme}://{netloc}{path}"


MOBILE_VIEWPORTS = [
    {
        "name": "390x844",
        "viewport": {"width": 390, "height": 844},
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "is_mobile": True,
        "has_touch": True,
        "environment": "Chromium mobile emulation (390x844, touch-enabled)",
    },
    {
        "name": "360x800",
        "viewport": {"width": 360, "height": 800},
        "user_agent": "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "is_mobile": True,
        "has_touch": True,
        "environment": "Chromium mobile emulation (360x800, touch-enabled)",
    },
]

DESKTOP_CONFIG = {
    "name": "1280x800",
    "viewport": {"width": 1280, "height": 800},
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
    "is_mobile": False,
    "has_touch": False,
    "environment": "Chromium desktop emulation (1280x800)",
}


class ShopifyMobilePurchaseBlockerAuditor(Skill):
    """Audits Shopify PDPs for Mobile-Specific and Shared Purchase Blockers using real Chromium mobile emulation."""

    def name(self) -> str:
        return "shopify_mobile_purchase_blocker_auditor"

    async def run(self, context: dict[str, Any]) -> SkillResult:
        page = context.get("page")
        base_url: str = context.get("base_url", "")

        if not page or not base_url:
            return SkillResult(
                skill_name=self.name(),
                status="WARN",
                summary="Missing page or base_url in context",
                details=_redact_value({
                    "reason_code": "MISSING_CONTEXT",
                    "steps": ["Initialization failed: missing page or base_url"],
                    "proven_purchase_conditions": {},
                    "expected": "Valid page and base_url in context",
                    "observed": "Page or base_url missing",
                    "timestamp": _now(),
                    "reproduction_steps": ["Run auditor without page context"],
                    "redacted_screenshot": None,
                    "network_cart_evidence": {},
                    "tested_viewports": [],
                    "environment": "Chromium mobile emulation",
                }),
            )

        browser = None
        if hasattr(page, "context") and page.context and hasattr(page.context, "browser"):
            browser = page.context.browser

        if not browser:
            return SkillResult(
                skill_name=self.name(),
                status="WARN",
                summary="Browser instance unavailable from page context",
                details=_redact_value({
                    "reason_code": "BROWSER_UNAVAILABLE",
                    "steps": ["Could not retrieve browser from page context"],
                    "proven_purchase_conditions": {},
                    "expected": "Valid browser instance from page context",
                    "observed": "Browser instance missing",
                    "timestamp": _now(),
                    "reproduction_steps": ["Run auditor with valid browser"],
                    "redacted_screenshot": None,
                    "network_cart_evidence": {},
                    "tested_viewports": [],
                    "environment": "Chromium mobile emulation",
                }),
            )

        # Extract session cookies from caller's page context in memory without mutating caller page
        session_cookies = []
        try:
            if hasattr(page, "context") and page.context:
                session_cookies = await page.context.cookies()
        except Exception as e:
            logger.debug("Failed extracting session cookies: %s", e)

        # 1. ALWAYS test BOTH mobile viewports (390x844 AND 360x800) in isolated contexts
        viewport_results = {}
        for vp_cfg in MOBILE_VIEWPORTS:
            vp_name = vp_cfg["name"]
            res = await self._run_isolated_audit(
                browser=browser,
                base_url=base_url,
                device_config=vp_cfg,
                session_cookies=session_cookies,
            )
            viewport_results[vp_name] = res

        # Evaluate combined mobile status
        failed_vp_names = [name for name, r in viewport_results.items() if r.get("status") == "FAIL"]
        warn_vp_names = [name for name, r in viewport_results.items() if r.get("status") == "WARN"]

        # Case A: Mobile FAIL detected on at least one viewport
        if failed_vp_names:
            primary_failed_vp = failed_vp_names[0]
            fail_res = viewport_results[primary_failed_vp]
            fail_identity = fail_res.get("details", {}).get("blocker_identity", {})
            target_vp_config = next(cfg for cfg in MOBILE_VIEWPORTS if cfg["name"] == primary_failed_vp)

            # 2. Desktop comparison on 1280x800
            desktop_res = await self._run_isolated_audit(
                browser=browser,
                base_url=base_url,
                device_config=DESKTOP_CONFIG,
                session_cookies=session_cookies,
            )
            desktop_status = desktop_res.get("status")
            desktop_identity = desktop_res.get("details", {}).get("blocker_identity", {})

            # Classification logic based on structured blocker_identity matching
            if desktop_status == "PASS":
                classification = "MOBILE_SPECIFIC"
            elif desktop_status == "FAIL" and self._is_same_blocker_identity(fail_identity, desktop_identity):
                classification = "SHARED"
            else:
                classification = "COMPARISON_UNRESOLVED"

            desktop_comp_evidence = {
                "classification": classification,
                "desktop_status": desktop_status,
                "desktop_blocker_identity": desktop_identity,
                "desktop_summary": desktop_res.get("summary"),
                "desktop_observed": desktop_res.get("details", {}).get("observed"),
                "desktop_expected": desktop_res.get("details", {}).get("expected"),
            }

            # 3. Local reproduction (2 independent runs on the failed mobile viewport)
            repro_run_1 = await self._run_isolated_audit(
                browser=browser,
                base_url=base_url,
                device_config=target_vp_config,
                session_cookies=session_cookies,
            )
            repro_run_2 = await self._run_isolated_audit(
                browser=browser,
                base_url=base_url,
                device_config=target_vp_config,
                session_cookies=session_cookies,
            )

            r1_status = repro_run_1.get("status")
            r1_identity = repro_run_1.get("details", {}).get("blocker_identity", {})
            r2_status = repro_run_2.get("status")
            r2_identity = repro_run_2.get("details", {}).get("blocker_identity", {})

            repro_confirmed = (
                r1_status == "FAIL" and self._is_same_blocker_identity(fail_identity, r1_identity) and
                r2_status == "FAIL" and self._is_same_blocker_identity(fail_identity, r2_identity)
            )

            repro_summary = {
                "reproduction_confirmed": repro_confirmed,
                "target_viewport": primary_failed_vp,
                "run_1_status": r1_status,
                "run_1_blocker_identity": r1_identity,
                "run_2_status": r2_status,
                "run_2_blocker_identity": r2_identity,
            }

            details = fail_res["details"]
            details["desktop_comparison"] = desktop_comp_evidence
            details["reproduction_summary"] = repro_summary
            details["tested_viewports"] = [cfg["name"] for cfg in MOBILE_VIEWPORTS]
            details["viewport_results"] = {
                name: {
                    "status": r["status"],
                    "summary": r["summary"],
                    "reason_code": r.get("details", {}).get("reason_code"),
                    "blocker_identity": r.get("details", {}).get("blocker_identity", {}),
                }
                for name, r in viewport_results.items()
            }
            details["environment"] = target_vp_config["environment"]

            # If reproduction could not confirm the EXACT SAME blocker, demote to WARN
            if not repro_confirmed:
                warn_msg = (
                    f"Observed initial purchase blocker ({fail_res['summary']}), "
                    f"but exact blocker identity could not be consistently reproduced across independent test runs "
                    f"(run1={r1_status}, run2={r2_status})"
                )
                details["reason_code"] = "UNCONFIRMED_TRANSIENT_BLOCKER"
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary=_redact_value(warn_msg),
                    details=_redact_value(details),
                )

            # Build summary message based on classification
            base_summary = fail_res["summary"]
            if classification == "MOBILE_SPECIFIC":
                summary_msg = f"{base_summary} (Mobile-specific blocker: desktop journey succeeded)"
            elif classification == "SHARED":
                summary_msg = f"{base_summary} (Shared issue: reproduced on desktop as well)"
            else:
                summary_msg = f"{base_summary} (Desktop comparison unresolved: desktop status={desktop_status})"

            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary=_redact_value(summary_msg),
                details=_redact_value(details),
            )

        # Case B: No FAILs, but at least one WARN
        if warn_vp_names:
            first_warn_vp = warn_vp_names[0]
            warn_res = viewport_results[first_warn_vp]
            details = warn_res["details"]
            details["tested_viewports"] = [cfg["name"] for cfg in MOBILE_VIEWPORTS]
            details["viewport_results"] = {
                name: {
                    "status": r["status"],
                    "summary": r["summary"],
                    "reason_code": r.get("details", {}).get("reason_code"),
                    "blocker_identity": r.get("details", {}).get("blocker_identity", {}),
                }
                for name, r in viewport_results.items()
            }
            details["desktop_comparison"] = {"classification": "NOT_NEEDED_FOR_WARN"}
            details["reproduction_summary"] = {"classification": "NOT_NEEDED"}
            details["environment"] = "Chromium mobile emulation"

            return SkillResult(
                skill_name=self.name(),
                status="WARN",
                summary=_redact_value(warn_res["summary"]),
                details=_redact_value(details),
            )

        # Case C: Both viewports PASS
        pass_res = viewport_results[MOBILE_VIEWPORTS[0]["name"]]
        details = pass_res["details"]
        details["tested_viewports"] = [cfg["name"] for cfg in MOBILE_VIEWPORTS]
        details["viewport_results"] = {
            name: {
                "status": r["status"],
                "summary": r["summary"],
                "reason_code": r.get("details", {}).get("reason_code"),
                "blocker_identity": r.get("details", {}).get("blocker_identity", {}),
            }
            for name, r in viewport_results.items()
        }
        details["desktop_comparison"] = {"classification": "NOT_NEEDED_FOR_PASS"}
        details["reproduction_summary"] = {"classification": "NOT_NEEDED"}
        details["environment"] = "Chromium mobile emulation"

        return SkillResult(
            skill_name=self.name(),
            status="PASS",
            summary=_redact_value("Mobile purchase blocker audit passed across all mobile viewports: target product variant added to cart successfully"),
            details=_redact_value(details),
        )

    def _is_same_blocker_identity(self, id1: dict[str, Any], id2: dict[str, Any]) -> bool:
        """Compare two structured blocker identities for exact match across journey conditions."""
        if not id1 or not id2:
            return False

        # All core identity keys must be present and non-None in both identities
        keys_to_check = [
            "reason_code",
            "product_handle",
            "target_variant_id",
            "proven_dom_options",
            "affected_control",
            "fault_descriptor",
        ]
        for k in keys_to_check:
            if k not in id1 or k not in id2:
                return False
            if id1[k] is None or id2[k] is None:
                return False
            if id1[k] != id2[k]:
                return False

        return True

    async def _run_isolated_audit(
        self,
        browser,
        base_url: str,
        device_config: dict[str, Any],
        session_cookies: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Creates a completely NEW browser context with specified mobile/desktop emulation parameters without touching caller page."""
        if not browser:
            return {"status": "WARN", "summary": "No browser instance for isolated audit", "details": {}}

        ctx = None
        try:
            ctx = await browser.new_context(
                viewport=device_config["viewport"],
                user_agent=device_config["user_agent"],
                is_mobile=device_config["is_mobile"],
                has_touch=device_config["has_touch"],
            )
            if session_cookies:
                try:
                    await ctx.add_cookies(session_cookies)
                except Exception as e:
                    logger.debug("Failed restoring session cookies: %s", e)

            audit_page = await ctx.new_page()
            await audit_page.goto(base_url, wait_until="commit", timeout=60000)

            res = await self._run_single_mobile_audit(
                page=audit_page,
                base_url=base_url,
                mobile_config=device_config,
            )
            return res
        except Exception as e:
            logger.error("Isolated audit exception: %s", e, exc_info=True)
            return {"status": "WARN", "summary": f"Isolated audit failed: {e}", "details": {}}
        finally:
            if ctx:
                try:
                    await ctx.close()
                except Exception:
                    pass

    async def _run_single_mobile_audit(
        self,
        page,
        base_url: str,
        mobile_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Core execution logic for auditing mobile purchase blockers on a dedicated page."""
        steps = []
        js_errors: list[dict[str, Any]] = []

        def on_page_error(error):
            js_errors.append({"error": str(error), "timestamp": _now()})

        audit_page = page

        try:
            audit_page.on("pageerror", on_page_error)

            current_url = _sanitize_url(audit_page.url or base_url)
            origin, locale = _get_locale_prefix_and_origin(current_url)
            handle = _extract_handle_from_url(current_url)

            if not handle and "/products/" not in current_url.lower():
                await audit_page.goto(base_url, wait_until="commit", timeout=60000)
                await asyncio.sleep(1)

                product_links = audit_page.locator('a[href*="/products/"]')
                count = await product_links.count()
                for i in range(min(count, 10)):
                    href = await product_links.nth(i).get_attribute("href")
                    if href:
                        handle = _extract_handle_from_url(href)
                        if handle:
                            product_url = href if href.startswith("http") else urljoin(base_url, href)
                            await audit_page.goto(product_url, wait_until="commit", timeout=60000)
                            await asyncio.sleep(1)
                            current_url = _sanitize_url(audit_page.url)
                            origin, locale = _get_locale_prefix_and_origin(current_url)
                            break

            if not handle:
                try:
                    res = await audit_page.request.get(f"{origin}{locale}/products.json?limit=5")
                    if res.ok:
                        data = await res.json()
                        products = data.get("products", [])
                        if products:
                            handle = products[0].get("handle")
                            product_url = f"{origin}{locale}/products/{handle}"
                            await audit_page.goto(product_url, wait_until="commit", timeout=60000)
                            await asyncio.sleep(1)
                            current_url = _sanitize_url(audit_page.url)
                            origin, locale = _get_locale_prefix_and_origin(current_url)
                except Exception as e:
                    logger.debug("Failed products.json lookup: %s", e)

            if not handle:
                return {
                    "status": "WARN",
                    "summary": "Could not discover a product handle on mobile view to audit purchase blockers",
                    "details": {
                        "reason_code": "PRODUCT_NOT_DISCOVERED",
                        "blocker_identity": {},
                        "steps": steps,
                        "proven_purchase_conditions": {},
                        "expected": "Discover a product handle on PDP",
                        "observed": "No product handle discovered",
                        "timestamp": _now(),
                        "reproduction_steps": [f"Navigate to {base_url}"],
                        "redacted_screenshot": None,
                        "network_cart_evidence": {},
                        "mobile_context_config": mobile_config,
                        "environment": mobile_config.get("environment", "Chromium mobile emulation"),
                    },
                }

            steps.append(f"Auditing product handle on mobile ({mobile_config['name']}): {handle}")

            # Fetch product JSON
            ajax_url = f"{origin}{locale}/products/{handle}.js"
            product_data = None
            try:
                ajax_res = await audit_page.request.get(ajax_url, timeout=15000)
                if ajax_res.ok:
                    product_data = await ajax_res.json()
            except Exception as e:
                logger.debug("Ajax product fetch failed: %s", e)

            if not product_data:
                return {
                    "status": "WARN",
                    "summary": f"Could not fetch product JSON from {ajax_url}",
                    "details": {
                        "reason_code": "PRODUCT_JSON_UNAVAILABLE",
                        "blocker_identity": {},
                        "steps": steps,
                        "proven_purchase_conditions": {},
                        "expected": f"Fetch JSON from {ajax_url}",
                        "observed": "Request failed or invalid response",
                        "timestamp": _now(),
                        "reproduction_steps": [f"GET {ajax_url}"],
                        "redacted_screenshot": None,
                        "network_cart_evidence": {},
                        "mobile_context_config": mobile_config,
                        "environment": mobile_config.get("environment", "Chromium mobile emulation"),
                    },
                }

            variants_list = product_data.get("variants", [])
            options_list = product_data.get("options", [])

            target_variant = None
            for v in variants_list:
                if v.get("available", True):
                    target_variant = v
                    break

            if not target_variant:
                return {
                    "status": "PASS",
                    "summary": "Product has no available variants to purchase; non-blocker for unavailable inventory",
                    "details": {
                        "reason_code": "NONE",
                        "blocker_identity": {},
                        "steps": steps + ["All product variants are marked unavailable"],
                        "proven_purchase_conditions": {"available_variants_count": 0},
                        "expected": "No purchase attempt on out-of-stock product",
                        "observed": "Product is entirely out of stock",
                        "timestamp": _now(),
                        "reproduction_steps": [f"View product {handle}"],
                        "redacted_screenshot": None,
                        "network_cart_evidence": {},
                        "mobile_context_config": mobile_config,
                        "environment": mobile_config.get("environment", "Chromium mobile emulation"),
                    },
                }

            # Find main product form/container
            main_form = audit_page.locator(
                'form[action*="/cart/add"], form.prd-ProductOffers_Form, [data-type="add-to-cart-form"]'
            ).first
            if await main_form.count() == 0:
                main_form = audit_page.locator('form:has(button[name="add"]), section[data-product-single-media-group]').first

            # Dismiss modal overlays
            dismissed_popup = await self._dismiss_overlays_if_any(audit_page)
            if dismissed_popup:
                steps.append("Dismissed modal overlay/popup")

            # Check mandatory customization field requirements
            unfulfilled_req = await self._check_unfulfilled_customization_fields(audit_page, main_form)
            if unfulfilled_req:
                return {
                    "status": "WARN",
                    "summary": f"Mandatory customization requirement unfulfilled on mobile ({unfulfilled_req})",
                    "details": {
                        "reason_code": "UNFULFILLED_PURCHASE_REQUIREMENT",
                        "blocker_identity": {
                            "reason_code": "UNFULFILLED_PURCHASE_REQUIREMENT",
                            "product_handle": handle,
                            "affected_control": "customization_field",
                            "fault_descriptor": unfulfilled_req,
                        },
                        "steps": steps + [f"Unfulfilled requirement: {unfulfilled_req}"],
                        "proven_purchase_conditions": {"customization_required": True, "field": unfulfilled_req},
                        "expected": "User input provided for mandatory customization field before purchasing",
                        "observed": f"Field '{unfulfilled_req}' requires user input or constraint satisfaction",
                        "timestamp": _now(),
                        "reproduction_steps": [f"Navigate to {current_url}", "Check required personalization fields"],
                        "redacted_screenshot": None,
                        "network_cart_evidence": {},
                        "mobile_context_config": mobile_config,
                        "environment": mobile_config.get("environment", "Chromium mobile emulation"),
                    },
                }

            # Option selection & DOM selection proof
            option_success, option_error, proven_dom_options = await self._select_variant_options_in_dom(
                audit_page, main_form, target_variant, options_list, mobile_config
            )
            satisfied_consents = await self._satisfy_required_consents(audit_page, main_form, mobile_config)
            if satisfied_consents:
                steps.append(f"Satisfied {satisfied_consents} required consent/terms input(s)")

            proven_conditions = {
                "product_handle": handle,
                "target_variant_id": target_variant.get("id"),
                "proven_dom_options": proven_dom_options,
                "required_consents_satisfied": satisfied_consents > 0,
            }

            if not option_success:
                status_to_return = "FAIL" if "unselectable" in option_error.lower() or "disabled" in option_error.lower() else "WARN"
                reason_to_return = "MANDATORY_OPTION_UNSELECTABLE" if status_to_return == "FAIL" else "UNPROVEN_OPTION_PATTERN"
                screenshot_path = await self._take_screenshot(audit_page, "option_issue")
                return {
                    "status": status_to_return,
                    "summary": f"Mobile option selection issue: {option_error}",
                    "details": {
                        "reason_code": reason_to_return,
                        "blocker_identity": {
                            "reason_code": reason_to_return,
                            "product_handle": handle,
                            "target_variant_id": target_variant.get("id"),
                            "proven_dom_options": proven_dom_options,
                            "affected_control": "variant_option",
                            "fault_descriptor": option_error,
                        },
                        "steps": steps + [f"Option selection issue: {option_error}"],
                        "proven_purchase_conditions": proven_conditions,
                        "expected": "Mandatory option can be interacted with and selected in DOM on mobile",
                        "observed": f"Option selection failed or unproven: {option_error}",
                        "timestamp": _now(),
                        "reproduction_steps": [f"Navigate to {current_url}", f"Select option values for variant {target_variant.get('id')}"],
                        "redacted_screenshot": screenshot_path,
                        "network_cart_evidence": {},
                        "mobile_context_config": mobile_config,
                        "environment": mobile_config.get("environment", "Chromium mobile emulation"),
                    },
                }

            steps.append("Successfully selected required variant options on mobile")

            # Locate Add to Cart button
            atc_button = await self._find_atc_button(audit_page, main_form)
            if not atc_button:
                screenshot_path = await self._take_screenshot(audit_page, "button_unconfirmed")
                return {
                    "status": "WARN",
                    "summary": "Main product form or Add to Cart button binding unconfirmed on mobile",
                    "details": {
                        "reason_code": "BUTTON_MISSING_OR_DISABLED",
                        "blocker_identity": {
                            "reason_code": "BUTTON_MISSING_OR_DISABLED",
                            "product_handle": handle,
                            "affected_control": "add_to_cart_button",
                            "fault_descriptor": "unconfirmed_binding",
                        },
                        "steps": steps + ["Could not establish clear Add to Cart button binding inside main product form or sticky CTA"],
                        "proven_purchase_conditions": proven_conditions,
                        "expected": "Visible enabled Add to Cart button bound to product form on mobile",
                        "observed": "Button binding unconfirmed",
                        "timestamp": _now(),
                        "reproduction_steps": [f"Navigate to {current_url}", "Inspect product form and ATC button"],
                        "redacted_screenshot": screenshot_path,
                        "network_cart_evidence": {},
                        "mobile_context_config": mobile_config,
                        "environment": mobile_config.get("environment", "Chromium mobile emulation"),
                    },
                }

            # Check off-screen scrolling
            try:
                await atc_button.scroll_into_view_if_needed(timeout=2000)
                steps.append("Scrolled to Add to Cart button on mobile")
            except Exception as e:
                logger.debug("Scroll to ATC button note: %s", e)

            # Check un-dismissable overlay or sticky footer blocking button pointer events
            is_blocked, overlay_desc = await self._is_button_blocked_by_overlay(audit_page, atc_button)
            if is_blocked:
                alt_button = await self._find_alternative_unblocked_atc_button(audit_page, atc_button)
                if alt_button:
                    atc_button = alt_button
                    steps.append("Primary button blocked, but found valid unblocked alternative ATC button")
                else:
                    screenshot_path = await self._take_screenshot(audit_page, "button_blocked_overlay")
                    return {
                        "status": "FAIL",
                        "summary": f"Add to Cart button blocked on mobile by un-dismissable overlay or sticky element ({overlay_desc})",
                        "details": {
                            "reason_code": "BUTTON_BLOCKED_BY_OVERLAY",
                            "blocker_identity": {
                                "reason_code": "BUTTON_BLOCKED_BY_OVERLAY",
                                "product_handle": handle,
                                "target_variant_id": target_variant.get("id"),
                                "proven_dom_options": proven_dom_options,
                                "affected_control": "add_to_cart_button",
                                "fault_descriptor": overlay_desc,
                            },
                            "steps": steps + [f"Add to Cart button blocked by overlay: {overlay_desc}"],
                            "proven_purchase_conditions": proven_conditions,
                            "expected": "Add to Cart button directly clickable/tappable without overlay interception",
                            "observed": f"Pointer events blocked by overlay element ({overlay_desc})",
                            "timestamp": _now(),
                            "reproduction_steps": [f"Navigate to {current_url}", "Attempt to tap Add to Cart button on mobile"],
                            "redacted_screenshot": screenshot_path,
                            "network_cart_evidence": {},
                            "mobile_context_config": mobile_config,
                            "environment": mobile_config.get("environment", "Chromium mobile emulation"),
                        },
                    }

            # Pre-cart snapshot
            meta_before, items_before = await self._fetch_cart_snapshot(audit_page, origin, locale)

            if meta_before["status"] not in ("empty", "nonempty"):
                return {
                    "status": "WARN",
                    "summary": f"Pre-cart snapshot failed (status={meta_before['status']}); unable to prove cart addition",
                    "details": {
                        "reason_code": "UNRESOLVED_PRE_CART_STATE",
                        "blocker_identity": {},
                        "steps": steps + [f"Pre-cart read returned status: {meta_before['status']}"],
                        "proven_purchase_conditions": proven_conditions,
                        "expected": "Successful pre-cart reading",
                        "observed": f"Pre-cart read status: {meta_before['status']}",
                        "timestamp": _now(),
                        "reproduction_steps": [f"GET {origin}{locale}/cart.js"],
                        "redacted_screenshot": None,
                        "network_cart_evidence": {"meta_before": meta_before},
                        "mobile_context_config": mobile_config,
                        "environment": mobile_config.get("environment", "Chromium mobile emulation"),
                    },
                }

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

            audit_page.on("request", on_request)
            audit_page.on("response", on_response)

            click_success = False
            click_error_msg = None
            handler_exception = False
            try:
                # Perform real touch tap if touch enabled in config
                if mobile_config.get("has_touch") and hasattr(audit_page, "touchscreen"):
                    box = await atc_button.bounding_box()
                    if box:
                        cx = box["x"] + box["width"] / 2
                        cy = box["y"] + box["height"] / 2
                        await audit_page.touchscreen.tap(cx, cy)
                    else:
                        await atc_button.click(timeout=10000)
                else:
                    await atc_button.click(timeout=10000)
                click_success = True
                steps.append("Tapped Add to Cart button using touch action")
            except Exception as e:
                click_error_msg = str(e)
                handler_exception = True
                logger.debug("ATC tap note: %s", e)

            # Bounded poll for network response
            start_poll = datetime.now()
            while (datetime.now() - start_poll).total_seconds() < 5.0:
                if add_request_data["status"] is not None:
                    break
                await asyncio.sleep(0.1)

            # Post-cart snapshot
            meta_after, items_after = await self._fetch_cart_snapshot(audit_page, origin, locale)

            expected_vid = target_variant.get("id")
            q_before = sum(item["quantity"] for item in items_before if item["variant_id"] == expected_vid)
            q_after = sum(item["quantity"] for item in items_after if item["variant_id"] == expected_vid)
            delta = q_after - q_before

            other_deltas = {}
            for item in items_after:
                vid = item["variant_id"]
                if vid != expected_vid:
                    q_b = sum(i["quantity"] for i in items_before if i["variant_id"] == vid)
                    if item["quantity"] - q_b > 0:
                        other_deltas[vid] = item["quantity"] - q_b

            cart_evidence = {
                "meta_before": meta_before,
                "meta_after": meta_after,
                "add_request_summary": {
                    "captured": add_request_data["captured"],
                    "status": add_request_data["status"],
                },
                "delta_quantity": delta,
            }

            screenshot_path = await self._take_screenshot(audit_page, "atc_result")

            # 1. Target variant quantity increased strictly -> PASS (even if drawer UI delayed)
            if delta > 0:
                return {
                    "status": "PASS",
                    "summary": "Mobile purchase blocker audit passed: target product variant added to cart successfully",
                    "details": {
                        "reason_code": "NONE",
                        "blocker_identity": {},
                        "steps": steps + ["Confirmed target product variant addition to cart on mobile"],
                        "proven_purchase_conditions": proven_conditions,
                        "expected": "Target product variant added to cart",
                        "observed": f"Added variant ID {expected_vid} (quantity delta = {delta})",
                        "timestamp": _now(),
                        "reproduction_steps": [f"Navigate to {current_url}", "Tap Add to Cart"],
                        "redacted_screenshot": screenshot_path,
                        "network_cart_evidence": cart_evidence,
                        "mobile_context_config": mobile_config,
                        "environment": mobile_config.get("environment", "Chromium mobile emulation"),
                    },
                }

            # 2. Wrong variant quantity increased -> FAIL
            if other_deltas:
                added_other_vid = list(other_deltas.keys())[0]
                return {
                    "status": "FAIL",
                    "summary": f"Added wrong variant ID {added_other_vid} instead of expected {expected_vid} on mobile",
                    "details": {
                        "reason_code": "WRONG_VARIANT_ADDED",
                        "blocker_identity": {
                            "reason_code": "WRONG_VARIANT_ADDED",
                            "product_handle": handle,
                            "target_variant_id": expected_vid,
                            "proven_dom_options": proven_dom_options,
                            "affected_control": "add_to_cart_button",
                            "fault_descriptor": f"added_variant_{added_other_vid}",
                        },
                        "steps": steps + [f"Added wrong variant ID {added_other_vid}"],
                        "proven_purchase_conditions": proven_conditions,
                        "expected": f"Add target variant ID {expected_vid}",
                        "observed": f"Added variant ID {added_other_vid}",
                        "timestamp": _now(),
                        "reproduction_steps": [f"Navigate to {current_url}", "Tap Add to Cart"],
                        "redacted_screenshot": screenshot_path,
                        "network_cart_evidence": cart_evidence,
                        "mobile_context_config": mobile_config,
                        "environment": mobile_config.get("environment", "Chromium mobile emulation"),
                    },
                }

            # 3. Request WAS NOT captured AND tap threw direct handler exception -> FAIL
            if not add_request_data["captured"]:
                if handler_exception and click_error_msg:
                    return {
                        "status": "FAIL",
                        "summary": "Tapping Add to Cart button failed in event handler on mobile",
                        "details": {
                            "reason_code": "ATC_CLICK_FAILED",
                            "blocker_identity": {
                                "reason_code": "ATC_CLICK_FAILED",
                                "product_handle": handle,
                                "target_variant_id": expected_vid,
                                "proven_dom_options": proven_dom_options,
                                "affected_control": "add_to_cart_button",
                                "fault_descriptor": click_error_msg,
                            },
                            "steps": steps + ["ATC tap exception in event handler"],
                            "proven_purchase_conditions": proven_conditions,
                            "expected": "Add to Cart button successfully tapped",
                            "observed": "Tap exception in handler",
                            "timestamp": _now(),
                            "reproduction_steps": [f"Navigate to {current_url}", "Tap Add to Cart"],
                            "redacted_screenshot": screenshot_path,
                            "network_cart_evidence": cart_evidence,
                            "mobile_context_config": mobile_config,
                            "environment": mobile_config.get("environment", "Chromium mobile emulation"),
                        },
                    }

                # Click executed without direct handler exception, but no request captured -> WARN
                return {
                    "status": "WARN",
                    "summary": "Add to Cart tap executed on mobile but request dispatch could not be confirmed",
                    "details": {
                        "reason_code": "NETWORK_OR_RESPONSE_UNRESOLVED",
                        "blocker_identity": {},
                        "steps": steps + ["ATC button tapped, but no network request was captured"],
                        "proven_purchase_conditions": proven_conditions,
                        "expected": "ATC tap triggers add request",
                        "observed": "Tap produced no network request",
                        "timestamp": _now(),
                        "reproduction_steps": [f"Navigate to {current_url}", "Tap Add to Cart"],
                        "redacted_screenshot": screenshot_path,
                        "network_cart_evidence": cart_evidence,
                        "mobile_context_config": mobile_config,
                        "environment": mobile_config.get("environment", "Chromium mobile emulation"),
                    },
                }

            # 4. Request WAS captured, but returned non-200 or 200 without cart change -> WARN
            status_code = add_request_data["status"]
            status_desc = f"HTTP {status_code}" if status_code else "unresolved response / timeout"
            summary_msg = (
                f"Add to Cart request returned {status_desc} but target variant quantity did not increase"
                if status_code == 200 else f"Add to Cart network request was inconclusive ({status_desc})"
            )
            return {
                "status": "WARN",
                "summary": summary_msg,
                "details": {
                    "reason_code": "NETWORK_OR_RESPONSE_UNRESOLVED",
                    "blocker_identity": {},
                    "steps": steps + [f"Network request result: {status_desc}, target quantity delta: {delta}"],
                    "proven_purchase_conditions": proven_conditions,
                    "expected": "Successful 200 OK network response incrementing target variant quantity",
                    "observed": f"Network status {status_desc}, quantity delta = {delta}",
                    "timestamp": _now(),
                    "reproduction_steps": [f"Navigate to {current_url}", "Tap Add to Cart"],
                    "redacted_screenshot": screenshot_path,
                    "network_cart_evidence": cart_evidence,
                    "mobile_context_config": mobile_config,
                    "environment": mobile_config.get("environment", "Chromium mobile emulation"),
                },
            }

        except Exception as e:
            logger.exception("Internal error in shopify_mobile_purchase_blocker_auditor: %s", e)
            return {
                "status": "WARN",
                "summary": f"Mobile purchase blocker auditor encountered internal execution issue: {type(e).__name__}",
                "details": {
                    "reason_code": "INTERNAL_AUDITOR_ERROR",
                    "blocker_identity": {},
                    "steps": steps + [f"Internal error: {type(e).__name__}"],
                    "proven_purchase_conditions": {},
                    "expected": "Auditor executes without internal python errors",
                    "observed": "Internal auditor exception",
                    "timestamp": _now(),
                    "reproduction_steps": [f"Run auditor on {base_url}"],
                    "redacted_screenshot": None,
                    "network_cart_evidence": {},
                    "mobile_context_config": mobile_config,
                    "environment": mobile_config.get("environment", "Chromium mobile emulation"),
                },
            }
        finally:
            try:
                audit_page.remove_listener("pageerror", on_page_error)
            except Exception:
                pass

    async def _check_unfulfilled_customization_fields(self, page, main_form) -> Optional[str]:
        """Check for mandatory personalization/text inputs requiring user input or violating HTML validity."""
        form_loc = main_form if await main_form.count() > 0 else page
        try:
            inputs = form_loc.locator(
                'input:not([type="checkbox"]):not([type="radio"]):not([type="hidden"]):not([type="submit"]), textarea, [name*="properties"]'
            )
            count = await inputs.count()
            for i in range(count):
                inp = inputs.nth(i)
                if await inp.is_visible():
                    is_valid = await inp.evaluate("el => el.checkValidity ? el.checkValidity() : true")
                    if not is_valid:
                        name_attr = await inp.get_attribute("name") or await inp.get_attribute("placeholder") or "input constraint"
                        return name_attr
        except Exception as e:
            logger.debug("Customization check note: %s", e)
        return None

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
        self, page, main_form, target_variant: dict[str, Any], options_list: list[Any], mobile_config: dict[str, Any]
    ) -> tuple[bool, str, dict[str, str]]:
        """Select option values required for target_variant via touch UI interaction and prove DOM selection strictly."""
        form_loc = main_form if await main_form.count() > 0 else page
        variant_options = target_variant.get("options", [])
        proven_dom_options = {}

        option_names = []
        for opt in options_list:
            if isinstance(opt, dict):
                option_names.append(opt.get("name", ""))
            elif isinstance(opt, str):
                option_names.append(opt)

        for idx, val in enumerate(variant_options):
            opt_name = option_names[idx] if idx < len(option_names) else f"Option{idx+1}"
            selected = False

            # Try select dropdown
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
                except Exception:
                    try:
                        await sel.select_option(value=val, timeout=1000)
                    except Exception:
                        pass
                cur_val = await sel.evaluate("el => el.value")
                if cur_val and str(cur_val).lower() == str(val).lower():
                    selected = True
                    proven_dom_options[opt_name] = str(cur_val)
                    break

            if selected:
                continue

            # Try radio button or swatch button using touch tap
            btn_loc = form_loc.locator(
                f'input[type="radio"][value="{val}" i], button:has-text("{val}"), [data-value="{val}"], label:has-text("{val}")'
            ).first
            if await btn_loc.count() > 0 and await btn_loc.is_visible():
                try:
                    if mobile_config.get("has_touch") and hasattr(page, "touchscreen"):
                        box = await btn_loc.bounding_box()
                        if box:
                            cx = box["x"] + box["width"] / 2
                            cy = box["y"] + box["height"] / 2
                            await page.touchscreen.tap(cx, cy)
                        else:
                            await btn_loc.click(timeout=2000)
                    else:
                        await btn_loc.click(timeout=2000)

                    # Verify selection state strictly in DOM after click
                    is_active = await btn_loc.evaluate("""
                        el => {
                            if (el.checked) return true;
                            if (el.getAttribute('aria-checked') === 'true') return true;
                            if (el.getAttribute('aria-selected') === 'true') return true;
                            if (el.getAttribute('aria-pressed') === 'true') return true;
                            if (el.classList.contains('selected') || el.classList.contains('active')) return true;
                            if (el.tagName.toLowerCase() === 'label') {
                                const inp = document.getElementById(el.getAttribute('for')) || el.querySelector('input');
                                if (inp && inp.checked) return true;
                            }
                            return false;
                        }
                    """)
                    if is_active:
                        selected = True
                        proven_dom_options[opt_name] = str(val)
                except Exception:
                    pass

            if not selected and len(variant_options) > 0:
                disabled_elem = form_loc.locator(f'[value="{val}"][disabled], button:has-text("{val}")[disabled]').first
                if await disabled_elem.count() > 0:
                    return False, f"Option element '{opt_name}={val}' is disabled or unselectable in mobile UI", proven_dom_options

        all_proven = len(option_names) == 0 or len(proven_dom_options) == len(option_names)
        if not all_proven and len(option_names) > 0:
            return False, f"Option selection in DOM could not be proven for all options ({proven_dom_options})", proven_dom_options

        return True, "", proven_dom_options

    async def _satisfy_required_consents(self, page, main_form, mobile_config: dict[str, Any]) -> int:
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
        """Locate visible enabled Add to Cart button supporting multi-language themes."""
        form_loc = main_form if await main_form.count() > 0 else page
        selectors = [
            'button[name="add"]:visible:not([disabled])',
            'button.add-to-cart:visible:not([disabled])',
            'button[type="submit"]:has-text("add"):visible:not([disabled])',
            'button[type="submit"]:has-text("bag"):visible:not([disabled])',
            'button:has-text("ADD TO CART"):visible:not([disabled])',
            'button:has-text("ADD TO BAG"):visible:not([disabled])',
            'button:has-text("AJOUTER AU PANIER"):visible:not([disabled])',
            'button:has-text("IN DEN WARENKORB"):visible:not([disabled])',
            'button:has-text("AÑADIR AL CARRITO"):visible:not([disabled])',
            'button:has-text("AGGIUNGI AL CARRELLO"):visible:not([disabled])',
            '[data-add-to-cart]:visible:not([disabled])',
            '.sticky-atc button:visible:not([disabled])',
            '#sticky-atc button:visible:not([disabled])',
        ]
        for sel in selectors:
            btn = form_loc.locator(sel).first
            if await btn.count() > 0:
                return btn
        # Fallback to page level check if main_form failed
        if main_form != page:
            for sel in selectors:
                btn = page.locator(sel).first
                if await btn.count() > 0:
                    return btn
        return None

    async def _find_alternative_unblocked_atc_button(self, page, primary_button):
        """Find an unblocked alternative ATC button on the page (e.g. sticky CTA or alternative form button)."""
        all_buttons = page.locator('button[name="add"], button.add-to-cart, [data-add-to-cart], .sticky-atc button, #sticky-atc button')
        count = await all_buttons.count()
        for i in range(count):
            btn = all_buttons.nth(i)
            if await btn.is_visible() and not (await btn.is_disabled()):
                is_blocked, _ = await self._is_button_blocked_by_overlay(page, btn)
                if not is_blocked:
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
            logger.debug("Overlay check note: %s", e)
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
            logger.debug("Cart snapshot fetch note: %s", e)
            meta["status"] = "request_error"

        return meta, items

    async def _take_screenshot(self, page, tag: str, target_locator=None) -> Optional[str]:
        """Take screenshot scoped strictly to product form / fault element with non-mutative visual CSS PII masking. Returns None if safe redaction or element scoping cannot be guaranteed."""
        os.makedirs("reports/screenshots", exist_ok=True)
        filename = f"mobile_blocker_audit_{tag}_{int(datetime.now().timestamp())}.png"
        path = os.path.join("reports/screenshots", filename)

        try:
            # Mask form fields and text elements visually without mutating input.value or textarea.value
            mask_success = await page.evaluate("""
                () => {
                    try {
                        const fieldSelector = 'input[type="text"], input[type="email"], input[type="tel"], input[type="search"], textarea, [name*="properties"], [data-pii], .user-pii, .pii-data, [data-user-pii]';
                        document.querySelectorAll(fieldSelector).forEach(el => {
                            el.dataset.origBg = el.style.backgroundColor || '';
                            el.dataset.origColor = el.style.color || '';
                            el.dataset.origFilter = el.style.filter || '';
                            el.style.backgroundColor = '#000000';
                            el.style.color = '#000000';
                            el.style.filter = 'blur(10px) brightness(0)';
                        });

                        const textElements = document.querySelectorAll('p, span, div, td, li, a, h1, h2, h3, h4, h5, h6, [data-pii-text], strong, em, b');
                        textElements.forEach(el => {
                            if (el.children.length === 0 && el.textContent && el.textContent.trim().length > 0) {
                                const parentForm = el.closest('form');
                                if (!parentForm || el.matches('[data-pii-text], .user-info, .customer-info') || !['add to cart', 'ajouter au panier', 'small', 'large', 'medium'].includes(el.textContent.trim().toLowerCase())) {
                                    el.dataset.origBg = el.style.backgroundColor || '';
                                    el.dataset.origColor = el.style.color || '';
                                    el.dataset.origFilter = el.style.filter || '';
                                    el.style.backgroundColor = '#000000';
                                    el.style.color = '#000000';
                                    el.style.filter = 'blur(10px) brightness(0)';
                                }
                            }
                        });
                        return true;
                    } catch (e) {
                        return false;
                    }
                }
            """)

            if not mask_success:
                logger.debug("Failed to apply visual PII masking; suppressing screenshot to prevent leak.")
                return None

            element_to_capture = None
            if target_locator and await target_locator.count() > 0:
                element_to_capture = target_locator.first
            else:
                form = page.locator('form[action*="/cart/add"], [data-type="add-to-cart-form"]').first
                if await form.count() > 0:
                    element_to_capture = form

            if element_to_capture and await element_to_capture.is_visible():
                await element_to_capture.screenshot(path=path)
            else:
                logger.debug("Product form / fault element not directly isolated; suppressing full page screenshot.")
                return None

            return path
        except Exception as e:
            logger.debug("Screenshot capture failed or excluded safely: %s", e)
            return None
        finally:
            # Guaranteed style restoration without mutating input or textarea values
            try:
                await page.evaluate("""
                    () => {
                        document.querySelectorAll('[data-orig-bg]').forEach(el => {
                            el.style.backgroundColor = el.dataset.origBg;
                            el.style.color = el.dataset.origColor;
                            el.style.filter = el.dataset.origFilter;
                            delete el.dataset.origBg;
                            delete el.dataset.origColor;
                            delete el.dataset.origFilter;
                        });
                    }
                """)
            except Exception:
                pass
