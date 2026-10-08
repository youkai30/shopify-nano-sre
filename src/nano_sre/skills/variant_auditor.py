"""Shopify Variant Auditor Skill - Audits Variant Identity, Price, Image, and Availability."""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urljoin, urlparse

from nano_sre.agent.core import Skill, SkillResult
from nano_sre.skills.shopify_shopper import _cart_root

logger = logging.getLogger(__name__)


class ShopifyVariantAuditor(Skill):
    """Audits Shopify product variants for identity, price, image, and availability sync."""

    def name(self) -> str:
        return "shopify_variant_auditor"

    async def run(self, context: dict[str, Any]) -> SkillResult:
        page = context.get("page")
        base_url: str = context.get("base_url", "")
        steps: List[str] = []
        findings: Dict[str, Any] = {}

        if not page or not base_url:
            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary="Missing page or base_url in context",
                details={"steps": steps, "findings": findings},
            )

        # Network add-to-cart listener
        intercepted_payloads: List[Dict[str, Any]] = []

        async def handle_request(request):
            if "/cart/add" in request.url and request.method == "POST":
                try:
                    post_data = request.post_data or ""
                    parsed_payload = {}
                    content_type = request.headers.get("content-type") or ""
                    if "application/json" in content_type:
                        import json
                        parsed_payload = json.loads(post_data)
                    else:
                        parsed_form = parse_qs(post_data)
                        for k, v in parsed_form.items():
                            parsed_payload[k] = v[0] if len(v) == 1 else v
                    intercepted_payloads.append({
                        "url": request.url,
                        "payload": parsed_payload,
                        "timestamp": _now(),
                    })
                except Exception as exc:
                    logger.debug("Failed parsing add-to-cart request: %s", type(exc).__name__)

        page.on("request", handle_request)

        try:
            # 1. Navigate to PDP
            await page.goto(base_url, wait_until="commit", timeout=60000)
            await asyncio.sleep(2)
            steps.append("Visited Product Page")

            # 2. Fetch official Ajax Product JS API: GET /{locale}/products/{handle}.js
            product_json = await _fetch_product_js(page, base_url)
            if not product_json or not isinstance(product_json.get("variants"), list):
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary="Could not fetch reliable product.js variant data",
                    details={"steps": steps, "reason_code": "missing_product_js"},
                )

            variants = product_json.get("variants", [])
            if len(variants) < 2:
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary="Product does not have multiple variants to audit",
                    details={"steps": steps, "variant_count": len(variants), "reason_code": "single_variant"},
                )

            # 3. Locate Offer Form
            offer_form = page.locator('form[action*="/cart/add"], form.prd-ProductOffers_Form').first
            if await offer_form.count() == 0:
                offer_form = page.locator('form').first

            # 4. Target an available non-default variant for Identity, Price, Image audits
            target_variant = None
            for v in variants[1:]:
                if v.get("available", True):
                    target_variant = v
                    break
            if not target_variant:
                target_variant = variants[1]

            target_variant_id = target_variant.get("id")
            target_title = target_variant.get("title", "")
            target_price = target_variant.get("price")
            target_image = target_variant.get("featured_image")
            target_image_src = target_image.get("src") if isinstance(target_image, dict) else None

            # 5. Select target variant via structured form controls/swatches
            option_selected = await _select_variant_options(page, offer_form, target_title)
            if not option_selected:
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary=f"Could not select variant UI options for '{target_title}'",
                    details={"steps": steps, "reason_code": "option_selection_failed"},
                )

            await _wait_for_dom_stabilization(page)
            steps.append(f"Selected variant UI: {target_title} (ID: {target_variant_id})")

            # --- SUB-AUDIT A: Variant Identity ---
            identity_audit = await _audit_identity(
                page, offer_form, intercepted_payloads, target_variant_id, target_variant
            )
            findings["identity"] = identity_audit

            # --- SUB-AUDIT B: Variant Price ---
            price_audit = await _audit_price(page, target_price, product_json)
            findings["price"] = price_audit

            # --- SUB-AUDIT C: Variant Image ---
            image_audit = await _audit_image(page, target_image_src, product_json)
            findings["image"] = image_audit

            # --- SUB-AUDIT D: Variant Availability (Separate Path) ---
            availability_audit = await _audit_availability_path(page, offer_form, variants)
            findings["availability"] = availability_audit

            # 6. Evaluate Overall Status
            statuses = [findings[k]["status"] for k in ("identity", "price", "image", "availability") if findings[k]["status"] != "NOT_APPLICABLE"]
            if "FAIL" in statuses:
                overall_status = "FAIL"
                summary = "One or more variant checks failed (Mismatch detected)"
            elif all(s == "PASS" for s in statuses):
                overall_status = "PASS"
                summary = "All applicable variant checks passed successfully"
            else:
                overall_status = "WARN"
                summary = "Variant audit completed with warnings or unconfirmed contexts"

            return SkillResult(
                skill_name=self.name(),
                status=overall_status,
                summary=summary,
                details={
                    "steps": steps,
                    "target_variant": {
                        "id": target_variant_id,
                        "title": target_title,
                    },
                    "findings": findings,
                },
            )

        except Exception as exc:
            logger.debug("Variant auditor execution error: %s", type(exc).__name__)
            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary="Variant audit encountered an execution error",
                error=f"variant_auditor_error:{type(exc).__name__}",
                details={"steps": steps, "findings": findings},
            )
        finally:
            try:
                page.remove_listener("request", handle_request)
            except Exception:
                pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _fetch_product_js(page, base_url: str) -> Optional[Dict[str, Any]]:
    """Fetch official GET /{locale}/products/{handle}.js using same-origin browser context."""
    try:
        parsed = urlparse(page.url)
        path = parsed.path.rstrip("/")
        if not path.endswith(".js"):
            if path.endswith(".json"):
                path = path[:-5] + ".js"
            else:
                path = path + ".js"

        # Fetch via page evaluate to guarantee same-origin session
        data = await page.evaluate("""async (url) => {
            try {
                const res = await fetch(url);
                if (res.ok) {
                    return await res.json();
                }
            } catch (e) {}
            return null;
        }""", path)

        if isinstance(data, dict) and isinstance(data.get("variants"), list):
            return data
    except Exception as exc:
        logger.debug("Error fetching product.js: %s", type(exc).__name__)

    return None


