"""Shopify Variant Auditor Skill - Audits variant selection, pricing, images, and availability."""

import asyncio
import logging
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from typing import Any, Optional, Tuple
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from nano_sre.agent.core import Skill, SkillResult

logger = logging.getLogger(__name__)


def parse_price_cents(text: str) -> Optional[int]:
    """Parse a price string into exact integer cents using Decimal, avoiding float precision errors."""
    if not text or not isinstance(text, str):
        return None

    # Find the numeric price string in text (e.g. $1,500.00 or 15.99)
    match = re.search(r"\d+(?:[.,]\d+)*", text)
    if not match:
        return None

    num_str = match.group(0)

    # Handle thousands and decimal separators
    if "." in num_str and "," in num_str:
        last_dot = num_str.rfind(".")
        last_comma = num_str.rfind(",")
        if last_dot > last_comma:  # e.g. 1,500.00
            clean = num_str.replace(",", "")
        else:  # e.g. 1.500,00
            clean = num_str.replace(".", "").replace(",", ".")
    elif "," in num_str:
        parts = num_str.split(",")
        if len(parts) == 2 and len(parts[1]) == 2:  # e.g. 15,99
            clean = num_str.replace(",", ".")
        elif len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):  # e.g. 1,500
            clean = num_str.replace(",", "")
        elif len(parts) == 2 and len(parts[1]) == 3:  # e.g. 1,500
            clean = num_str.replace(",", "")
        else:
            clean = num_str.replace(",", ".")
    elif "." in num_str:
        parts = num_str.split(".")
        if len(parts) == 2 and len(parts[1]) in (1, 2):  # e.g. 15.99 or 15.9
            clean = num_str
        elif len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):  # e.g. 1.500
            clean = num_str.replace(".", "")
        else:
            clean = num_str
    else:
        clean = num_str

    try:
        dec = Decimal(clean)
        if "." in clean:
            dec = dec.quantize(Decimal("0.01"))
            cents = int(dec * 100)
        else:
            cents = int(dec * 100)
        return cents
    except (InvalidOperation, ValueError):
        return None


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


