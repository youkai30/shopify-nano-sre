"""Shopify Checkout Blocker Auditor Skill - Phase 6 Checkout Blockers."""

import asyncio
import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from nano_sre.agent.core import Skill, SkillResult
from nano_sre.agent.privacy import Redactor

logger = logging.getLogger(__name__)

_redactor = Redactor()


class PaymentGuardrailException(Exception):
    """Raised when an illegal attempt to trigger order completion or live payment gateway is intercepted."""
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_value(val: Any) -> Any:
    """Recursively redact strings, dicts, and lists without corrupting local screenshot file paths."""
    if isinstance(val, str):
        if val.startswith("reports/") or val.startswith("phase6_") or val.startswith("verification_output/") or ("/" in val and val.endswith(".png")):
            return val
        s = _redactor.redact_text(val)
        s = re.sub(r"\b[a-f0-9]{32,64}\b", "[REDACTED_TOKEN]", s, flags=re.IGNORECASE)
        return s
    if isinstance(val, list):
        return [_redact_value(item) for item in val]
    if isinstance(val, dict):
        return {k: _redact_value(v) for k, v in val.items()}
    return val


def parse_price_cents(text: str) -> Optional[int]:
    """Parse a price string into exact integer cents using Decimal, safely ignoring discount callouts and rejecting negative values."""
    if not text or not isinstance(text, str):
        return None

    if re.search(r"-\s*[\$£€¥]?\d+|[\$£€¥]?\s*-\d+", text):
        return None

    clean_text = re.sub(r"\b(?:save|off)\s*\d+\s*%", "", text, flags=re.IGNORECASE)
    clean_text = re.sub(r"\b\d+\s*%\s*(?:save|off)\b", "", clean_text, flags=re.IGNORECASE)

    match = re.search(r"(?:[\$£€¥]\s*)?\d+(?:[.,]\d+)*(?:\s*[\$£€¥])?", clean_text)
    if not match:
        return None

    num_match = re.search(r"\d+(?:[.,]\d+)*", match.group(0))
    if not num_match:
        return None

    num_str = num_match.group(0)

    if "." in num_str and "," in num_str:
        last_dot = num_str.rfind(".")
        last_comma = num_str.rfind(",")
        if last_dot > last_comma:
            clean = num_str.replace(",", "")
        else:
            clean = num_str.replace(".", "").replace(",", ".")
    elif "," in num_str:
        parts = num_str.split(",")
        if len(parts) == 2 and len(parts[1]) == 2:
            clean = num_str.replace(",", ".")
        elif len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
            clean = num_str.replace(",", "")
        elif len(parts) == 2 and len(parts[1]) == 3:
            clean = num_str.replace(",", "")
        else:
            clean = num_str.replace(",", ".")
    elif "." in num_str:
        parts = num_str.split(".")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            clean = num_str
        elif len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
            clean = num_str.replace(".", "")
        else:
            clean = num_str
    else:
        clean = num_str

    try:
        dec = Decimal(clean)
        if dec < 0:
            return None
        if "." in clean:
            dec = dec.quantize(Decimal("0.01"))
            cents = int(dec * 100)
        else:
            cents = int(dec * 100)
        return cents
    except (InvalidOperation, ValueError):
        return None


def _get_locale_prefix_and_origin(url: str) -> tuple[str, str]:
    """Extract origin and locale prefix from URL."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    parts = [p for p in parsed.path.split("/") if p]
    locale = ""
    if parts and re.fullmatch(r"[a-zA-Z]{2}(?:-[a-zA-Z]{2})?", parts[0]):
        locale = f"/{parts[0]}"
    return origin, locale


def _sanitize_url(url: str) -> str:
    """Sanitize URL to remove tokens, keys, or credentials without breaking scheme/host/path."""
    if not url:
        return ""
    parsed = urlparse(url)
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc += f":{parsed.port}"
    scheme = parsed.scheme or "http"
    path = parsed.path or "/"
    q_params = parse_qs(parsed.query)
    safe_params = {k: v for k, v in q_params.items() if k.lower() not in ("token", "key", "cart_token", "secret", "auth")}
    clean_path = re.sub(r"/[a-f0-9]{32,64}", "/[REDACTED_TOKEN]", path)
    return f"{scheme}://{netloc}{clean_path}"


def _is_local_origin(url: str) -> bool:
    """Verify that URL points strictly to a local host origin."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return host in ("127.0.0.1", "localhost", "0.0.0.0", "::1")
    except Exception:
        return False