async def _select_variant_options(page, offer_form, target_title: str) -> bool:
    """Select target variant options using structured form controls or swatches."""
    try:
        parts = [p.strip() for p in target_title.split("/") if p.strip()]
        selected_any = False
        for option_value in parts:
            # 1. Structured Form Radio inputs or Option buttons inside form
            swatch = offer_form.locator(
                f'input[type="radio"][value="{option_value}" i]:visible, '
                f'button[data-option-value="{option_value}"]:visible, '
                f'button:has-text("{option_value}"):visible, '
                f'label:has-text("{option_value}"):visible'
            ).first
            if await swatch.count() > 0:
                await swatch.click(timeout=5000)
                await asyncio.sleep(0.5)
                selected_any = True
                continue

            # 2. Select dropdowns inside form
            selects = offer_form.locator("select:visible")
            count = await selects.count()
            for i in range(count):
                sel = selects.nth(i)
                opts_text = await sel.inner_text()
                if option_value.lower() in opts_text.lower():
                    await sel.select_option(label=option_value)
                    await asyncio.sleep(0.5)
                    selected_any = True
                    break
        return selected_any
    except Exception as exc:
        logger.debug("Option selection failed: %s", type(exc).__name__)
        return False


async def _wait_for_dom_stabilization(page, timeout_ms: int = 3000):
    """Wait for DOM updates to stabilize using state-based polling where possible."""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        await asyncio.sleep(1)


