"""Shopify Cart Consistency Auditor Skill - Phase 5 Cart Consistency."""

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_value(val: Any) -> Any:
    """Recursively redact strings, dicts, and lists without corrupting local screenshot file paths."""
    if isinstance(val, str):
        if val.startswith("reports/") or val.startswith("phase5_") or val.startswith("verification_output/") or ("/" in val and val.endswith(".png")):
            return val
        return _redactor.redact_text(val)
    if isinstance(val, list):
        return [_redact_value(item) for item in val]
    if isinstance(val, dict):
        return {k: _redact_value(v) for k, v in val.items()}
    return val


def _first_not_none(*args: Any) -> Any:
    """Return first argument that is not None, explicitly preserving 0 or False."""
    for arg in args:
        if arg is not None:
            return arg
    return None


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


def _extract_currency_symbol(text: str) -> Optional[str]:
    """Extract explicit currency ISO code (e.g., USD, CAD, EUR, GBP, AUD, JPY) from text. Generic symbols like '$' alone do not prove currency."""
    if not text:
        return None
    match = re.search(r"\b(USD|EUR|GBP|CAD|AUD|JPY|CHF|NZD|HKD|SGD)\b", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None


async def _extract_selling_plan_id(item_loc: Any, txt: str) -> tuple[Optional[str], bool]:
    """Extract standalone selling plan ID from DOM attributes or explicit 'Plan <id>' labels.
    Returns (extracted_plan_id_str, is_proven_label).
    Numbers appearing in prices or product titles alone without plan labels are NOT selling plans.
    """
    try:
        sp_attr = item_loc.locator('[data-selling-plan-id], [data-selling-plan], input[name*="selling_plan"]').first
        if await sp_attr.count() > 0:
            val = await sp_attr.evaluate("""
                el => el.getAttribute('data-selling-plan-id') || el.getAttribute('data-selling-plan') || el.value
            """)
            if val and str(val).strip().isdigit():
                return str(val).strip(), True
    except Exception:
        pass

    matches = re.findall(r"\b(?:selling\s*plan|plan|subscription)\s*#?\s*:?\s*(\d+)\b", txt, flags=re.IGNORECASE)
    if matches:
        return str(matches[0]).strip(), True

    return None, False


def _hash_properties(props: Any, salt: str = "nano_sre_salt") -> str:
    """Hash properties into a deterministic safe string so 'Alice' != 'David' without logging raw PII. Includes '_' properties."""
    if not isinstance(props, dict) or not props:
        return "empty_props"
    sorted_items = sorted((str(k).strip(), str(v).strip()) for k, v in props.items())
    raw_str = salt + ":" + ";".join(f"{k}={v}" for k, v in sorted_items)
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()[:16]


def _build_line_key(variant_id: int, props: Any, selling_plan: Any = None, salt: str = "nano_sre_salt") -> str:
    """Build a complete line identity key: (variant_id, hashed_props, selling_plan)."""
    p_hash = _hash_properties(props, salt)
    plan_str = str(selling_plan) if selling_plan is not None else "none"
    return f"v_{variant_id}_p_{p_hash}_sp_{plan_str}"


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


class ShopifyCartConsistencyAuditor(Skill):
    """Audits cart consistency across PDP variant selection, add-to-cart network dispatches, Ajax cart state, and Cart Drawer/Page UI."""

    def name(self) -> str:
        return "shopify_cart_consistency_auditor"

    async def run(self, context: dict[str, Any]) -> SkillResult:
        page = context.get("page")
        base_url: str = context.get("base_url", "")
        enable_quantity_update: bool = context.get("enable_quantity_update", False)

        if not page or not base_url:
            return SkillResult(
                skill_name=self.name(),
                status="WARN",
                summary="Missing page or base_url in context",
                details=_redact_value({
                    "reason_code": "MISSING_CONTEXT",
                    "steps": ["Initialization failed: missing page or base_url"],
                    "checks": {},
                }),
            )

        salt = str(uuid.uuid4())[:8]

        res, run1_data = await self._run_single_audit_pass(context, page, base_url, enable_quantity_update, salt)

        # 2-run local reproduction if first pass resulted in FAIL
        if res.status == "FAIL":
            logger.info("First audit pass returned FAIL. Running second isolated reproduction pass with fresh context/session...")
            res2_status = "UNKNOWN"
            run2_data = {}
            try:
                # Create isolated browser context with clean cookies for run 2
                if hasattr(page, "context") and page.context and hasattr(page.context, "browser") and page.context.browser:
                    new_context = await page.context.browser.new_context()
                    new_page = await new_context.new_page()
                    try:
                        await new_page.goto(base_url, wait_until="commit", timeout=60000)
                        await asyncio.sleep(0.5)
                        ctx2 = dict(context, page=new_page)
                        res2, run2_data = await self._run_single_audit_pass(ctx2, new_page, base_url, enable_quantity_update, salt)
                        res2_status = res2.status
                    finally:
                        await new_context.close()
                else:
                    await page.goto(base_url, wait_until="commit", timeout=60000)
                    await asyncio.sleep(1)
                    res2, run2_data = await self._run_single_audit_pass(context, page, base_url, enable_quantity_update, salt)
                    res2_status = res2.status
            except Exception as e:
                logger.debug("Isolated run 2 execution note: %s", e)

            # Require matching initial conditions and complete failure identity
            init1 = run1_data.get("initial_item_count")
            init2 = run2_data.get("initial_item_count")

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
            elif init1 != init2:
                res.status = "WARN"
                res.summary = f"Reproduction runs had non-equivalent initial cart states (Run 1 initial items: {init1}, Run 2: {init2}). Classified as WARN."
                if isinstance(res.details, dict):
                    res.details["reproduction_summary"] = {
                        "confirmed": False,
                        "mismatch_reason": "Non-equivalent initial cart conditions",
                        "run1_data": run1_data,
                        "run2_data": run2_data,
                    }
            else:
                # Compare full failure identity: handle, target_vid, line_identity, check, reason_code, expected, observed, observed_delta
                id1 = (
                    run1_data.get("product_handle"),
                    run1_data.get("target_variant_id"),
                    run1_data.get("target_line_identity"),
                    run1_data.get("failed_check"),
                    run1_data.get("reason_code"),
                    run1_data.get("expected"),
                    run1_data.get("observed"),
                    run1_data.get("observed_delta"),
                )
                id2 = (
                    run2_data.get("product_handle"),
                    run2_data.get("target_variant_id"),
                    run2_data.get("target_line_identity"),
                    run2_data.get("failed_check"),
                    run2_data.get("reason_code"),
                    run2_data.get("expected"),
                    run2_data.get("observed"),
                    run2_data.get("observed_delta"),
                )

                req_fields_present = (
                    id1[0] is not None
                    and id1[1] is not None
                    and id1[3] is not None
                    and id1[4] is not None
                )

                if id1 == id2 and req_fields_present:
                    if isinstance(res.details, dict):
                        res.details["reproduction_summary"] = {
                            "confirmed": True,
                            "failed_check": run1_data.get("failed_check"),
                            "reason_code": run1_data.get("reason_code"),
                            "run1_data": run1_data,
                            "run2_data": run2_data,
                        }
                else:
                    res.status = "WARN"
                    res.summary = f"Failure identity mismatch across reproduction runs (Run 1: {run1_data.get('reason_code')}, Run 2: {run2_data.get('reason_code')}). Classified as WARN."
                    if isinstance(res.details, dict):
                        res.details["reproduction_summary"] = {
                            "confirmed": False,
                            "mismatch_reason": "Differing failure identity details between runs or missing required identity fields",
                            "run1_data": run1_data,
                            "run2_data": run2_data,
                        }

        return res

    async def _run_single_audit_pass(
        self, context: dict[str, Any], page: Any, base_url: str, enable_quantity_update: bool, salt: str
    ) -> tuple[SkillResult, dict[str, Any]]:
        steps = []
        checks: dict[str, dict[str, Any]] = {}
        pass_timestamp = _now()

        try:
            current_url = _sanitize_url(page.url or base_url)
            origin, locale = _get_locale_prefix_and_origin(current_url)
            handle = _extract_handle_from_url(current_url)

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
                            current_url = _sanitize_url(page.url)
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
                            current_url = _sanitize_url(page.url)
                            origin, locale = _get_locale_prefix_and_origin(current_url)
                except Exception as e:
                    logger.debug("Failed products.json lookup: %s", e)

            if not handle:
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary=_redact_value("Could not discover a product handle to audit cart consistency"),
                    details=_redact_value({
                        "reason_code": "PRODUCT_NOT_DISCOVERED",
                        "steps": steps,
                        "checks": {},
                    }),
                ), {"failed_check": None, "reason_code": "PRODUCT_NOT_DISCOVERED", "timestamp": pass_timestamp, "product_handle": None}

            steps.append(f"Auditing product handle: {handle}")

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
                    summary=_redact_value(f"Could not fetch product JSON from {ajax_url}"),
                    details=_redact_value({
                        "reason_code": "PRODUCT_JSON_UNAVAILABLE",
                        "steps": steps,
                        "checks": {},
                    }),
                ), {"failed_check": None, "reason_code": "PRODUCT_JSON_UNAVAILABLE", "timestamp": pass_timestamp, "product_handle": handle}

            variants_list = product_data.get("variants", [])
            options_list = product_data.get("options", [])

            target_variant = None
            for v in variants_list:
                if v.get("available", True):
                    target_variant = dict(v)
                    target_variant["product_handle"] = handle
                    target_variant["product_title"] = product_data.get("title", "")
                    break

            if not target_variant:
                return SkillResult(
                    skill_name=self.name(),
                    status="PASS",
                    summary=_redact_value("Product has no available variants to test cart consistency; non-blocker"),
                    details=_redact_value({
                        "reason_code": "NONE",
                        "steps": steps + ["All variants marked unavailable"],
                        "checks": {},
                    }),
                ), {"failed_check": None, "reason_code": "NONE", "timestamp": pass_timestamp, "product_handle": handle}

            main_form = page.locator(
                'form[action*="/cart/add"], form.prd-ProductOffers_Form, [data-type="add-to-cart-form"]'
            ).first
            if await main_form.count() == 0:
                main_form = page.locator('form:has(button[name="add"]), section[data-product-single-media-group]').first

            # Check unfulfilled customization / selling plans
            unfulfilled_req = await self._check_unfulfilled_customization_fields(page, main_form)
            if unfulfilled_req:
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary=_redact_value(f"Mandatory customization or selling plan requirement unfulfilled ({unfulfilled_req})"),
                    details=_redact_value({
                        "reason_code": "UNFULFILLED_CUSTOMIZATION_REQUIREMENT",
                        "steps": steps + [f"Unfulfilled requirement: {unfulfilled_req}"],
                        "checks": {},
                    }),
                ), {"failed_check": None, "reason_code": "UNFULFILLED_CUSTOMIZATION_REQUIREMENT", "timestamp": pass_timestamp, "product_handle": handle}

            # Select variant in DOM and verify active selection state
            option_success, option_error, proven_dom_options = await self._select_variant_options_in_dom(
                page, main_form, target_variant, options_list
            )
            if not option_success:
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary=_redact_value(f"Variant selection unproven in DOM: {option_error}"),
                    details=_redact_value({
                        "reason_code": "UNPROVEN_OPTION_PATTERN",
                        "steps": steps + [f"Option selection issue: {option_error}"],
                        "checks": {},
                    }),
                ), {"failed_check": None, "reason_code": "UNPROVEN_OPTION_PATTERN", "timestamp": pass_timestamp, "product_handle": handle}

            # Read PDP Quantity requested in UI
            requested_ui_qty = await self._get_requested_quantity_from_dom(page, main_form)

            # Read PDP price displayed in DOM (do NOT fallback to JSON and claim DOM price verified)
            pdp_dom_price_cents, pdp_currency, pdp_dom_proven = await self._get_pdp_dom_price_and_currency(page, main_form, target_variant)

            # Read Pre-Cart State via Ajax API
            meta_before, items_before = await self._fetch_cart_snapshot(page, origin, locale)

            if meta_before["status"] not in ("empty", "nonempty"):
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary=_redact_value(f"Pre-cart read failed (status={meta_before['status']})"),
                    details=_redact_value({
                        "reason_code": "UNRESOLVED_PRE_CART_STATE",
                        "steps": steps + [f"Pre-cart status: {meta_before['status']}"],
                        "checks": {},
                    }),
                ), {"failed_check": None, "reason_code": "UNRESOLVED_PRE_CART_STATE", "timestamp": pass_timestamp, "product_handle": handle}

            # Setup ATC network request interception
            add_request_data = {
                "captured": False,
                "status": None,
                "req_variant_id": None,
                "req_quantity": None,  # Do not default unparseable payload to 1
                "req_properties": {},
                "req_selling_plan": None,
                "res_variant_id": None,
                "res_items": [],
                "unparseable": False,
            }
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

                try:
                    post_data = request.post_data or ""
                    if post_data.startswith("{"):
                        try:
                            payload = json.loads(post_data)
                            vid = payload.get("id") or payload.get("variant_id")
                            qty = payload.get("quantity")
                            props = payload.get("properties")
                            plan = payload.get("selling_plan")
                            if _is_valid_int(vid):
                                add_request_data["req_variant_id"] = vid
                            if _is_valid_int(qty) and qty > 0:
                                add_request_data["req_quantity"] = qty
                            if isinstance(props, dict):
                                add_request_data["req_properties"] = props
                            if plan is not None:
                                add_request_data["req_selling_plan"] = plan
                        except Exception:
                            add_request_data["unparseable"] = True
                    else:
                        parsed = parse_qs(post_data)
                        v_val = parsed.get("id", [None])[0] or parsed.get("variant_id", [None])[0]
                        q_val = parsed.get("quantity", [None])[0]
                        plan_val = parsed.get("selling_plan", [None])[0]
                        if v_val and v_val.isdigit():
                            add_request_data["req_variant_id"] = int(v_val)
                        if q_val and q_val.isdigit():
                            add_request_data["req_quantity"] = int(q_val)
                        if plan_val:
                            add_request_data["req_selling_plan"] = plan_val

                        props = {}
                        for pk, pv in parsed.items():
                            if pk.startswith("properties[") and pk.endswith("]"):
                                prop_name = pk[11:-1]
                                if pv:
                                    props[prop_name] = pv[0]
                        if props:
                            add_request_data["req_properties"] = props
                except Exception:
                    add_request_data["unparseable"] = True

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
                                if "items" in res_json and isinstance(res_json["items"], list):
                                    add_request_data["res_items"] = res_json["items"]
                    except Exception as e:
                        logger.debug("ATC response parse failed: %s", e)

            page.on("request", on_request)
            page.on("response", on_response)

            try:
                atc_button = await self._find_atc_button(page, main_form)
                if not atc_button:
                    return SkillResult(
                        skill_name=self.name(),
                        status="WARN",
                        summary=_redact_value("Could not locate Add to Cart button in DOM"),
                        details=_redact_value({"reason_code": "BUTTON_MISSING", "steps": steps, "checks": {}}),
                    ), {"failed_check": None, "reason_code": "BUTTON_MISSING", "timestamp": pass_timestamp, "product_handle": handle}

                try:
                    await atc_button.click(timeout=10000)
                    steps.append("Clicked Add to Cart button")
                except Exception as e:
                    return SkillResult(
                        skill_name=self.name(),
                        status="WARN",
                        summary=_redact_value(f"Clicking Add to Cart button failed: {e}"),
                        details=_redact_value({"reason_code": "ATC_CLICK_FAILED", "steps": steps, "checks": {}}),
                    ), {"failed_check": None, "reason_code": "ATC_CLICK_FAILED", "timestamp": pass_timestamp, "product_handle": handle}

                # Await network completion of intercepted ATC POST request first (bounded timeout 4.0s)
                req_start = datetime.now()
                while (datetime.now() - req_start).total_seconds() < 4.0:
                    if add_request_data["status"] is not None:
                        break
                    await asyncio.sleep(0.1)

                # Repeated consecutive stable snapshot polling within a bounded deadline
                start_poll = datetime.now()
                meta_after = None
                items_after = []
                target_vid = target_variant.get("id")

                stable_count = 0
                last_snapshot_count = None

                while (datetime.now() - start_poll).total_seconds() < 5.0:
                    m_curr, i_curr = await self._fetch_cart_snapshot(page, origin, locale)
                    if m_curr["status"] in ("empty", "nonempty"):
                        meta_after = m_curr
                        items_after = i_curr
                        curr_count = m_curr["item_count"]
                        if last_snapshot_count is not None and curr_count == last_snapshot_count:
                            stable_count += 1
                            if stable_count >= 2:
                                break
                        else:
                            stable_count = 0
                        last_snapshot_count = curr_count
                    await asyncio.sleep(0.3)

                if not meta_after:
                    meta_after, items_after = await self._fetch_cart_snapshot(page, origin, locale)

            finally:
                # Guaranteed listener removal
                try:
                    page.remove_listener("request", on_request)
                    page.remove_listener("response", on_response)
                except Exception:
                    pass

            target_vid = target_variant.get("id")
            target_line_identity = _build_line_key(
                target_vid, add_request_data.get("req_properties"), add_request_data.get("req_selling_plan"), salt
            )

            # Find matching line item by full identity (variant_id, hashed_props, selling_plan)
            matched_line_before = self._find_matching_line_item(items_before, target_vid, add_request_data.get("req_properties"), add_request_data.get("req_selling_plan"), salt)
            matched_line_after = self._find_matching_line_item(items_after, target_vid, add_request_data.get("req_properties"), add_request_data.get("req_selling_plan"), salt)

            q_target_before = matched_line_before.get("quantity", 0) if matched_line_before else 0
            q_target_after = matched_line_after.get("quantity", 0) if matched_line_after else 0
            actual_delta = q_target_after - q_target_before

            # Identify wrong variant increases
            wrong_variant_deltas = {}
            for it in items_after:
                vid = it["variant_id"]
                if vid != target_vid and vid in {v.get("id") for v in variants_list if v.get("id")}:
                    q_b = sum(i["quantity"] for i in items_before if i["variant_id"] == vid)
                    d = it["quantity"] - q_b
                    if d > 0:
                        wrong_variant_deltas[vid] = d

            side_additions = []
            for it in items_after:
                vid = it["variant_id"]
                if vid != target_vid and vid not in wrong_variant_deltas:
                    q_b = sum(i["quantity"] for i in items_before if i["variant_id"] == vid)
                    if it["quantity"] - q_b > 0:
                        side_additions.append({
                            "variant_id": vid,
                            "delta_quantity": it["quantity"] - q_b,
                            "line_identity": _build_line_key(vid, it.get("properties"), it.get("selling_plan_id"), salt),
                        })

            # --- CHECK 1: Variant Identity Check ---
            if add_request_data.get("unparseable"):
                checks["variant_identity"] = {
                    "status": "WARN",
                    "reason_code": "UNPARSEABLE_POST_PAYLOAD",
                    "summary": "ATC POST request payload was unparseable",
                    "expected_variant_id": target_vid,
                }
            elif wrong_variant_deltas or (add_request_data["captured"] and add_request_data["req_variant_id"] and add_request_data["req_variant_id"] != target_vid):
                added_wrong_vid = list(wrong_variant_deltas.keys())[0] if wrong_variant_deltas else add_request_data["req_variant_id"]
                r_code = "WRONG_VARIANT_ADDED" if wrong_variant_deltas else "WRONG_VARIANT_REQUESTED"
                checks["variant_identity"] = {
                    "status": "FAIL",
                    "reason_code": r_code,
                    "summary": f"Added wrong variant ID {added_wrong_vid} instead of target {target_vid}",
                    "expected_variant_id": target_vid,
                    "observed_variant_id": added_wrong_vid,
                }
            elif not add_request_data["captured"]:
                checks["variant_identity"] = {
                    "status": "WARN",
                    "reason_code": "ATC_REQUEST_NOT_CAPTURED",
                    "summary": "Add to Cart request was not captured during UI click",
                    "expected_variant_id": target_vid,
                }
            elif actual_delta <= 0:
                if add_request_data["status"] == 200:
                    checks["variant_identity"] = {
                        "status": "WARN",
                        "reason_code": "HTTP_200_NO_CART_CHANGE",
                        "summary": f"Add to Cart returned HTTP 200 but target variant line quantity did not change (delta={actual_delta})",
                        "expected_variant_id": target_vid,
                        "delta_quantity": actual_delta,
                    }
                else:
                    checks["variant_identity"] = {
                        "status": "WARN",
                        "reason_code": "ADD_REQUEST_FAILED",
                        "summary": f"Add to Cart request failed with HTTP {add_request_data['status']}",
                        "expected_variant_id": target_vid,
                    }
            else:
                checks["variant_identity"] = {
                    "status": "PASS",
                    "reason_code": "NONE",
                    "summary": f"Target variant {target_vid} correctly requested and added to cart",
                    "expected_variant_id": target_vid,
                    "observed_variant_id": target_vid,
                    "target_line_identity": target_line_identity,
                    "delta_quantity": actual_delta,
                    "side_additions": side_additions,
                }

            # --- CHECK 2: Quantity Consistency Check ---
            post_req_qty = add_request_data.get("req_quantity")

            if checks["variant_identity"]["status"] == "FAIL":
                checks["quantity_consistency"] = {
                    "status": "WARN",
                    "reason_code": "VARIANT_IDENTITY_FAILED",
                    "summary": "Quantity consistency unproven due to variant identity failure",
                }
            elif checks["variant_identity"]["status"] == "WARN":
                checks["quantity_consistency"] = {
                    "status": "WARN",
                    "reason_code": "VARIANT_IDENTITY_UNPROVEN",
                    "summary": "Quantity consistency unproven due to unconfirmed variant addition",
                }
            elif post_req_qty is not None and post_req_qty != requested_ui_qty:
                checks["quantity_consistency"] = {
                    "status": "FAIL",
                    "reason_code": "REQUEST_QUANTITY_MISMATCH",
                    "summary": f"UI requested quantity ({requested_ui_qty}) mismatch with POST request quantity ({post_req_qty})",
                    "expected_qty": requested_ui_qty,
                    "observed_qty": post_req_qty,
                }
            elif actual_delta != requested_ui_qty:
                checks["quantity_consistency"] = {
                    "status": "FAIL",
                    "reason_code": "QUANTITY_DELTA_MISMATCH",
                    "summary": f"Requested quantity delta ({requested_ui_qty}) does not match actual cart quantity delta ({actual_delta})",
                    "expected_delta": requested_ui_qty,
                    "observed_delta": actual_delta,
                }
            else:
                checks["quantity_consistency"] = {
                    "status": "PASS",
                    "reason_code": "NONE",
                    "summary": f"Requested quantity ({requested_ui_qty}) matches POST request and actual cart quantity delta ({actual_delta})",
                    "expected_qty": requested_ui_qty,
                    "observed_delta": actual_delta,
                }

            # --- CHECK 3: Price Consistency Check ---
            api_currency = meta_after.get("currency")

            if not pdp_dom_proven or pdp_dom_price_cents is None:
                checks["price_consistency"] = {
                    "status": "WARN",
                    "reason_code": "DOM_PRICE_UNPROVEN",
                    "summary": "PDP DOM displayed price could not be proven directly in DOM",
                }
            elif not pdp_currency or not api_currency:
                checks["price_consistency"] = {
                    "status": "WARN",
                    "reason_code": "CURRENCY_UNPROVEN",
                    "summary": f"Currency not proven explicitly on PDP display ({pdp_currency}) or Ajax cart API ({api_currency})",
                    "pdp_currency": pdp_currency,
                    "api_currency": api_currency,
                }
            elif api_currency.upper() != pdp_currency.upper():
                checks["price_consistency"] = {
                    "status": "WARN",
                    "reason_code": "CURRENCY_MISMATCH",
                    "summary": f"PDP display currency ({pdp_currency}) differs from cart API currency ({api_currency})",
                    "pdp_currency": pdp_currency,
                    "api_currency": api_currency,
                }
            elif not matched_line_after:
                checks["price_consistency"] = {
                    "status": "WARN",
                    "reason_code": "LINE_ITEM_NOT_FOUND",
                    "summary": "Matched line item not found in cart for price comparison",
                }
            else:
                # Respect final_price = 0 explicitly
                cart_unit_price = matched_line_after.get("final_price")
                if cart_unit_price is None:
                    cart_unit_price = matched_line_after.get("price")

                cart_line_qty = matched_line_after.get("quantity", 1)
                cart_discounts = matched_line_after.get("line_level_discount_allocations", []) or matched_line_after.get("discounts", [])

                if cart_unit_price is None:
                    checks["price_consistency"] = {
                        "status": "WARN",
                        "reason_code": "CART_PRICE_MISSING",
                        "summary": "Cart item price missing in Ajax snapshot",
                    }
                elif cart_unit_price == pdp_dom_price_cents:
                    checks["price_consistency"] = {
                        "status": "PASS",
                        "reason_code": "NONE",
                        "summary": f"PDP DOM price ({pdp_dom_price_cents/100:.2f} {pdp_currency}) matches cart unit price ({cart_unit_price/100:.2f} {api_currency})",
                        "expected_cents": pdp_dom_price_cents,
                        "observed_cents": cart_unit_price,
                        "currency": pdp_currency,
                    }
                else:
                    # Calculate unit discount (total_discount / line_quantity) before comparing
                    price_diff = pdp_dom_price_cents - cart_unit_price
                    total_discount_amount = 0
                    if isinstance(cart_discounts, list):
                        for d in cart_discounts:
                            if isinstance(d, dict) and "amount" in d and _is_valid_int(d["amount"]):
                                total_discount_amount += d["amount"]

                    unit_discount = total_discount_amount // cart_line_qty if cart_line_qty > 0 else 0

                    if total_discount_amount > 0 and price_diff == unit_discount:
                        checks["price_consistency"] = {
                            "status": "PASS",
                            "reason_code": "EXPLAINED_DISCOUNT",
                            "summary": f"Price difference (PDP {pdp_dom_price_cents/100:.2f} vs Cart {cart_unit_price/100:.2f}) mathematically explained by line unit discount ({unit_discount/100:.2f})",
                            "expected_cents": pdp_dom_price_cents,
                            "observed_cents": cart_unit_price,
                            "unit_discount": unit_discount,
                        }
                    else:
                        checks["price_consistency"] = {
                            "status": "FAIL",
                            "reason_code": "UNEXPLAINED_PRICE_DISCREPANCY",
                            "summary": f"PDP displayed DOM price ({pdp_dom_price_cents/100:.2f}) does not match cart unit price ({cart_unit_price/100:.2f}) without valid discount justification",
                            "expected_cents": pdp_dom_price_cents,
                            "observed_cents": cart_unit_price,
                        }

            # --- CHECK 4: Cart UI & Cart Drawer Consistency Check ---
            is_identity_failed = checks["variant_identity"]["status"] in ("FAIL", "WARN") and actual_delta <= 0
            if is_identity_failed:
                checks["cart_ui_consistency"] = {
                    "status": "WARN",
                    "reason_code": "VARIANT_IDENTITY_UNPROVEN",
                    "summary": "Cart UI check skipped because target item addition was unconfirmed or failed",
                    "verified_scope": ["ajax_api"],
                }
            else:
                checks["cart_ui_consistency"] = await self._check_cart_ui_consistency(
                    page=page,
                    target_variant=target_variant,
                    target_properties=add_request_data.get("req_properties"),
                    target_selling_plan=add_request_data.get("req_selling_plan"),
                    expected_final_quantity=q_target_after,
                    expected_price_cents=pdp_dom_price_cents,
                    variants_list=variants_list,
                )

            # --- CHECK 5: Optional Quantity Update Check ---
            if enable_quantity_update:
                checks["quantity_update"] = await self._check_quantity_update_ui(
                    page=page,
                    origin=origin,
                    locale=locale,
                    target_variant=target_variant,
                    target_properties=add_request_data.get("req_properties"),
                    target_selling_plan=add_request_data.get("req_selling_plan"),
                    items_after=items_after,
                    variants_list=variants_list,
                    salt=salt,
                )

            screenshot_path = await self._take_screenshot(page, "cart_ui")

            statuses = [c["status"] for c in checks.values()]
            failed_check_name = None
            failed_reason_code = None
            failed_expected = None
            failed_observed = None

            if "FAIL" in statuses:
                overall_status = "FAIL"
                failed_checks = [k for k, v in checks.items() if v["status"] == "FAIL"]
                failed_check_name = failed_checks[0]
                failed_reason_code = checks[failed_check_name].get("reason_code", "FAIL")
                failed_expected = _first_not_none(
                    checks[failed_check_name].get("expected_variant_id"),
                    checks[failed_check_name].get("expected_variant_title"),
                    checks[failed_check_name].get("expected_selling_plan"),
                    checks[failed_check_name].get("expected_qty"),
                    checks[failed_check_name].get("expected_delta"),
                    checks[failed_check_name].get("expected_cents"),
                    checks[failed_check_name].get("expected_quantity"),
                )
                failed_observed = _first_not_none(
                    checks[failed_check_name].get("observed_variant_id"),
                    checks[failed_check_name].get("observed_variant_title"),
                    checks[failed_check_name].get("observed_selling_plan"),
                    checks[failed_check_name].get("observed_qty"),
                    checks[failed_check_name].get("observed_delta"),
                    checks[failed_check_name].get("observed_cents"),
                    checks[failed_check_name].get("observed_quantity"),
                )
                summary = f"Cart consistency audit failed: {', '.join(failed_checks)}"
            elif "WARN" in statuses:
                overall_status = "WARN"
                warned_checks = [k for k, v in checks.items() if v["status"] == "WARN"]
                summary = f"Cart consistency audit completed with warnings: {', '.join(warned_checks)}"
            else:
                overall_status = "PASS"
                summary = "All cart consistency checks passed successfully"

            run_meta = {
                "product_handle": handle,
                "target_variant_id": target_vid,
                "target_line_identity": target_line_identity,
                "failed_check": failed_check_name,
                "reason_code": failed_reason_code or ("NONE" if overall_status == "PASS" else "WARN"),
                "expected": failed_expected,
                "observed": failed_observed,
                "observed_delta": actual_delta,
                "initial_item_count": meta_before.get("item_count"),
                "timestamp": pass_timestamp,
            }

            return SkillResult(
                skill_name=self.name(),
                status=overall_status,
                summary=_redact_value(summary),
                details=_redact_value({
                    "reason_code": run_meta["reason_code"],
                    "steps": steps,
                    "target_variant_id": target_vid,
                    "checks": checks,
                    "redacted_screenshot": screenshot_path,
                    "pass_timestamp": pass_timestamp,
                }),
            ), run_meta

        except Exception as e:
            logger.exception("Error in shopify_cart_consistency_auditor: %s", e)
            return SkillResult(
                skill_name=self.name(),
                status="WARN",
                summary=_redact_value(f"Shopify cart consistency auditor exception: {str(e)}"),
                error=str(e),
                details=_redact_value({
                    "reason_code": "INTERNAL_AUDITOR_ERROR",
                    "steps": steps,
                    "checks": checks,
                }),
            ), {
                "failed_check": None,
                "reason_code": "INTERNAL_AUDITOR_ERROR",
                "timestamp": pass_timestamp,
                "product_handle": handle if 'handle' in locals() else None,
                "target_variant_id": target_vid if 'target_vid' in locals() else None,
                "target_line_identity": None,
                "expected": None,
                "observed": None,
                "observed_delta": None,
                "initial_item_count": meta_before.get("item_count") if 'meta_before' in locals() and isinstance(meta_before, dict) else None,
            }

    def _find_matching_line_item(
        self, items: list[dict[str, Any]], target_vid: int, req_props: Any, req_selling_plan: Any, salt: str
    ) -> Optional[dict[str, Any]]:
        """Match exact line item using (variant_id, hashed_props, selling_plan). Do NOT treat properties={} as wildcard."""
        target_key = _build_line_key(target_vid, req_props, req_selling_plan, salt)
        for item in items:
            if item.get("variant_id") == target_vid:
                key = _build_line_key(item.get("variant_id"), item.get("properties"), item.get("selling_plan_id"), salt)
                if key == target_key:
                    return item
        return None

    async def _check_unfulfilled_customization_fields(self, page, main_form) -> Optional[str]:
        """Check for mandatory personalization/text inputs requiring user input or violating HTML validity."""
        form_loc = main_form if await main_form.count() > 0 else page
        try:
            inputs = form_loc.locator(
                'input:not([type="checkbox"]):not([type="radio"]):not([type="hidden"]):not([type="submit"]):not([name="quantity"]), textarea, [name*="properties"]'
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

    async def _select_variant_options_in_dom(
        self, page, main_form, target_variant: dict[str, Any], options_list: list[Any]
    ) -> tuple[bool, str, dict[str, str]]:
        """Select option values required for target_variant via UI interaction and prove DOM selection strictly."""
        form_loc = main_form if await main_form.count() > 0 else page
        variant_options = target_variant.get("options", [])
        proven_dom_options = {}

        option_names = []
        for opt in options_list:
            if isinstance(opt, dict):
                option_names.append(opt.get("name", ""))
            elif isinstance(opt, str):
                option_names.append(opt)

        has_option_controls = False
        try:
            opt_cnt = await form_loc.locator('select, input[type="radio"], [data-value], .swatch, button.variant-option').count()
            has_option_controls = opt_cnt > 0
        except Exception:
            pass

        if not has_option_controls:
            for idx, val in enumerate(variant_options):
                opt_name = option_names[idx] if idx < len(option_names) else f"Option{idx+1}"
                proven_dom_options[opt_name] = str(val)
            return True, "", proven_dom_options

        for idx, val in enumerate(variant_options):
            opt_name = option_names[idx] if idx < len(option_names) else f"Option{idx+1}"
            selected = False

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

            btn_loc = form_loc.locator(
                f'input[type="radio"][value="{val}" i], button:has-text("{val}"), [data-value="{val}"], label:has-text("{val}")'
            ).first
            if await btn_loc.count() > 0 and await btn_loc.is_visible():
                try:
                    await btn_loc.click(timeout=2000)
                    is_active = await btn_loc.evaluate("""
                        el => {
                            if (el.checked) return true;
                            if (el.getAttribute('aria-checked') === 'true') return true;
                            if (el.getAttribute('aria-selected') === 'true') return true;
                            if (el.getAttribute('aria-pressed') === 'true') return true;
                            if (el.classList.contains('selected') || el.classList.contains('active')) return true;
                            return false;
                        }
                    """)
                    if is_active:
                        selected = True
                        proven_dom_options[opt_name] = str(val)
                except Exception:
                    pass

            if not selected:
                btn_exists = await form_loc.locator(
                    f'input[type="radio"][value="{val}" i], button:has-text("{val}"), [data-value="{val}"], label:has-text("{val}")'
                ).count() > 0
                if btn_exists:
                    return False, f"Option control '{opt_name}={val}' exists but produced no active selection state in DOM", proven_dom_options

                proven_dom_options[opt_name] = str(val)

        all_proven = len(option_names) == 0 or len(proven_dom_options) == len(option_names)
        if not all_proven and len(option_names) > 0:
            return False, f"Option selection in DOM could not be proven for all options ({proven_dom_options})", proven_dom_options

        return True, "", proven_dom_options

    async def _get_requested_quantity_from_dom(self, page, main_form) -> int:
        """Read selected quantity from DOM quantity input."""
        form_loc = main_form if await main_form.count() > 0 else page
        try:
            qty_input = form_loc.locator('input[name="quantity"], input[type="number"].quantity, .quantity-input input').first
            if await qty_input.count() > 0 and await qty_input.is_visible():
                val_str = await qty_input.evaluate("el => el.value")
                if val_str and val_str.isdigit():
                    val = int(val_str)
                    if val > 0:
                        return val
        except Exception:
            pass
        return 1

    async def _get_pdp_dom_price_and_currency(self, page, main_form, target_variant: dict[str, Any]) -> tuple[Optional[int], Optional[str], bool]:
        """Read price displayed in DOM (do NOT fallback to JSON and claim DOM price verified)."""
        form_loc = main_form if await main_form.count() > 0 else page
        selectors = [
            '.price-item--sale',
            '.price-item--regular',
            '[data-product-price]',
            '.product-single__price',
            '.price',
        ]

        for sel in selectors:
            loc = form_loc.locator(sel)
            cnt = await loc.count()
            for i in range(cnt):
                item_loc = loc.nth(i)
                if not await item_loc.is_visible():
                    continue

                raw_text = await item_loc.evaluate("""
                    el => {
                        if (el.tagName.toLowerCase() === 's' || el.tagName.toLowerCase() === 'del' || el.classList.contains('compare-at-price')) {
                            return '';
                        }
                        const clone = el.cloneNode(true);
                        clone.querySelectorAll('s, del, .compare-at-price, [data-compare-price], .price__compare-at').forEach(e => e.remove());
                        return clone.innerText || clone.textContent || '';
                    }
                """)
                raw_text = raw_text.strip()
                if raw_text:
                    parsed_cents = parse_price_cents(raw_text)
                    if parsed_cents is not None:
                        currency = _extract_currency_symbol(raw_text)
                        return parsed_cents, currency, True

        return None, None, False

    async def _find_atc_button(self, page, main_form):
        """Locate visible enabled Add to Cart button."""
        form_loc = main_form if await main_form.count() > 0 else page
        selectors = [
            'button[name="add"]:visible:not([disabled])',
            'button.add-to-cart:visible:not([disabled])',
            'button[type="submit"]:has-text("add"):visible:not([disabled])',
            'button[type="submit"]:has-text("bag"):visible:not([disabled])',
            'button:has-text("ADD TO CART"):visible:not([disabled])',
            '[data-add-to-cart]:visible:not([disabled])',
        ]
        for sel in selectors:
            btn = form_loc.locator(sel).first
            if await btn.count() > 0:
                return btn
        return None

    async def _find_cart_ui_line_container(
        self,
        ui_container: Any,
        target_variant: dict[str, Any],
        target_properties: Any,
        target_selling_plan: Any,
        variants_list: list[dict[str, Any]],
    ) -> tuple[Optional[Any], bool, Optional[str], Optional[str], Optional[str]]:
        """Strictly locate and bind target line item container in Cart UI.
        Requires matching product context AND target variant title/options, properties, and selling plan.
        Returns (container_loc, is_unique, error_code, wrong_variant, wrong_plan).
        """
        target_title = target_variant.get("name") or target_variant.get("title") or ""
        v_title = target_variant.get("title", "")
        p_handle = (target_variant.get("product_handle") or target_variant.get("handle") or "").lower()
        p_title = (target_variant.get("product_title") or target_variant.get("name") or "").lower()

        other_variant_titles = [
            v.get("title", "").lower() for v in variants_list
            if v.get("id") != target_variant.get("id") and v.get("title") and v.get("title", "").lower() not in ("default title", "default")
        ]

        generic_words = {"small", "large", "medium", "xl", "s", "l", "m", "default title", "default", "red", "blue", "black", "white"}

        line_items = ui_container.locator(
            '.cart-item, .cart-drawer__item, [data-cart-item], .mini-cart-item, tr.cart__row, cart-drawer-items .cart-item'
        )
        count = await line_items.count()

        matched_containers = []
        wrong_variant_found = None
        wrong_plan_found = None

        if count > 0:
            for i in range(count):
                item_loc = line_items.nth(i)
                txt = (await item_loc.inner_text()).lower()

                is_product_match = False
                if p_handle and p_handle in txt:
                    is_product_match = True
                elif p_title and any(word in txt for word in p_title.split() if len(word) > 2 and word not in generic_words):
                    is_product_match = True

                if not is_product_match:
                    continue

                # Check if line displays a wrong variant of the same product
                for other_v_title in other_variant_titles:
                    if other_v_title and other_v_title in txt and (v_title.lower() not in txt):
                        wrong_variant_found = other_v_title
                        break

                # Check target variant match
                is_target_match = False
                if v_title and v_title.lower() not in ("default title", "default") and v_title.lower() in txt:
                    is_target_match = True
                elif target_title and target_title.lower() not in generic_words and target_title.lower() in txt:
                    is_target_match = True
                elif len(variants_list) <= 1 or v_title.lower() in ("default title", "default"):
                    is_target_match = True

                if not is_target_match:
                    continue

                # Match properties strictly
                if target_properties and isinstance(target_properties, dict):
                    prop_mismatch = False
                    for prop_k, prop_v in target_properties.items():
                        if str(prop_v).lower() not in txt:
                            prop_mismatch = True
                            break
                    if prop_mismatch:
                        continue

                # Match selling plan strictly using exact ID extraction
                if target_selling_plan is not None and str(target_selling_plan).strip() != "":
                    target_plan_str = str(target_selling_plan).strip()
                    extracted_plan, is_proven = await _extract_selling_plan_id(item_loc, txt)
                    if is_proven and extracted_plan:
                        if extracted_plan != target_plan_str:
                            wrong_plan_found = extracted_plan
                            continue
                    else:
                        # Selling plan requested but label or attribute absent in UI
                        continue

                matched_containers.append(item_loc)

        if wrong_variant_found and not matched_containers:
            return None, False, "WRONG_CART_UI_VARIANT", wrong_variant_found, None

        if wrong_plan_found and not matched_containers:
            return None, False, "WRONG_CART_UI_SELLING_PLAN", None, wrong_plan_found

        if len(matched_containers) == 1:
            return matched_containers[0], True, None, None, None

        if len(matched_containers) > 1:
            return None, False, "AMBIGUOUS_CART_UI_LINE_BINDING", None, None

        return None, False, "CART_UI_ITEM_UNPROVEN", None, None

    async def _check_cart_ui_consistency(
        self,
        page,
        target_variant: dict[str, Any],
        target_properties: Any,
        target_selling_plan: Any,
        expected_final_quantity: int,
        expected_price_cents: Optional[int],
        variants_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Verify Cart Drawer or Cart Page UI consistency against matched target line item final quantity."""
        drawer = page.locator(
            '.cart-drawer:visible, #CartDrawer:visible, [data-cart-drawer]:visible, .mini-cart:visible, .drawer:visible, cart-drawer:visible'
        ).first

        if await drawer.count() == 0:
            cart_trigger = page.locator('a[href*="/cart"], button[aria-label*="cart" i], .cart-link, #cart-icon-bubble').first
            if await cart_trigger.count() > 0 and await cart_trigger.is_visible():
                try:
                    await cart_trigger.click(timeout=2000)
                    await asyncio.sleep(1)
                    drawer = page.locator(
                        '.cart-drawer:visible, #CartDrawer:visible, [data-cart-drawer]:visible, .mini-cart:visible, .drawer:visible, cart-drawer:visible'
                    ).first
                except Exception:
                    pass

        start_ui_poll = datetime.now()
        ui_container = drawer if await drawer.count() > 0 else page.locator('.cart-page, main, body').first

        last_err_code = None
        last_w_variant = None
        last_w_plan = None
        last_observed_qty = None
        last_observed_price_cents = None

        # Continuously poll UI until quantity and price match expected values or deadline (3.0s) expires
        while (datetime.now() - start_ui_poll).total_seconds() < 3.0:
            target_container, is_unique, err_code, w_variant, w_plan = await self._find_cart_ui_line_container(
                ui_container, target_variant, target_properties, target_selling_plan, variants_list
            )
            last_err_code = err_code
            last_w_variant = w_variant
            last_w_plan = w_plan

            if err_code in ("WRONG_CART_UI_VARIANT", "WRONG_CART_UI_SELLING_PLAN"):
                break

            if target_container is not None and is_unique:
                qty_loc = target_container.locator('input[name*="updates"], .cart-item__quantity input, input.quantity').first
                obs_q = None
                if await qty_loc.count() > 0:
                    q_str = await qty_loc.evaluate("el => el.value")
                    if q_str and q_str.isdigit():
                        obs_q = int(q_str)

                if obs_q is None:
                    qty_text_loc = target_container.locator('.cart-item__quantity, .quantity').first
                    if await qty_text_loc.count() > 0:
                        t_q = await qty_text_loc.inner_text()
                        m = re.search(r"\b\d+\b", t_q)
                        if m:
                            obs_q = int(m.group(0))

                obs_p = None
                price_loc = target_container.locator('.cart-item__price, .price, [data-cart-item-price]').first
                if await price_loc.count() > 0:
                    p_txt = await price_loc.inner_text()
                    obs_p = parse_price_cents(p_txt)

                last_observed_qty = obs_q
                last_observed_price_cents = obs_p

                if obs_q == expected_final_quantity:
                    if expected_price_cents is None or obs_p == expected_price_cents:
                        return {
                            "status": "PASS",
                            "reason_code": "NONE",
                            "summary": "Cart drawer/page UI correctly displays target item, final quantity, and price after stabilization",
                            "expected_quantity": expected_final_quantity,
                            "observed_quantity": obs_q,
                            "verified_scope": ["ajax_api", "cart_drawer_ui"],
                        }

            await asyncio.sleep(0.2)

        if last_err_code == "WRONG_CART_UI_VARIANT":
            return {
                "status": "FAIL",
                "reason_code": "WRONG_CART_UI_VARIANT",
                "summary": f"Cart UI displays wrong variant ('{last_w_variant}') for target product instead of expected '{target_variant.get('title')}'",
                "expected_variant_title": target_variant.get("title"),
                "observed_variant_title": last_w_variant,
                "verified_scope": ["ajax_api", "cart_drawer_ui"],
            }

        if last_err_code == "WRONG_CART_UI_SELLING_PLAN":
            return {
                "status": "FAIL",
                "reason_code": "WRONG_CART_UI_SELLING_PLAN",
                "summary": f"Cart UI displays wrong selling plan ('{last_w_plan}') for target item instead of expected '{target_selling_plan}'",
                "expected_selling_plan": str(target_selling_plan),
                "observed_selling_plan": last_w_plan,
                "verified_scope": ["ajax_api", "cart_drawer_ui"],
            }

        if target_selling_plan is not None and str(target_selling_plan).strip() != "" and last_err_code == "CART_UI_ITEM_UNPROVEN":
            return {
                "status": "WARN",
                "reason_code": "CART_UI_SELLING_PLAN_UNPROVEN",
                "summary": f"Could not conclusively prove selling plan '{target_selling_plan}' in Cart UI",
                "verified_scope": ["ajax_api"],
                "unverified_scope": ["cart_drawer_ui"],
            }

        if last_observed_qty is None:
            return {
                "status": "WARN",
                "reason_code": "CART_UI_QUANTITY_UNREADABLE",
                "summary": "Cart UI line item quantity element missing or unreadable",
                "verified_scope": ["ajax_api"],
            }

        if last_observed_price_cents is None:
            return {
                "status": "WARN",
                "reason_code": "CART_UI_PRICE_UNREADABLE",
                "summary": "Cart UI line item price element missing or unreadable",
                "verified_scope": ["ajax_api"],
            }

        if last_observed_qty != expected_final_quantity:
            return {
                "status": "FAIL",
                "reason_code": "STALE_CART_UI_QUANTITY",
                "summary": f"Cart UI display remained stale after stabilization (expected final quantity {expected_final_quantity}, observed in UI {last_observed_qty})",
                "expected_quantity": expected_final_quantity,
                "observed_quantity": last_observed_qty,
                "verified_scope": ["ajax_api", "cart_drawer_ui"],
            }

        if expected_price_cents and last_observed_price_cents and last_observed_price_cents != expected_price_cents:
            return {
                "status": "WARN",
                "reason_code": "CART_UI_PRICE_MISMATCH",
                "summary": f"Cart UI price ({last_observed_price_cents/100:.2f}) differs from expected PDP price ({expected_price_cents/100:.2f})",
                "expected_price_cents": expected_price_cents,
                "observed_price_cents": last_observed_price_cents,
                "verified_scope": ["ajax_api", "cart_drawer_ui"],
            }

        return {
            "status": "PASS",
            "reason_code": "NONE",
            "summary": "Cart drawer/page UI correctly displays target item, final quantity, and price after stabilization",
            "expected_quantity": expected_final_quantity,
            "observed_quantity": last_observed_qty,
            "verified_scope": ["ajax_api", "cart_drawer_ui"],
        }

    async def _check_quantity_update_ui(
        self,
        page,
        origin: str,
        locale: str,
        target_variant: dict[str, Any],
        target_properties: Any,
        target_selling_plan: Any,
        items_after: list[dict[str, Any]],
        variants_list: list[dict[str, Any]],
        salt: str = "nano_sre_salt",
    ) -> dict[str, Any]:
        """Optional opt-in quantity update check strictly bound to target line's UI container."""
        try:
            target_vid = target_variant.get("id")
            ui_container = page.locator('.cart-drawer:visible, #CartDrawer:visible, .cart-page, main, body').first

            target_line_container, is_unique, err_code, _, _ = await self._find_cart_ui_line_container(
                ui_container, target_variant, target_properties, target_selling_plan, variants_list
            )

            if target_line_container is None or not is_unique:
                # Do NOT click any UI button if target line container is unproven or ambiguous!
                return {
                    "status": "WARN",
                    "reason_code": "QUANTITY_UPDATE_LINE_UNPROVEN",
                    "summary": f"Target line item container could not be bound in Cart UI for quantity update ({err_code or 'unproven'})",
                }

            qty_plus_btn = target_line_container.locator(
                'button[name="plus"], button.quantity__button[name="plus"], button:has-text("+"), .cart__qty-btn--plus'
            ).first

            if await qty_plus_btn.count() == 0 or not await qty_plus_btn.is_visible():
                return {
                    "status": "WARN",
                    "reason_code": "QUANTITY_UPDATE_UI_MISSING",
                    "summary": "Quantity increment control not found in target line item UI",
                }

            # Read exact target line quantity before click (without wildcard summing across other property lines)
            matched_line_before = self._find_matching_line_item(items_after, target_vid, target_properties, target_selling_plan, salt)
            q_before_update = matched_line_before.get("quantity", 0) if matched_line_before else 0

            await qty_plus_btn.click(timeout=2000)
            await asyncio.sleep(1.5)

            meta_updated, items_updated = await self._fetch_cart_snapshot(page, origin, locale)
            matched_line_after = self._find_matching_line_item(items_updated, target_vid, target_properties, target_selling_plan, salt)
            q_after_update = matched_line_after.get("quantity", 0) if matched_line_after else 0

            # Check that all other lines (by full identity) were NOT mutated
            target_key = _build_line_key(target_vid, target_properties, target_selling_plan, salt)
            other_lines_before = [
                it for it in items_after
                if _build_line_key(it.get("variant_id"), it.get("properties"), it.get("selling_plan_id"), salt) != target_key
            ]
            other_lines_after = [
                it for it in items_updated
                if _build_line_key(it.get("variant_id"), it.get("properties"), it.get("selling_plan_id"), salt) != target_key
            ]

            if len(other_lines_before) != len(other_lines_after):
                return {
                    "status": "FAIL",
                    "reason_code": "UNINTENDED_LINE_MUTATION",
                    "summary": "Quantity update on target line item unintentionally mutated other cart lines",
                }

            for l_b in other_lines_before:
                key_b = _build_line_key(l_b.get("variant_id"), l_b.get("properties"), l_b.get("selling_plan_id"), salt)
                matching_after = [
                    l_a for l_a in other_lines_after
                    if _build_line_key(l_a.get("variant_id"), l_a.get("properties"), l_a.get("selling_plan_id"), salt) == key_b
                ]
                if not matching_after or matching_after[0]["quantity"] != l_b["quantity"]:
                    return {
                        "status": "FAIL",
                        "reason_code": "UNINTENDED_LINE_MUTATION",
                        "summary": f"Quantity update mutated line item key={key_b}",
                    }

            if q_after_update > q_before_update:
                return {
                    "status": "PASS",
                    "reason_code": "NONE",
                    "summary": f"Target line quantity updated via UI from {q_before_update} to {q_after_update} and synced with API",
                    "quantity_before": q_before_update,
                    "quantity_after": q_after_update,
                }

            return {
                "status": "FAIL",
                "reason_code": "QUANTITY_UPDATE_FAILED",
                "summary": f"Clicking quantity update control failed to update target line quantity (before: {q_before_update}, after: {q_after_update})",
            }
        except Exception as e:
            return {
                "status": "WARN",
                "reason_code": "QUANTITY_UPDATE_ERROR",
                "summary": f"Quantity update test failed with exception: {e}",
            }

    async def _fetch_cart_snapshot(self, page, origin: str, locale: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Fetch safe cart.js snapshot with strict schema validation."""
        meta = {
            "status": "failed",
            "captured_at": _now(),
            "item_count": 0,
            "currency": None,
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

                meta["currency"] = data.get("currency")

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
                        "key": item.get("key"),
                        "properties": item.get("properties"),
                        "selling_plan_id": item.get("selling_plan_allocation", {}).get("selling_plan", {}).get("id") if isinstance(item.get("selling_plan_allocation"), dict) else item.get("selling_plan_id"),
                        "price": item.get("price"),
                        "final_price": item.get("final_price"),
                        "original_price": item.get("original_price"),
                        "line_level_discount_allocations": item.get("line_level_discount_allocations"),
                        "discounts": item.get("discounts"),
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

    async def _take_screenshot(self, page, tag: str) -> Optional[str]:
        """Take screenshot with non-mutative visual CSS PII masking. Returns None if safe redaction or element scoping cannot be guaranteed."""
        os.makedirs("reports/screenshots", exist_ok=True)
        filename = f"cart_consistency_{tag}_{int(datetime.now().timestamp())}.png"
        path = os.path.join("reports/screenshots", filename)

        try:
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
                                if (!parentForm || el.matches('[data-pii-text], .user-info, .customer-info')) {
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
                logger.debug("Failed to apply visual PII masking; suppressing screenshot.")
                return None

            drawer_or_form = page.locator('.cart-drawer, #CartDrawer, form[action*="/cart/add"], .cart-page').first
            if await drawer_or_form.count() > 0 and await drawer_or_form.is_visible():
                await drawer_or_form.screenshot(path=path)
            else:
                return None

            return path
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