class ShopifyCheckoutBlockerAuditor(Skill):
    """Audits checkout blockers across cart readiness, cart-to-checkout UI transition, local checkout steps, shipping/totals, and payment step access."""

    LOCAL_MOCK_LIMITATION = (
        "Local mock checkout audit verifies DOM, network transitions, and local fixture steps up to payment step access, "
        "but does NOT prove compatibility with live Shopify checkout or payment gateways."
    )

    def name(self) -> str:
        return "shopify_checkout_blocker_auditor"

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
                    "limitation_disclaimer": self.LOCAL_MOCK_LIMITATION,
                    "checks": {},
                }),
            )

        # Enforce local scope before any interaction
        if not _is_local_origin(base_url) or not _is_local_origin(page.url or base_url):
            return SkillResult(
                skill_name=self.name(),
                status="WARN",
                summary="Target URL is outside local sandbox origin; checkout audit skipped for safety",
                details=_redact_value({
                    "reason_code": "NON_LOCAL_SCOPE_BLOCKED",
                    "steps": ["Local origin scope check failed"],
                    "limitation_disclaimer": self.LOCAL_MOCK_LIMITATION,
                    "checks": {},
                }),
            )

        salt = str(uuid.uuid4())[:8]

        res, run1_data = await self._run_single_audit_pass(context, page, base_url, salt)

        # 2-run local reproduction if first pass resulted in FAIL
        if res.status == "FAIL":
            logger.info("First audit pass returned FAIL. Running second isolated reproduction pass with fresh context/session...")
            res2_status = "UNKNOWN"
            run2_data = {}
            try:
                if hasattr(page, "context") and page.context and hasattr(page.context, "browser") and page.context.browser:
                    # Clean isolated context with fresh cookies under equivalent initial conditions (no auditor cart mutations)
                    new_context = await page.context.browser.new_context()
                    new_page = await new_context.new_page()

                    # Attach active route abortion guardrail BEFORE initial page navigation of pass 2
                    async def p2_guardrail_route(route):
                        req_path = urlparse(route.request.url).path.lower()
                        req_host = (urlparse(route.request.url).hostname or "").lower()
                        if req_host and not _is_local_origin(route.request.url):
                            await route.abort("blockedbyclient")
                            return
                        if any(p in req_path for p in ["/checkout/pay", "/orders/create", "/payments/charge", "/api/pay"]):
                            await route.abort("blockedbyclient")
                        else:
                            await route.continue_()

                    await new_page.route("**/*", p2_guardrail_route)

                    try:
                        await new_page.goto(base_url, wait_until="commit", timeout=60000)
                        try:
                            await new_page.wait_for_load_state("networkidle", timeout=3000)
                        except Exception:
                            pass

                        ctx2 = dict(context, page=new_page)
                        res2, run2_data = await self._run_single_audit_pass(ctx2, new_page, base_url, salt)
                        res2_status = res2.status
                    finally:
                        try:
                            await new_page.unroute("**/*", p2_guardrail_route)
                        except Exception:
                            pass
                        await new_context.close()
                else:
                    res2_status = "UNRESOLVED"
            except Exception as e:
                logger.debug("Isolated run 2 execution note: %s", e)

            init1 = run1_data.get("initial_item_count")
            init2 = run2_data.get("initial_item_count")

            items1 = sorted([(i.get("variant_id") or i.get("id"), i.get("key"), i.get("quantity")) for i in run1_data.get("cart_items", [])])
            items2 = sorted([(i.get("variant_id") or i.get("id"), i.get("key"), i.get("quantity")) for i in run2_data.get("cart_items", [])])

            if res2_status != "FAIL":
                res.status = "WARN"
                res.summary = f"Failure not reproduced on second pass (Run 1: FAIL, Run 2: {res2_status}). Classified as WARN."
                if isinstance(res.details, dict):
                    res.details["reproduction_summary"] = {
                        "confirmed": False,
                        "run1_status": "FAIL",
                        "run2_status": res2_status,
                        "run1_data": run1_data,
                        "run2_data": run2_data,
                    }
            elif init1 != init2 or items1 != items2:
                res.status = "WARN"
                res.summary = f"Reproduction runs had non-equivalent initial cart conditions (Run 1 items: {items1}, Run 2: {items2}). Classified as WARN."
                if isinstance(res.details, dict):
                    res.details["reproduction_summary"] = {
                        "confirmed": False,
                        "mismatch_reason": "Non-equivalent initial cart conditions or line item mismatch across runs",
                        "run1_data": run1_data,
                        "run2_data": run2_data,
                    }
            else:
                id1 = (
                    run1_data.get("target_product_id"),
                    run1_data.get("target_variant_id"),
                    run1_data.get("target_line_key"),
                    run1_data.get("failed_stage"),
                    run1_data.get("reason_code"),
                    run1_data.get("broken_element"),
                    run1_data.get("expected"),
                    run1_data.get("observed"),
                )
                id2 = (
                    run2_data.get("target_product_id"),
                    run2_data.get("target_variant_id"),
                    run2_data.get("target_line_key"),
                    run2_data.get("failed_stage"),
                    run2_data.get("reason_code"),
                    run2_data.get("broken_element"),
                    run2_data.get("expected"),
                    run2_data.get("observed"),
                )

                # ALL target identity fields AND fault fields are mandatory for confirmed=True (using is not None for zero preservation)
                req_fields_present = (
                    id1[0] is not None  # target_product_id
                    and id1[1] is not None  # target_variant_id
                    and id1[2] is not None  # target_line_key
                    and id1[3] is not None  # failed_stage
                    and id1[4] is not None  # reason_code
                    and id1[5] is not None  # broken_element
                    and id1[6] is not None  # expected
                    and id1[7] is not None  # observed
                )

                if id1 == id2 and req_fields_present:
                    if isinstance(res.details, dict):
                        res.details["reproduction_summary"] = {
                            "confirmed": True,
                            "failed_stage": run1_data.get("failed_stage"),
                            "reason_code": run1_data.get("reason_code"),
                            "run1_data": run1_data,
                            "run2_data": run2_data,
                        }
                else:
                    res.status = "WARN"
                    res.summary = f"Failure identity mismatch across reproduction runs or missing required identity details (Run 1: {run1_data.get('reason_code')}, Run 2: {run2_data.get('reason_code')}). Classified as WARN."
                    if isinstance(res.details, dict):
                        res.details["reproduction_summary"] = {
                            "confirmed": False,
                            "mismatch_reason": "Differing failure identity details or missing required identity fields across runs",
                            "run1_data": run1_data,
                            "run2_data": run2_data,
                        }

        return res

    async def _run_single_audit_pass(
        self, context: dict[str, Any], page: Any, base_url: str, salt: str
    ) -> tuple[SkillResult, dict[str, Any]]:
        steps = []
        checks: dict[str, dict[str, Any]] = {}
        pass_timestamp = _now()

        current_url = _sanitize_url(page.url or base_url)
        origin, locale = _get_locale_prefix_and_origin(current_url)

        # Active Payment Guardrail Interceptor via Playwright Route Abort
        order_pay_endpoints_called = []

        async def payment_guardrail_route(route):
            req_path = urlparse(route.request.url).path.lower()
            req_host = (urlparse(route.request.url).hostname or "").lower()

            # Enforce local scope on all network requests
            if req_host and not _is_local_origin(route.request.url):
                logger.warning("Active Sandbox Scope Guardrail: Blocked non-local request to %s", route.request.url)
                await route.abort("blockedbyclient")
                return

            if any(p in req_path for p in ["/checkout/pay", "/orders/create", "/payments/charge", "/api/pay"]):
                order_pay_endpoints_called.append(route.request.url)
                logger.warning("Payment Guardrail actively aborted request to payment endpoint: %s", route.request.url)
                await route.abort("blockedbyclient")
            else:
                await route.continue_()

        await page.route("**/*", payment_guardrail_route)

        try:
            try:
                await page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass

            expected_product_id = context.get("target_product_id")
            expected_variant_id = context.get("target_variant_id")
            expected_line_key = context.get("target_line_key")

            # --- STAGE 1: Cart Readiness & Line Item Identity ---
            cart_meta, cart_items = await self._fetch_cart_snapshot(page, origin, locale)

            if cart_meta["status"] not in ("empty", "nonempty") or cart_meta["item_count"] <= 0 or not cart_items:
                checks["cart_readiness"] = {
                    "status": "WARN",
                    "reason_code": "CART_NOT_READY",
                    "summary": f"Cart readiness unproven: cart is empty or snapshot failed (status={cart_meta['status']}, count={cart_meta['item_count']})",
                    "expected": "Positive item count with valid line items",
                    "observed": f"item_count={cart_meta['item_count']}, items_len={len(cart_items)}",
                }
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary=_redact_value("Cart is empty or unproven; checkout audit skipped"),
                    details=_redact_value({
                        "reason_code": "CART_NOT_READY",
                        "steps": steps + ["Cart readiness check failed: cart empty"],
                        "limitation_disclaimer": self.LOCAL_MOCK_LIMITATION,
                        "checks": checks,
                    }),
                ), {
                    "failed_stage": "cart_readiness",
                    "reason_code": "CART_NOT_READY",
                    "broken_element": "cart_state",
                    "expected": "Positive item count with valid line items",
                    "observed": f"item_count={cart_meta['item_count']}",
                    "initial_item_count": cart_meta["item_count"],
                    "cart_items": cart_items,
                }

            # Prove target line item identity matching product ID, variant ID, AND line key strictly
            matched_item = None
            if expected_variant_id is not None or expected_product_id is not None or expected_line_key is not None:
                for item in cart_items:
                    v_match = expected_variant_id is None or str(item.get("variant_id") or item.get("id")) == str(expected_variant_id)
                    p_match = expected_product_id is None or str(item.get("product_id")) == str(expected_product_id)
                    k_match = expected_line_key is None or str(item.get("key")) == str(expected_line_key)
                    if v_match and p_match and k_match and (item.get("quantity") or 0) > 0:
                        matched_item = item
                        break
            else:
                if len(cart_items) == 1 and (cart_items[0].get("quantity") or 0) > 0 and cart_items[0].get("key") is not None:
                    matched_item = cart_items[0]

            target_line_key = matched_item.get("key") if matched_item else None
            target_product_id = matched_item.get("product_id") if matched_item else None
            target_variant_id = (matched_item.get("variant_id") or matched_item.get("id")) if matched_item else None
            target_quantity = matched_item.get("quantity", 1) if matched_item else 0

            if not matched_item or target_quantity <= 0 or target_line_key is None:
                checks["cart_readiness"] = {
                    "status": "WARN",
                    "reason_code": "TARGET_LINE_UNPROVEN",
                    "summary": "Target line item identity or explicit line key unproven in cart snapshot",
                    "expected": f"Target variant {expected_variant_id} with positive quantity and explicit line key",
                    "observed": f"Available cart items: {[i.get('variant_id') or i.get('id') for i in cart_items]}",
                }
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary=_redact_value("Target line item unproven; checkout audit skipped"),
                    details=_redact_value({
                        "reason_code": "TARGET_LINE_UNPROVEN",
                        "steps": steps + ["Target line item readiness check failed"],
                        "limitation_disclaimer": self.LOCAL_MOCK_LIMITATION,
                        "checks": checks,
                    }),
                ), {
                    "failed_stage": "cart_readiness",
                    "reason_code": "TARGET_LINE_UNPROVEN",
                    "broken_element": "cart_line_item",
                    "expected": "Proven line item identity and line key",
                    "observed": "Unproven line item identity or key",
                    "initial_item_count": cart_meta["item_count"],
                    "cart_items": cart_items,
                }

            checks["cart_readiness"] = {
                "status": "PASS",
                "reason_code": "NONE",
                "summary": f"Cart verified ready with positive quantity ({target_quantity}) for target variant {target_variant_id}",
                "item_count": cart_meta["item_count"],
                "target_variant_id": target_variant_id,
                "target_product_id": target_product_id,
                "target_line_key": target_line_key,
            }
            steps.append(f"Cart readiness verified: target variant {target_variant_id}, quantity {target_quantity}")

            # --- STAGE 2: Transition from Cart to Checkout ---
            btn_loc, btn_err, btn_code = await self._find_checkout_ui_element(page)

            # Search for alternative valid UI path (e.g. express checkout button) if primary button is missing
            if not btn_loc:
                alt_btn = page.locator('button[data-shopify-express-checkout], .shopify-payment-button__button:visible, [data-testid="grid-cell"] button:visible').first
                if await alt_btn.count() > 0 and await alt_btn.is_visible():
                    btn_loc = alt_btn
                    btn_err = None
                    btn_code = None
                    steps.append("Using alternative valid express checkout UI path")

            if not btn_loc:
                checks["cart_to_checkout"] = {
                    "status": "FAIL",
                    "reason_code": btn_code or "BUTTON_MISSING",
                    "summary": f"Checkout UI transition failed: {btn_err}",
                    "broken_element": "checkout_button",
                    "expected": "Visible, enabled checkout button or alternative UI path",
                    "observed": btn_err or "No checkout element found",
                }
            else:
                overlay_blocked = await self._check_overlay_blocked(page, btn_loc)
                if overlay_blocked:
                    checks["cart_to_checkout"] = {
                        "status": "FAIL",
                        "reason_code": "BUTTON_OVERLAY_BLOCKED",
                        "summary": "Checkout button is blocked by an unhandled modal or overlay",
                        "broken_element": "overlay_element",
                        "expected": "Unobscured checkout button",
                        "observed": "Checkout button covered by overlay element",
                    }
                else:
                    request_sent = [False]

                    def on_atc_request(req):
                        req_path = urlparse(req.url).path.lower()
                        if "/checkout" in req_path or "/cart" in req_path:
                            request_sent[0] = True

                    page.on("request", on_atc_request)
                    try:
                        await btn_loc.click(timeout=5000)
                        try:
                            await page.wait_for_load_state("networkidle", timeout=3000)
                        except Exception:
                            pass
                    except Exception as e:
                        logger.debug("Checkout click exception: %s", e)
                    finally:
                        try:
                            page.remove_listener("request", on_atc_request)
                        except Exception:
                            pass

                    # Validate transition: Browser MUST prove visible checkout UI elements in DOM!
                    is_checkout_ui = await self._verify_checkout_entry(page)
                    if not is_checkout_ui:
                        if page.url.startswith("chrome-error://") or "500 Internal Server Error" in await page.content():
                            reason = "CHECKOUT_ERROR_PAGE"
                        elif not request_sent[0]:
                            reason = "CLICK_NO_ACTION"
                        elif "/cart" in page.url.lower():
                            reason = "CHECKOUT_REDIRECT_CART"
                        else:
                            reason = "CLICK_NO_ACTION"
                        checks["cart_to_checkout"] = {
                            "status": "FAIL",
                            "reason_code": reason,
                            "summary": f"Clicking checkout button did not reach recognizable checkout UI (current URL: {_sanitize_url(page.url)})",
                            "broken_element": "checkout_transition",
                            "expected": "Navigation to checkout page or drawer UI with visible checkout DOM elements",
                            "observed": f"Remained on non-checkout page or missing checkout DOM ({_sanitize_url(page.url)})",
                        }
                    else:
                        checks["cart_to_checkout"] = {
                            "status": "PASS",
                            "reason_code": "NONE",
                            "summary": f"Successfully transitioned to checkout UI ({_sanitize_url(page.url)})",
                        }

            if checks.get("cart_to_checkout", {}).get("status") == "FAIL":
                screenshot_path = await self._take_screenshot(page, "checkout_transition_failed")
                failed_reason = checks["cart_to_checkout"]["reason_code"]
                return SkillResult(
                    skill_name=self.name(),
                    status="FAIL",
                    summary=_redact_value(f"Checkout transition failed: {failed_reason}"),
                    details=_redact_value({
                        "reason_code": failed_reason,
                        "steps": steps,
                        "limitation_disclaimer": self.LOCAL_MOCK_LIMITATION,
                        "checks": checks,
                        "redacted_screenshot": screenshot_path,
                    }),
                ), {
                    "failed_stage": "cart_to_checkout",
                    "reason_code": failed_reason,
                    "broken_element": checks["cart_to_checkout"].get("broken_element"),
                    "expected": checks["cart_to_checkout"].get("expected"),
                    "observed": checks["cart_to_checkout"].get("observed"),
                    "target_product_id": target_product_id,
                    "target_variant_id": target_variant_id,
                    "target_line_key": target_line_key,
                    "initial_item_count": cart_meta["item_count"],
                    "cart_items": cart_items,
                }

            # --- STAGE 3: Local Checkout Steps ---
            steps.append("Verifying local checkout UI steps")

            # Check checkout entry DOM
            entry_ok = await self._verify_checkout_entry(page)
            checks["checkout_entry"] = {
                "status": "PASS" if entry_ok else "WARN",
                "reason_code": "NONE" if entry_ok else "ENTRY_UNPROVEN",
                "summary": "Recognizable checkout entry UI loaded" if entry_ok else "Checkout entry UI unproven",
                "expected": "Checkout form or container present in DOM",
                "observed": "Checkout DOM verified" if entry_ok else "Checkout DOM unproven",
            }

            # Fill synthetic dummy contact/address if inputs exist
            address_status = await self._fill_synthetic_address(page)
            checks["contact_address"] = address_status

            # Verify shipping options step
            shipping_status = await self._check_shipping_step(page)
            checks["shipping"] = shipping_status

            # Verify payment step access
            payment_access_status = await self._check_payment_step_access(page)
            checks["payment_step_access"] = payment_access_status

            screenshot_path = await self._take_screenshot(page, "checkout_summary")

            statuses = [c["status"] for c in checks.values()]
            failed_stage = None
            failed_reason_code = None

            if "FAIL" in statuses:
                overall_status = "FAIL"
                failed_stages = [k for k, v in checks.items() if v["status"] == "FAIL"]
                failed_stage = failed_stages[0]
                failed_reason_code = checks[failed_stage].get("reason_code", "FAIL")
                summary = f"Checkout blocker audit failed: {', '.join(failed_stages)}"
            elif "WARN" in statuses:
                overall_status = "WARN"
                warned_stages = [k for k, v in checks.items() if v["status"] == "WARN"]
                summary = f"Checkout audit completed with warnings: {', '.join(warned_stages)}"
            else:
                overall_status = "PASS"
                summary = "All local checkout steps passed successfully up to payment step access"

            run_meta = {
                "failed_stage": failed_stage,
                "reason_code": failed_reason_code or ("NONE" if overall_status == "PASS" else "WARN"),
                "broken_element": checks.get(failed_stage, {}).get("broken_element") if failed_stage else None,
                "expected": checks.get(failed_stage, {}).get("expected"),
                "observed": checks.get(failed_stage, {}).get("observed"),
                "target_product_id": target_product_id,
                "target_variant_id": target_variant_id,
                "target_line_key": target_line_key,
                "initial_item_count": cart_meta["item_count"],
                "cart_items": cart_items,
                "order_pay_endpoints_called": order_pay_endpoints_called,
                "timestamp": pass_timestamp,
            }

            return SkillResult(
                skill_name=self.name(),
                status=overall_status,
                summary=_redact_value(summary),
                details=_redact_value({
                    "reason_code": run_meta["reason_code"],
                    "steps": steps,
                    "limitation_disclaimer": self.LOCAL_MOCK_LIMITATION,
                    "checks": checks,
                    "redacted_screenshot": screenshot_path,
                    "pass_timestamp": pass_timestamp,
                    "order_pay_guardrail_verified": len(order_pay_endpoints_called) == 0,
                }),
            ), run_meta

        finally:
            try:
                await page.unroute("**/*", payment_guardrail_route)
            except Exception:
                pass

    async def _fetch_cart_snapshot(self, page, origin: str, locale: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Fetch safe cart.js snapshot with strict schema validation."""
        meta = {"status": "failed", "captured_at": _now(), "item_count": 0}
        items = []
        try:
            res = await page.request.get(f"{origin}{locale}/cart.js", timeout=10000)
            if res.ok:
                data = await res.json()
                if isinstance(data, dict):
                    cnt = data.get("item_count", 0)
                    meta["status"] = "empty" if cnt == 0 else "nonempty"
                    meta["item_count"] = cnt
                    items = data.get("items", [])
            else:
                meta["status"] = f"http_{res.status}"
        except Exception as e:
            logger.debug("Cart fetch exception: %s", e)
        return meta, items

    async def _find_checkout_ui_element(self, page) -> tuple[Optional[Any], Optional[str], Optional[str]]:
        """Locate visible, enabled checkout UI button or link."""
        selectors = [
            'button[name="checkout"]:visible:not([disabled])',
            'a[href*="/checkout"]:visible:not([disabled])',
            'input[name="checkout"]:visible:not([disabled])',
            'button:has-text("Checkout"):visible:not([disabled])',
            'button:has-text("CHECK OUT"):visible:not([disabled])',
            '[data-checkout-btn]:visible:not([disabled])',
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                return loc, None, None

        # Check if button exists but is disabled
        disabled_sel = 'button[name="checkout"][disabled], button:has-text("Checkout")[disabled]'
        if await page.locator(disabled_sel).count() > 0:
            return None, "Checkout button exists in DOM but is disabled", "BUTTON_DISABLED"

        return None, "No checkout button or link found in DOM", "BUTTON_MISSING"

    async def _check_overlay_blocked(self, page, btn_loc) -> bool:
        """Check if an overlay blocks interaction with the checkout button."""
        try:
            overlays = page.locator('.modal-backdrop:visible, .overlay:visible, [data-modal-overlay]:visible, .drawer-overlay:visible')
            if await overlays.count() > 0:
                is_blocked = await page.evaluate("""
                    el => {
                        const rect = el.getBoundingClientRect();
                        const topEl = document.elementFromPoint(rect.left + rect.width/2, rect.top + rect.height/2);
                        return topEl && !el.contains(topEl) && topEl !== el;
                    }
                """, await btn_loc.element_handle())
                return is_blocked
        except Exception:
            pass
        return False

    async def _verify_checkout_entry(self, page) -> bool:
        """Verify that page contains recognizable visible checkout entry UI elements. Excludes cart page forms."""
        try:
            if "/cart" in page.url.lower() and "/checkout" not in page.url.lower():
                return False

            selectors = [
                '#checkout',
                '.checkout-container',
                'input[name*="email"]',
                '.step[data-step]',
                'h1:has-text("Checkout")',
                'h2:has-text("Checkout")',
            ]
            for sel in selectors:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    return True
        except Exception:
            pass
        return False

    async def _fill_synthetic_address(self, page) -> dict[str, Any]:
        """Fill synthetic dummy contact/address fields if present on local checkout form. Reclassify explicit address validation errors as WARN."""
        try:
            email_field = page.locator('input[type="email"]:visible, input[name*="email"]:visible').first
            if await email_field.count() > 0:
                await email_field.fill("test.user@example.test")

            fname_field = page.locator('input[name*="first_name"]:visible, input[name*="firstname"]:visible').first
            if await fname_field.count() > 0:
                await fname_field.fill("Jane")

            lname_field = page.locator('input[name*="last_name"]:visible, input[name*="lastname"]:visible').first
            if await lname_field.count() > 0:
                await lname_field.fill("Doe")

            addr_field = page.locator('input[name*="address1"]:visible').first
            if await addr_field.count() > 0:
                await addr_field.fill("123 Synthetic Way")

            city_field = page.locator('input[name*="city"]:visible').first
            if await city_field.count() > 0:
                await city_field.fill("Metropolis")

            zip_field = page.locator('input[name*="zip"]:visible, input[name*="postal_code"]:visible').first
            if await zip_field.count() > 0:
                await zip_field.fill("10001")

            error_el = page.locator('.field__message--error:visible, .address-error:visible, [data-address-error]:visible').first
            if await error_el.count() > 0:
                err_text = await error_el.text_content()
                return {
                    "status": "WARN",
                    "reason_code": "ADDRESS_VALIDATION_NOTICE",
                    "summary": f"Synthetic contact/address entry notice: {err_text}",
                    "expected": "Accepted synthetic address entry",
                    "observed": err_text,
                }

            # Require physical address inputs (address1 AND zip) AND UI step transition/continuation proof
            has_email = await email_field.count() > 0
            has_addr = await addr_field.count() > 0
            has_zip = await zip_field.count() > 0

            if not (has_email and has_addr and has_zip):
                return {
                    "status": "WARN",
                    "reason_code": "ADDRESS_INPUT_UNPROVEN",
                    "summary": "Synthetic contact/address entry unproven in DOM (physical address inputs missing or incomplete)",
                }

            # Check for order completion buttons that MUST NEVER be clicked
            order_pay_btns = page.locator(
                'button:has-text("Place order"):visible, button:has-text("Pay now"):visible, '
                'button:has-text("Complete order"):visible, button:has-text("Submit order"):visible, '
                'button:has-text("Pay"):visible, [data-pay-button]:visible'
            )

            # Locate EXPLICIT address continuation buttons ONLY (never generic button[type="submit"])
            continue_btn_selectors = [
                'button[data-continue-to-shipping]:visible',
                'button[name="continue_to_shipping"]:visible',
                'button:has-text("Continue to shipping"):visible',
                'button:has-text("Continue to delivery"):visible',
                'button:has-text("Continue to payment"):visible',
                '[data-address-continue-btn]:visible',
            ]

            continue_btn = None
            for sel in continue_btn_selectors:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    continue_btn = loc
                    break

            # If no explicit address continuation button is found or if button function is ambiguous -> return WARN without clicking
            if not continue_btn:
                return {
                    "status": "WARN",
                    "reason_code": "ADDRESS_INPUT_UNPROVEN",
                    "summary": "Synthetic contact/address fields filled, but no explicit address continuation button found; click skipped for safety",
                }

            # Capture baseline count of next step elements BEFORE clicking continue
            initial_next_step_count = await page.locator(
                '.shipping-option:visible, [data-shipping-method]:visible, .step--current[data-step="shipping_method"]:visible, [data-shipping-methods-container]:visible'
            ).count()

            # Execute click on proven continue button
            try:
                await continue_btn.click(timeout=3000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass
            except Exception as e:
                return {
                    "status": "WARN",
                    "reason_code": "ADDRESS_INPUT_UNPROVEN",
                    "summary": f"Contact/address continuation button click failed: {e}",
                }

            # Verify step transition: next step element count increased or new step container rendered after click
            post_next_step_count = await page.locator(
                '.shipping-option:visible, [data-shipping-method]:visible, .step--current[data-step="shipping_method"]:visible, [data-shipping-methods-container]:visible'
            ).count()

            # Prove that step state transitioned or new shipping/continuation UI loaded after the action
            step_transition_proven = (post_next_step_count > initial_next_step_count) or (
                await page.locator('.step--current[data-step="shipping_method"]:visible, [data-step="shipping_method"].is-active').count() > 0
            )

            if step_transition_proven:
                return {
                    "status": "PASS",
                    "reason_code": "NONE",
                    "summary": "Synthetic contact/address inputs accepted and step continuation verified successfully",
                }

            return {
                "status": "WARN",
                "reason_code": "ADDRESS_INPUT_UNPROVEN",
                "summary": "Contact/address fields entered, but UI step transition was unproven following continue button click",
            }
        except Exception as e:
            return {
                "status": "WARN",
                "reason_code": "ADDRESS_INPUT_EXCEPTION",
                "summary": f"Synthetic address filling note: {e}",
            }

    async def _check_shipping_step(self, page) -> dict[str, Any]:
        """Check shipping options and ensure no infinite loading spinner or shipping ineligibility blocks local checkout."""
        try:
            ineligible_el = page.locator('.shipping-error:visible, .notice--error:visible, [data-shipping-error]:visible').first
            if await ineligible_el.count() > 0:
                msg = await ineligible_el.text_content()
                return {
                    "status": "WARN",
                    "reason_code": "SHIPPING_INELIGIBLE",
                    "summary": f"Address or items ineligible for local shipping options: {msg}",
                    "expected": "Valid shipping options loaded",
                    "observed": msg,
                }

            spinner = page.locator('.spinner:visible, .loading-spinner:visible, [data-shipping-loading]:visible').first
            if await spinner.count() > 0:
                start_spin = datetime.now()
                while (datetime.now() - start_spin).total_seconds() < 3.0:
                    if await spinner.count() == 0 or not await spinner.is_visible():
                        break
                    await asyncio.sleep(0.3)

                # Require affirmative proof of address eligibility and valid response before issuing FAIL
                shipping_opts = page.locator('.shipping-option:visible, [data-shipping-method]:visible, input[name*="shipping"]:visible, .content-box__row:visible').first
                has_eligible_options = await shipping_opts.count() > 0

                if await spinner.count() > 0 and await spinner.is_visible():
                    if has_eligible_options:
                        return {
                            "status": "FAIL",
                            "reason_code": "SHIPPING_SPINNER_TIMEOUT",
                            "summary": "Shipping options loading spinner hung indefinitely despite eligible shipping methods present",
                            "broken_element": "shipping_loading_spinner",
                            "expected": "Shipping options loaded and spinner dismissed",
                            "observed": "Loading spinner hung past timeout with eligible methods present",
                        }
                    else:
                        return {
                            "status": "WARN",
                            "reason_code": "ADDRESS_VALIDATION_NOTICE",
                            "summary": "Shipping options spinner timed out without affirmative proof of address eligibility",
                            "expected": "Affirmative proof of shipping eligibility",
                            "observed": "Spinner pending without confirmed eligibility",
                        }

            shipping_opts = page.locator('.shipping-option:visible, [data-shipping-method]:visible, input[name*="shipping"]:visible, .content-box__row:visible').first
            if await shipping_opts.count() > 0:
                return {
                    "status": "PASS",
                    "reason_code": "NONE",
                    "summary": "Shipping step options verified visible in DOM",
                }

            return {
                "status": "WARN",
                "reason_code": "SHIPPING_OPTIONS_UNPROVEN",
                "summary": "Shipping options DOM elements unproven in current view",
            }
        except Exception as e:
            return {
                "status": "WARN",
                "reason_code": "SHIPPING_STEP_WARN",
                "summary": f"Shipping step check note: {e}",
            }

    async def _check_payment_step_access(self, page) -> dict[str, Any]:
        """Verify payment step elements are visible without attempting order completion or entering payment credentials."""
        try:
            payment_selectors = [
                '#payment-methods:visible',
                '.payment-step:visible',
                '[data-payment-option]:visible',
                'input[name*="payment"]:visible',
                '.card-fields-container:visible',
                'h2:has-text("Payment"):visible',
            ]
            found = False
            for sel in payment_selectors:
                if await page.locator(sel).count() > 0:
                    found = True
                    break

            return {
                "status": "PASS" if found else "WARN",
                "reason_code": "NONE" if found else "PAYMENT_STEP_UNPROVEN",
                "summary": "Payment step elements verified visible in DOM" if found else "Payment step elements unproven in DOM",
            }
        except Exception as e:
            return {
                "status": "WARN",
                "reason_code": "PAYMENT_STEP_WARN",
                "summary": f"Payment step access check note: {e}",
            }

    async def _take_screenshot(self, page, tag: str) -> Optional[str]:
        """Take screenshot with non-mutative visual CSS PII masking. Returns None if safe redaction or element scoping cannot be guaranteed."""
        os.makedirs("reports/screenshots", exist_ok=True)
        filename = f"checkout_blocker_{tag}_{int(datetime.now().timestamp())}.png"
        path = os.path.join("reports/screenshots", filename)

        try:
            mask_success = await page.evaluate("""
                () => {
                    try {
                        const fieldSelector = 'input[type="text"], input[type="email"], input[type="tel"], input[type="search"], textarea, [name*="address"], [name*="first_name"], [name*="last_name"], [data-pii]';
                        document.querySelectorAll(fieldSelector).forEach(el => {
                            el.dataset.origBg = el.style.backgroundColor || '';
                            el.dataset.origColor = el.style.color || '';
                            el.dataset.origFilter = el.style.filter || '';
                            el.style.backgroundColor = '#000000';
                            el.style.color = '#000000';
                            el.style.filter = 'blur(10px) brightness(0)';
                        });
                        return true;
                    } catch (e) {
                        return false;
                    }
                }
            """)

            if not mask_success:
                return None

            checkout_container = page.locator('#checkout, .cart-drawer, form[action*="/checkout"], body').first
            if await checkout_container.count() > 0 and await checkout_container.is_visible():
                await checkout_container.screenshot(path=path)
            else:
                return None

            if os.path.exists(path) and os.path.getsize(path) > 0:
                return path
            return None
        except Exception as e:
            logger.debug("Screenshot capture failed: %s", e)
            return None
        finally:
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