async def _get_cart_snapshot(page) -> Optional[Dict[int, int]]:
    """Get mapping of variant_id -> quantity from /{locale}/cart.js using safe absolute URL."""
    counts: Dict[int, int] = {}
    try:
        root = await _cart_root(page)
        cart_url = root + "cart.js"
        resp = await page.request.get(cart_url, timeout=10000)
        if resp.ok:
            data = await resp.json()
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                for item in data.get("items", []):
                    vid = item.get("variant_id") or item.get("id")
                    qty = item.get("quantity", 0)
                    if isinstance(vid, int) and isinstance(qty, int):
                        counts[vid] = counts.get(vid, 0) + qty
                return counts
    except Exception as exc:
        logger.debug("Failed getting cart snapshot: %s", type(exc).__name__)
    return None


async def _audit_identity(
    page, offer_form, intercepted_payloads: List[Dict[str, Any]], target_variant_id: int, target_variant: Dict[str, Any]
) -> Dict[str, Any]:
    """Audit A: Variant Identity verification with pre/post cart snapshots and request attribution."""
    # Pre-add cart snapshot
    pre_cart = await _get_cart_snapshot(page)
    if pre_cart is None:
        return {
            "status": "WARN",
            "reason_code": "pre_cart_read_failed",
            "expected_variant_id": target_variant_id,
            "summary": "Could not read pre-add cart state from cart.js",
        }

    # Read form hidden id
    form_variant_id = None
    try:
        id_input = offer_form.locator('input[name="id"], select[name="id"]').first
        if await id_input.count() > 0:
            val = await id_input.get_attribute("value") or await id_input.input_value()
            if val and val.isdigit():
                form_variant_id = int(val)
    except Exception:
        pass

    # Click Add to Cart
    atc_button = offer_form.locator('button[name="add"], button[type="submit"]:has-text("Add")').first
    if await atc_button.count() == 0:
        atc_button = page.locator('button.add-to-cart:visible, button[name="add"]:visible').first

    if await atc_button.count() > 0 and await atc_button.is_enabled():
        try:
            await atc_button.click(timeout=10000)
            await asyncio.sleep(2)
        except Exception:
            pass

    # Post-add cart snapshot
    post_cart = await _get_cart_snapshot(page)
    if post_cart is None:
        return {
            "status": "WARN",
            "reason_code": "post_cart_read_failed",
            "expected_variant_id": target_variant_id,
            "summary": "Could not read post-add cart state from cart.js",
        }

    # Intercepted request payload ID
    sent_variant_id = None
    if intercepted_payloads:
        last_payload = intercepted_payloads[-1]["payload"]
        raw_id = last_payload.get("id") or last_payload.get("items[0][id]")
        if isinstance(raw_id, list):
            raw_id = raw_id[0]
        if raw_id and str(raw_id).isdigit():
            sent_variant_id = int(raw_id)

    # Calculate actual added delta
    pre_qty = pre_cart.get(target_variant_id, 0)
    post_qty = post_cart.get(target_variant_id, 0)
    target_added = post_qty > pre_qty

    # Check for mismatches
    if form_variant_id and form_variant_id != target_variant_id:
        return {
            "status": "FAIL",
            "reason_code": "form_id_mismatch",
            "expected_variant_id": target_variant_id,
            "observed_form_variant_id": form_variant_id,
            "summary": f"UI selected variant {target_variant_id} but form ID remained {form_variant_id}",
        }

    if sent_variant_id and sent_variant_id != target_variant_id:
        return {
            "status": "FAIL",
            "reason_code": "payload_id_mismatch",
            "expected_variant_id": target_variant_id,
            "observed_sent_variant_id": sent_variant_id,
            "summary": f"UI selected variant {target_variant_id} but add-to-cart payload sent {sent_variant_id}",
        }

    # Check if a wrong variant quantity increased instead
    for vid, post_q in post_cart.items():
        if vid != target_variant_id and post_q > pre_cart.get(vid, 0):
            return {
                "status": "FAIL",
                "reason_code": "wrong_variant_added_to_cart",
                "expected_variant_id": target_variant_id,
                "observed_added_variant_id": vid,
                "summary": f"Expected variant {target_variant_id} to be added, but wrong variant {vid} was added to cart",
            }

    # STRICT ATTRIBUTION: Require actual quantity increase for PASS
    if target_added and sent_variant_id == target_variant_id:
        return {
            "status": "PASS",
            "expected_variant_id": target_variant_id,
            "summary": f"Variant {target_variant_id} identity verified in request and cart delta (+{post_qty - pre_qty})",
        }

    # Pre-existing variant without quantity increase -> WARN (not PASS)
    if sent_variant_id == target_variant_id and post_qty <= pre_qty and pre_qty > 0:
        return {
            "status": "WARN",
            "reason_code": "pre_existing_variant_no_quantity_increase",
            "expected_variant_id": target_variant_id,
            "summary": f"Variant {target_variant_id} sent in payload but cart quantity did not increase (pre: {pre_qty}, post: {post_qty})",
        }

    # Empty cart or add request failed -> WARN (not PASS)
    if post_qty == 0:
        return {
            "status": "WARN",
            "reason_code": "cart_remained_empty_after_add",
            "expected_variant_id": target_variant_id,
            "summary": f"Add to cart payload was sent for variant {target_variant_id} but cart remained empty",
        }

    return {
        "status": "WARN",
        "reason_code": "identity_unconfirmed",
        "expected_variant_id": target_variant_id,
        "summary": "Could not conclusively verify variant add-to-cart attribution",
    }