class ShopifyVariantAuditor(Skill):
    """Audits Shopify variant selection logic, price updates, images, and availability."""

    def name(self) -> str:
        return "shopify_variant_auditor"

    async def run(self, context: dict[str, Any]) -> SkillResult:
        page = context.get("page")
        base_url: str = context.get("base_url", "")

        if not page or not base_url:
            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary="Missing page or base_url in context",
                details={},
            )

        checks: dict[str, dict[str, Any]] = {}
        steps = []

        try:
            current_url = page.url or base_url
            origin, locale = _get_locale_prefix_and_origin(current_url)
            handle = _extract_handle_from_url(current_url)

            # 1. Discover Product URL & Handle if not already on product page
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
                    summary="Could not discover a product handle to audit variants",
                    details={"steps": steps},
                )

            steps.append(f"Auditing product handle: {handle}")

            # 2. Fetch Ajax Product API JSON preserving locale: GET /{locale}/products/{handle}.js
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
                    details={"steps": steps},
                )

            variants_list = product_data.get("variants", [])
            options_list = product_data.get("options", [])

            if not variants_list:
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary="Product JSON contains no variants",
                    details={"steps": steps, "product_data": product_data},
                )

            # 3. Locate Main PDP Product Form / Container
            main_form = page.locator(
                'form[action*="/cart/add"], form.prd-ProductOffers_Form, [data-type="add-to-cart-form"]'
            ).first

            if await main_form.count() == 0:
                main_form = page.locator('form:has(button[name="add"]), section[data-product-single-media-group]').first

            await asyncio.sleep(1)

            # Execute 4 Checks
            selected_options, complete_options = await self._get_selected_options_from_dom(page, main_form, options_list)
            is_ambiguous_pricing = await self._check_pricing_ambiguity(page, main_form)

            # Target variant selection
            target_variant = None
            if len(variants_list) > 1:
                for v in variants_list[1:]:
                    if v.get("available", True):
                        target_variant = v
                        break
            if not target_variant:
                target_variant = variants_list[0]

            if len(variants_list) > 1 and target_variant != variants_list[0]:
                await self._select_variant_in_dom(page, main_form, target_variant, options_list)
                await asyncio.sleep(1)
                selected_options, complete_options = await self._get_selected_options_from_dom(page, main_form, options_list)

            matched_variant = self._match_options_to_variant(selected_options, variants_list, options_list)
            expected_variant = matched_variant or target_variant

            # CHECK 1: Variant Identity Check
            checks["variant_identity"] = await self._check_variant_identity(
                page=page,
                main_form=main_form,
                expected_variant=expected_variant,
                complete_options=complete_options,
                origin=origin,
                locale=locale,
            )

            # CHECK 2: Variant Price Check
            if complete_options and checks["variant_identity"]["status"] in ("PASS", "WARN"):
                checks["variant_price"] = await self._check_variant_price(
                    page=page,
                    main_form=main_form,
                    expected_variant=expected_variant,
                    is_ambiguous_pricing=is_ambiguous_pricing,
                )
            else:
                checks["variant_price"] = {
                    "status": "WARN",
                    "summary": "Option selection for target variant was unproven or incomplete",
                }

            # CHECK 3: Variant Image Check
            if complete_options and checks["variant_identity"]["status"] in ("PASS", "WARN"):
                checks["variant_image"] = await self._check_variant_image(
                    page=page,
                    main_form=main_form,
                    expected_variant=expected_variant,
                    product_data=product_data,
                )
            else:
                checks["variant_image"] = {
                    "status": "WARN",
                    "summary": "Option selection for target variant was unproven or incomplete",
                }

            # CHECK 4: Variant Availability Display Check
            checks["variant_availability_display"] = await self._check_variant_availability(
                page=page,
                main_form=main_form,
                variants_list=variants_list,
                options_list=options_list,
            )

            # Determine Overall Skill Status
            statuses = [c["status"] for c in checks.values()]
            if "FAIL" in statuses:
                overall_status = "FAIL"
                failed_checks = [k for k, v in checks.items() if v["status"] == "FAIL"]
                summary = f"Variant audit failed: {', '.join(failed_checks)}"
            elif "WARN" in statuses:
                overall_status = "WARN"
                warned_checks = [k for k, v in checks.items() if v["status"] == "WARN"]
                summary = f"Variant audit completed with warnings: {', '.join(warned_checks)}"
            else:
                overall_status = "PASS"
                summary = "All variant checks passed successfully"

            return SkillResult(
                skill_name=self.name(),
                status=overall_status,
                summary=summary,
                details={
                    "steps": steps,
                    "product_handle": handle,
                    "expected_variant_id": expected_variant.get("id"),
                    "checks": checks,
                },
            )

        except Exception as e:
            logger.exception("Error in shopify_variant_auditor: %s", e)
            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary=f"Shopify variant auditor error: {str(e)}",
                error=str(e),
                details={"steps": steps, "checks": checks},
            )

    async def _get_selected_options_from_dom(
        self, page, main_form, options_list: list[Any]
    ) -> tuple[dict[str, str], bool]:
        """Read selected options from DOM controls in the main PDP form."""
        selected_options = {}

        option_names = []
        for opt in options_list:
            if isinstance(opt, dict):
                option_names.append(opt.get("name", ""))
            elif isinstance(opt, str):
                option_names.append(opt)

        form_loc = main_form if await main_form.count() > 0 else page

        # 1. Select elements
        selects = form_loc.locator("select")
        select_count = await selects.count()
        for i in range(select_count):
            sel = selects.nth(i)
            if not await sel.is_visible():
                continue
            val = await sel.evaluate("el => el.value")
            name_attr = (await sel.get_attribute("name") or "").lower()
            id_attr = (await sel.get_attribute("id") or "").lower()

            for opt_name in option_names:
                if opt_name.lower() in name_attr or opt_name.lower() in id_attr or f"option{option_names.index(opt_name)+1}" in name_attr or f"option{option_names.index(opt_name)+1}" in id_attr:
                    selected_options[opt_name] = val

        # 2. Checked radio buttons
        radios = form_loc.locator('input[type="radio"]:checked')
        radio_count = await radios.count()
        for i in range(radio_count):
            r = radios.nth(i)
            val = await r.evaluate("el => el.value")
            name_attr = (await r.get_attribute("name") or "").lower()
            for opt_name in option_names:
                if opt_name.lower() in name_attr or f"option{option_names.index(opt_name)+1}" in name_attr:
                    selected_options[opt_name] = val

        # 3. Active swatches
        swatches = form_loc.locator(
            '.swatch.selected, .swatch.active, [data-option-value].active, button.selected, button.active, [aria-checked="true"], [aria-selected="true"], button.swatch'
        )
        swatch_count = await swatches.count()
        for i in range(swatch_count):
            sw = swatches.nth(i)
            val = await sw.get_attribute("data-value") or await sw.get_attribute("data-option-value") or await sw.inner_text()
            val = val.strip() if val else ""
            opt_name_attr = await sw.get_attribute("data-option-name") or (option_names[0] if len(option_names) == 1 else "")
            if opt_name_attr and val:
                selected_options[opt_name_attr] = val

        complete = len(option_names) > 0 and all(name in selected_options for name in option_names)
        return selected_options, complete

    async def _select_variant_in_dom(self, page, main_form, target_variant: dict[str, Any], options_list: list[Any]) -> bool:
        """Click or change DOM controls using real Playwright actions to select target_variant options."""
        form_loc = main_form if await main_form.count() > 0 else page
        variant_options = target_variant.get("options", [])

        option_names = []
        for opt in options_list:
            if isinstance(opt, dict):
                option_names.append(opt.get("name", ""))
            elif isinstance(opt, str):
                option_names.append(opt)

        all_selected = True

        for idx, val in enumerate(variant_options):
            opt_name = option_names[idx] if idx < len(option_names) else f"Option{idx+1}"
            opt_selected = False

            # 1. Try select dropdown via Playwright select_option
            select_loc = form_loc.locator(
                f'select[name*="{opt_name}" i], select[name*="option{idx+1}" i], select[id*="{opt_name}" i], select'
            )
            select_count = await select_loc.count()
            for i in range(select_count):
                sel = select_loc.nth(i)
                if not await sel.is_visible():
                    continue
                try:
                    # Select option using Playwright select_option
                    await sel.select_option(label=val, timeout=1000)
                    opt_selected = True
                    break
                except Exception:
                    try:
                        await sel.select_option(value=val, timeout=1000)
                        opt_selected = True
                        break
                    except Exception:
                        pass

            if opt_selected:
                continue

            # 2. Try radio button or swatch button/label click via Playwright click
            btn_loc = form_loc.locator(
                f'input[type="radio"][value="{val}" i], button:has-text("{val}"), [data-value="{val}"], label:has-text("{val}")'
            ).first
            if await btn_loc.count() > 0 and await btn_loc.is_visible():
                try:
                    await btn_loc.click(timeout=2000)
                    opt_selected = True
                except Exception:
                    pass

            if not opt_selected:
                all_selected = False

        return all_selected

    def _match_options_to_variant(
        self, selected_options: dict[str, str], variants_list: list[dict[str, Any]], options_list: list[Any]
    ) -> Optional[dict[str, Any]]:
        """Match selected option key-values to a variant in variants_list."""
        if not selected_options:
            return None

        for v in variants_list:
            v_options = v.get("options", [])
            match = True
            for idx, opt_item in enumerate(options_list):
                opt_name = opt_item.get("name") if isinstance(opt_item, dict) else opt_item
                sel_val = selected_options.get(opt_name)
                if sel_val and idx < len(v_options):
                    if str(v_options[idx]).lower() != str(sel_val).lower():
                        match = False
                        break
            if match and len(selected_options) > 0:
                return v

        return None

    async def _check_pricing_ambiguity(self, page, main_form) -> bool:
        """Check if PDP pricing context is ambiguous."""
        try:
            form_loc = main_form if await main_form.count() > 0 else page
            price_elems = form_loc.locator('.price:not(.compare-at-price), [data-product-price], .product-single__price')
            count = await price_elems.count()
            if count > 1:
                texts = set()
                for i in range(count):
                    t = (await price_elems.nth(i).inner_text()).strip()
                    if t and not any(term in t.lower() for term in ("save", "off", "compare")):
                        texts.add(t)
                if len(texts) > 1:
                    return True
        except Exception:
            pass
        return False

    async def _fetch_cart_snapshot(self, page, origin: str, locale: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Fetch safe cart.js snapshot with payload consistency validation."""
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
                item_count = data.get("item_count")
                if not isinstance(item_count, int) or item_count < 0:
                    meta["status"] = "inconsistent_payload"
                    return meta, []

                raw_items = data.get("items", [])
                if not isinstance(raw_items, list):
                    meta["status"] = "inconsistent_payload"
                    return meta, []

                calculated_total = 0
                for item in raw_items:
                    qty = item.get("quantity")
                    vid = item.get("variant_id")
                    if not isinstance(qty, int) or qty <= 0 or not isinstance(vid, int) or vid <= 0:
                        meta["status"] = "inconsistent_payload"
                        return meta, []
                    calculated_total += qty
                    items.append({
                        "variant_id": vid,
                        "product_id": item.get("product_id"),
                        "quantity": qty,
                        "selling_plan_id": item.get("selling_plan_id"),
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

    async def _check_variant_identity(
        self,
        page,
        main_form,
        expected_variant: dict[str, Any],
        complete_options: bool,
        origin: str,
        locale: str,
    ) -> dict[str, Any]:
        """Check 1: Variant Identity Verification."""
        expected_variant_id = expected_variant.get("id")

        if not complete_options:
            return {
                "status": "WARN",
                "summary": "Partial or ambiguous option selection in DOM",
                "expected_variant_id": expected_variant_id,
            }

        # Take Cart Snapshot 1 (Before ATC)
        meta_before, items_before = await self._fetch_cart_snapshot(page, origin, locale)

        # If pre-cart read failed (e.g. HTTP 503 or error), return WARN
        if meta_before["status"] not in ("empty", "nonempty"):
            return {
                "status": "WARN",
                "summary": f"Pre-cart read failed (status={meta_before['status']}); variant identity unconfirmed",
                "meta_before": meta_before,
            }

        qty_before = sum(item["quantity"] for item in items_before if item["variant_id"] == expected_variant_id)

        # Intercept ATC network request directly tied to click
        add_request_data = {"captured": False, "variant_id": None, "quantity": None, "status": None}

        def on_request(request):
            if "/cart/add" in request.url and request.method.upper() == "POST":
                add_request_data["captured"] = True
                try:
                    post_data = request.post_data or ""
                    # Check JSON or URL-encoded form data
                    if post_data.startswith("{"):
                        try:
                            import json
                            payload = json.loads(post_data)
                            vid = payload.get("id") or payload.get("variant_id")
                            if vid:
                                add_request_data["variant_id"] = int(vid)
                        except Exception:
                            pass
                    else:
                        parsed = parse_qs(post_data)
                        v_val = parsed.get("id", [None])[0] or parsed.get("variant_id", [None])[0]
                        if v_val and v_val.isdigit():
                            add_request_data["variant_id"] = int(v_val)
                except Exception:
                    pass

        def on_response(response):
            if "/cart/add" in response.url and response.request.method.upper() == "POST":
                add_request_data["status"] = response.status

        page.on("request", on_request)
        page.on("response", on_response)

        # Specifically locate Buy / Add to Cart button inside main form (exclude option swatch buttons)
        form_loc = main_form if await main_form.count() > 0 else page
        atc_button = form_loc.locator('button[name="add"], button[type="submit"]:has-text("add"), button[type="submit"]:has-text("bag"), button.add-to-cart, [data-add-to-cart]').first

        if await atc_button.count() == 0:
            atc_button = page.locator('button[name="add"], button[type="submit"]:has-text("add"), button.add-to-cart').first

        if await atc_button.count() == 0 or not await atc_button.is_enabled():
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)
            return {
                "status": "FAIL",
                "summary": "Add to Cart button missing or disabled for target variant",
                "expected_variant_id": expected_variant_id,
            }

        try:
            await atc_button.click(timeout=10000)
            await asyncio.sleep(2)
        except Exception as e:
            logger.debug("Click ATC failed: %s", e)

        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)

        # If add request returned an HTTP error (e.g., 422), return WARN
        if add_request_data["status"] and add_request_data["status"] >= 400:
            return {
                "status": "WARN",
                "summary": f"Add to Cart request returned HTTP {add_request_data['status']}",
                "add_request_summary": add_request_data,
            }

        # Take Cart Snapshot 2 (After ATC)
        meta_after, items_after = await self._fetch_cart_snapshot(page, origin, locale)

        if meta_after["status"] not in ("empty", "nonempty"):
            return {
                "status": "WARN",
                "summary": f"Post-cart read failed (status={meta_after['status']}); variant identity unconfirmed",
                "meta_before": meta_before,
                "meta_after": meta_after,
            }

        qty_after = sum(item["quantity"] for item in items_after if item["variant_id"] == expected_variant_id)
        delta_quantity = qty_after - qty_before

        added_variant_id = add_request_data.get("variant_id")

        # Proven wrong variant ID -> FAIL
        if added_variant_id and str(added_variant_id) != str(expected_variant_id):
            return {
                "status": "FAIL",
                "summary": f"Added wrong variant ID {added_variant_id} instead of expected {expected_variant_id}",
                "expected_variant_id": expected_variant_id,
                "added_variant_id": added_variant_id,
                "delta_quantity": delta_quantity,
            }

        # Unconfirmed quantity increase without proven wrong ID -> WARN
        if delta_quantity <= 0:
            return {
                "status": "WARN",
                "summary": f"Unconfirmed variant identity: quantity did not increase (delta_quantity={delta_quantity})",
                "expected_variant_id": expected_variant_id,
                "delta_quantity": delta_quantity,
                "meta_before": meta_before,
                "meta_after": meta_after,
            }

        return {
            "status": "PASS",
            "summary": "Variant selection matches added cart variant and quantity increased",
            "expected_variant_id": expected_variant_id,
            "delta_quantity": delta_quantity,
            "meta_before": meta_before,
            "meta_after": meta_after,
            "add_request_summary": {
                "captured": add_request_data["captured"],
                "status": add_request_data["status"],
            },
        }

    async def _check_variant_price(
        self,
        page,
        main_form,
        expected_variant: dict[str, Any],
        is_ambiguous_pricing: bool,
    ) -> dict[str, Any]:
        """Check 2: Variant Price Verification with Decimal precision."""
        if is_ambiguous_pricing:
            return {
                "status": "WARN",
                "summary": "Unresolved or ambiguous pricing context on PDP",
            }

        v_price = expected_variant.get("price")
        if v_price is None:
            return {"status": "WARN", "summary": "Expected variant price missing in product JSON"}

        if isinstance(v_price, int):
            expected_cents = v_price
        else:
            expected_cents = parse_price_cents(str(v_price))

        if expected_cents is None:
            return {"status": "WARN", "summary": f"Invalid expected price format: {v_price}"}

        # Search STRICTLY within main product form / section (no global fallbacks!)
        form_loc = main_form if await main_form.count() > 0 else None
        if not form_loc:
            return {
                "status": "WARN",
                "summary": "Main product form missing for price verification",
            }

        price_loc = form_loc.locator(
            '.price-item--regular:visible, [data-product-price]:visible, .price:not(.compare-at-price):visible, .product-single__price:visible'
        ).first

        if await price_loc.count() == 0:
            return {
                "status": "WARN",
                "summary": "Could not locate PDP price element within main product form",
            }

        price_text = await price_loc.inner_text()
        parsed_cents = parse_price_cents(price_text)

        if parsed_cents is None:
            return {
                "status": "WARN",
                "summary": f"Could not parse displayed price text '{price_text}'",
            }

        if parsed_cents == expected_cents:
            return {
                "status": "PASS",
                "summary": f"Displayed PDP price ({parsed_cents/100:.2f}) matches expected variant price ({expected_cents/100:.2f})",
                "displayed_cents": parsed_cents,
                "expected_cents": expected_cents,
            }

        return {
            "status": "FAIL",
            "summary": f"Displayed PDP price ({parsed_cents/100:.2f}) does not match expected variant price ({expected_cents/100:.2f})",
            "displayed_cents": parsed_cents,
            "expected_cents": expected_cents,
            "price_text": price_text,
        }

    async def _check_variant_image(
        self,
        page,
        main_form,
        expected_variant: dict[str, Any],
        product_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Check 3: Variant Image Verification."""
        featured_image = expected_variant.get("featured_image") or expected_variant.get("featured_media")
        expected_img_src = None

        if isinstance(featured_image, dict):
            expected_img_src = featured_image.get("src")
        elif isinstance(featured_image, str):
            expected_img_src = featured_image

        if not expected_img_src:
            return {
                "status": "NOT_APPLICABLE",
                "summary": "No distinct variant image expected for this variant",
            }

        form_loc = main_form if await main_form.count() > 0 else page
        img_loc = form_loc.locator(
            '.product-single__photo img:visible, [data-product-featured-media] img:visible, img.product-featured-media:visible, section img[src*="cdn.shopify.com"]:visible, img[src*="products"]:visible'
        ).first

        if await img_loc.count() == 0:
            img_loc = page.locator('img[src*="products"]:visible, img.featured-image:visible').first

        if await img_loc.count() == 0:
            return {
                "status": "WARN",
                "summary": "Main product image element not found in DOM",
            }

        try:
            img_props = await img_loc.evaluate("""
                el => ({
                    naturalWidth: el.naturalWidth,
                    naturalHeight: el.naturalHeight,
                    src: el.src,
                    currentSrc: el.currentSrc
                })
            """)
        except Exception as e:
            return {
                "status": "WARN",
                "summary": f"Failed to evaluate image attributes: {e}",
            }

        natural_width = img_props.get("naturalWidth", 0)
        active_src = img_props.get("currentSrc") or img_props.get("src") or ""

        if natural_width == 0:
            return {
                "status": "FAIL",
                "summary": "Main variant image is broken (naturalWidth == 0)",
                "active_src": active_src,
            }

        expected_filename = unquote(urlparse(expected_img_src).path).split("/")[-1].split("?")[0]
        active_filename = unquote(urlparse(active_src).path).split("/")[-1].split("?")[0]

        if expected_filename.lower() in active_src.lower() or active_filename.lower() in expected_img_src.lower():
            return {
                "status": "PASS",
                "summary": f"Active main image is loaded (naturalWidth={natural_width}) and matches variant image",
                "active_src": active_src,
                "expected_src": expected_img_src,
            }

        return {
            "status": "FAIL",
            "summary": "Active main product image does not match expected variant image",
            "active_src": active_src,
            "expected_src": expected_img_src,
        }

    async def _check_variant_availability(
        self,
        page,
        main_form,
        variants_list: list[dict[str, Any]],
        options_list: list[Any],
    ) -> dict[str, Any]:
        """Check 4: Variant Availability Display Verification."""
        unavailable_variant = None
        for v in variants_list:
            if not v.get("available", True):
                unavailable_variant = v
                break

        if not unavailable_variant:
            return {
                "status": "NOT_APPLICABLE",
                "summary": "No unavailable variants exist in product data",
            }

        add_clicked_request = {"fired": False}

        def on_add_request(req):
            if "/cart/add" in req.url and req.method.upper() == "POST":
                add_clicked_request["fired"] = True

        page.on("request", on_add_request)

        # Select unavailable variant options in UI using Playwright locator actions
        selected_ok = await self._select_variant_in_dom(page, main_form, unavailable_variant, options_list)
        await asyncio.sleep(1)

        # Verify selected options in DOM match unavailable_variant
        selected_options, complete_options = await self._get_selected_options_from_dom(page, main_form, options_list)
        matched_unavail = self._match_options_to_variant(selected_options, variants_list, options_list)

        if not selected_ok or not complete_options or matched_unavail != unavailable_variant:
            page.remove_listener("request", on_add_request)
            return {
                "status": "WARN",
                "summary": "Could not select complete options for unavailable variant in DOM",
            }

        form_loc = main_form if await main_form.count() > 0 else page
        atc_button = form_loc.locator('button[name="add"], button[type="submit"]:has-text("add"), button[type="submit"]:has-text("sold"), button.add-to-cart, [data-add-to-cart]').first

        if await atc_button.count() == 0:
            atc_button = page.locator('button[name="add"], button[type="submit"]').first

        if await atc_button.count() == 0:
            page.remove_listener("request", on_add_request)
            return {
                "status": "WARN",
                "summary": "Add to cart button not found after selecting unavailable variant",
            }

        btn_text = (await atc_button.inner_text()).upper()
        is_disabled = not (await atc_button.is_enabled())

        page.remove_listener("request", on_add_request)

        if add_clicked_request["fired"]:
            return {
                "status": "FAIL",
                "summary": "An add-to-cart request was incorrectly triggered for unavailable variant",
            }

        if is_disabled or any(term in btn_text for term in ("SOLD OUT", "OUT OF STOCK", "UNAVAILABLE")):
            return {
                "status": "PASS",
                "summary": "Unavailable variant correctly displayed (button disabled or Sold Out)",
                "button_text": btn_text,
                "is_disabled": is_disabled,
            }

        return {
            "status": "FAIL",
            "summary": "Unavailable variant remains enabled with Add to Cart text",
            "button_text": btn_text,
            "is_disabled": is_disabled,
        }