async def _audit_price(page, target_price_cents: Optional[int], product_json: Dict[str, Any]) -> Dict[str, Any]:
    """Audit B: Variant Price verification with exact money representation."""
    if target_price_cents is None or not isinstance(target_price_cents, int):
        return {"status": "WARN", "reason_code": "missing_expected_price", "summary": "Expected price cents not specified"}

    try:
        # Search page text for price elements
        price_locators = page.locator(
            '.price:visible, .product-price:visible, [data-product-price]:visible, span.money:visible'
        )
        count = await price_locators.count()
        observed_prices: List[int] = []
        for i in range(min(count, 10)):
            txt = await price_locators.nth(i).inner_text()
            # Precise currency extraction: match $XX.YY or XX.YY as well as whole dollar $XX or XX USD
            money_match = re.search(r"\b(\d+)(?:[.,](\d{2}))?\b", txt)
            if money_match:
                dollars = int(money_match.group(1))
                cents_part = int(money_match.group(2)) if money_match.group(2) else 0
                cents = dollars * 100 + cents_part
                observed_prices.append(cents)

        if target_price_cents in observed_prices:
            return {
                "status": "PASS",
                "expected_price_cents": target_price_cents,
                "observed_prices": observed_prices,
                "summary": f"PDP price matches target variant price ({target_price_cents} cents)",
            }

        # Check for stale price match from another variant
        for v in product_json.get("variants", []):
            v_price = v.get("price")
            if v_price and v_price != target_price_cents and v_price in observed_prices:
                return {
                    "status": "FAIL",
                    "reason_code": "stale_variant_price",
                    "expected_price_cents": target_price_cents,
                    "observed_prices": observed_prices,
                    "summary": f"PDP displayed stale price ({v_price} cents) from previous variant instead of {target_price_cents} cents",
                }

        if observed_prices:
            return {
                "status": "FAIL",
                "reason_code": "variant_price_mismatch",
                "expected_price_cents": target_price_cents,
                "observed_prices": observed_prices,
                "summary": f"PDP displayed price {observed_prices} which did not match expected {target_price_cents} cents",
            }
    except Exception as exc:
        logger.debug("Price audit failed: %s", type(exc).__name__)

    return {"status": "WARN", "reason_code": "price_unconfirmed", "summary": "Could not confirm PDP price update"}


async def _audit_image(page, target_image_src: Optional[str], product_json: Dict[str, Any]) -> Dict[str, Any]:
    """Audit C: Variant Image verification with NOT_APPLICABLE status if no dedicated image exists."""
    if not target_image_src or not isinstance(target_image_src, str):
        return {
            "status": "NOT_APPLICABLE",
            "summary": "Target variant does not have a dedicated image (shared image is valid)",
        }

    current_src = ""
    srcset = ""

    try:
        clean_target_filename = urlparse(target_image_src).path.split("/")[-1].split("?")[0]
        if not clean_target_filename:
            return {"status": "NOT_APPLICABLE", "summary": "No target image filename to compare"}

        # Inspect visible active main image via currentSrc property
        main_img = page.locator('img[src*="/products/"]:visible, .product-single__photo img:visible').first
        if await main_img.count() > 0:
            current_src = await main_img.evaluate("el => el.currentSrc || el.src || ''")
            srcset = await main_img.get_attribute("srcset") or ""

            if clean_target_name_in(clean_target_filename, current_src, srcset):
                return {
                    "status": "PASS",
                    "expected_image": clean_target_filename,
                    "summary": "PDP updated main image to target variant image",
                }

            # Check for stale image belonging to another variant
            for v in product_json.get("variants", []):
                v_img = v.get("featured_image")
                v_src = v_img.get("src") if isinstance(v_img, dict) else None
                if v_src and v_src != target_image_src:
                    v_filename = urlparse(v_src).path.split("/")[-1].split("?")[0]
                    if v_filename and clean_target_name_in(v_filename, current_src, srcset):
                        return {
                            "status": "FAIL",
                            "reason_code": "stale_variant_image",
                            "expected_image": clean_target_filename,
                            "observed_image": current_src,
                            "summary": f"PDP main image did not update to target variant image {clean_target_filename}",
                        }

            return {
                "status": "WARN",
                "reason_code": "image_mismatch_unconfirmed",
                "expected_image": clean_target_filename,
                "summary": f"PDP main image did not display expected target variant image {clean_target_filename}",
            }

        return {
            "status": "WARN",
            "reason_code": "image_unrendered",
            "summary": f"PDP main image element was unrendered or missing for variant {clean_target_filename}",
        }
    except Exception as exc:
        logger.debug("Image audit failed: %s", type(exc).__name__)

    return {"status": "WARN", "reason_code": "image_unconfirmed", "summary": "Could not verify variant image update"}


def clean_target_name_in(target_filename: str, current_src: str, srcset: str) -> bool:
    return target_filename.lower() in current_src.lower() or target_filename.lower() in srcset.lower()


async def _audit_availability_path(page, offer_form, variants: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Audit D: Variant Availability Display on separate path WITHOUT clicking Add to Cart for sold-out variants."""
    sold_out_variant = None
    for v in variants:
        if not v.get("available", True):
            sold_out_variant = v
            break

    if not sold_out_variant:
        return {
            "status": "NOT_APPLICABLE",
            "summary": "Product does not contain a sold-out variant to test availability transitions",
        }

    try:
        # Select sold out variant in UI
        sold_out_title = sold_out_variant.get("title", "")
        await _select_variant_options(page, offer_form, sold_out_title)
        await _wait_for_dom_stabilization(page)

        # Inspect button state WITHOUT clicking
        atc_button = offer_form.locator('button[name="add"], button[type="submit"]').first
        if await atc_button.count() == 0:
            atc_button = page.locator('button.add-to-cart:visible, button[name="add"]:visible').first

        if await atc_button.count() > 0:
            btn_text = (await atc_button.inner_text()).strip().upper()
            is_enabled = await atc_button.is_enabled()
            is_aria_disabled = (await atc_button.get_attribute("aria-disabled")) == "true"

            # Check if sold out variant displays enabled Add to Cart button
            if is_enabled and not is_aria_disabled and not ("SOLD" in btn_text or "غير متوفر" in btn_text or "EPUISÉ" in btn_text or "OUT OF" in btn_text):
                return {
                    "status": "FAIL",
                    "reason_code": "sold_out_variant_enabled",
                    "expected_available": False,
                    "observed_button_text": btn_text,
                    "observed_enabled": is_enabled,
                    "summary": f"Sold-out variant '{sold_out_title}' displayed enabled Add to Cart button",
                }

            return {
                "status": "PASS",
                "expected_available": False,
                "summary": f"Sold-out variant '{sold_out_title}' correctly displayed disabled or sold-out button state",
            }
    except Exception as exc:
        logger.debug("Availability audit failed: %s", type(exc).__name__)

    return {"status": "WARN", "reason_code": "availability_unconfirmed", "summary": "Could not verify sold-out variant UI state"}
