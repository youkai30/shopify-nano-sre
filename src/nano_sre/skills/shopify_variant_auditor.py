"""Shopify Variant Auditor Skill - Audits variant selection, pricing, images, and availability."""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional, Tuple
from urllib.parse import unquote, urljoin, urlparse

from nano_sre.agent.core import Skill, SkillResult

logger = logging.getLogger(__name__)


def parse_price_amount(text: str) -> Optional[float]:
    """Parse a price string into a float amount safely without naive comma stripping."""
    if not text or not isinstance(text, str):
        return None

    # Find the numeric price string in text (e.g. $1,500.00 -> 1,500.00)
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
        if len(parts) == 2 and len(parts[1]) in (1, 2):  # e.g. 15.99
            clean = num_str
        elif len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):  # e.g. 1.500
            clean = num_str.replace(".", "")
        else:
            clean = num_str
    else:
        clean = num_str

    try:
        val = float(clean)
        return round(val, 2)
    except ValueError:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            # 1. Discover Product URL & Handle
            current_url = page.url or base_url
            handle = _extract_handle_from_url(current_url)

            if not handle and "/products/" not in current_url.lower():
                # Navigate to homepage / products.json to discover a product
                await page.goto(base_url, wait_until="commit", timeout=60000)
                await asyncio.sleep(1)

                # Try finding product links
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
                            break

            if not handle:
                # Try products.json fallback
                try:
                    res = await page.request.get(f"{base_url.rstrip('/')}/products.json?limit=5")
                    if res.ok:
                        data = await res.json()
                        products = data.get("products", [])
                        if products:
                            handle = products[0].get("handle")
                            product_url = f"{base_url.rstrip('/')}/products/{handle}"
                            await page.goto(product_url, wait_until="commit", timeout=60000)
                            await asyncio.sleep(1)
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

            # 2. Fetch Ajax Product API JSON: GET /{locale}/products/{handle}.js
            current_origin = f"{urlparse(page.url).scheme}://{urlparse(page.url).netloc}"
            ajax_url = f"{current_origin}/products/{handle}.js"

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
                # Fallback to main form or section
                main_form = page.locator('form:has(button[name="add"]), section[data-product-single-media-group]').first

            # Ensure page hydration / Alpine / theme JS is ready
            await asyncio.sleep(1)

            # Execute 4 Checks
            # -------------------------------------------------------------
            # CHECK 1 & 2: Option Selection & Target Variant Discovery
            # -------------------------------------------------------------
            selected_options, complete_options = await self._get_selected_options_from_dom(page, main_form, options_list)

            # Check for ambiguous pricing context
            is_ambiguous_pricing = await self._check_pricing_ambiguity(page, main_form)

            # Select a non-default variant if available, or test default variant
            target_variant = None
            if len(variants_list) > 1:
                # Try finding a non-default available variant
                for v in variants_list[1:]:
                    if v.get("available", True):
                        target_variant = v
                        break
            if not target_variant:
                target_variant = variants_list[0]

            # If we need to select options in UI for target_variant:
            if len(variants_list) > 1 and target_variant != variants_list[0]:
                await self._select_variant_in_dom(page, main_form, target_variant, options_list)
                await asyncio.sleep(1)  # Allow async UI updates
                selected_options, complete_options = await self._get_selected_options_from_dom(page, main_form, options_list)

            # Map selected options to product variant
            matched_variant = self._match_options_to_variant(selected_options, variants_list, options_list)

            # If matched_variant exists, use it as expected_variant
            expected_variant = matched_variant or target_variant

            # -------------------------------------------------------------
            # CHECK 1: Variant Identity Check
            # -------------------------------------------------------------
            checks["variant_identity"] = await self._check_variant_identity(
                page=page,
                main_form=main_form,
                expected_variant=expected_variant,
                complete_options=complete_options,
                current_origin=current_origin,
            )

            # -------------------------------------------------------------
            # CHECK 2: Variant Price Check
            # -------------------------------------------------------------
            checks["variant_price"] = await self._check_variant_price(
                page=page,
                main_form=main_form,
                expected_variant=expected_variant,
                is_ambiguous_pricing=is_ambiguous_pricing,
            )

            # -------------------------------------------------------------
            # CHECK 3: Variant Image Check
            # -------------------------------------------------------------
            checks["variant_image"] = await self._check_variant_image(
                page=page,
                main_form=main_form,
                expected_variant=expected_variant,
                product_data=product_data,
            )

            # -------------------------------------------------------------
            # CHECK 4: Variant Availability Display Check
            # -------------------------------------------------------------
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

        # Normalize options_list to names
        option_names = []
        for opt in options_list:
            if isinstance(opt, dict):
                option_names.append(opt.get("name", ""))
            elif isinstance(opt, str):
                option_names.append(opt)

        form_loc = main_form if await main_form.count() > 0 else page

        # 1. Check <select> elements
        selects = form_loc.locator("select")
        select_count = await selects.count()
        for i in range(select_count):
            sel = selects.nth(i)
            if not await sel.is_visible():
                continue
            val = await sel.evaluate("el => el.value")
            name_attr = (await sel.get_attribute("name") or "").lower()
            id_attr = (await sel.get_attribute("id") or "").lower()

            # Try matching to option name
            for opt_name in option_names:
                if opt_name.lower() in name_attr or opt_name.lower() in id_attr or f"option{option_names.index(opt_name)+1}" in name_attr or f"option{option_names.index(opt_name)+1}" in id_attr:
                    selected_options[opt_name] = val

        # 2. Check checked radio buttons / swatches
        radios = form_loc.locator('input[type="radio"]:checked')
        radio_count = await radios.count()
        for i in range(radio_count):
            r = radios.nth(i)
            val = await r.evaluate("el => el.value")
            name_attr = (await r.get_attribute("name") or "").lower()
            for opt_name in option_names:
                if opt_name.lower() in name_attr or f"option{option_names.index(opt_name)+1}" in name_attr:
                    selected_options[opt_name] = val

        # 3. Check active swatch buttons / elements
        swatches = form_loc.locator('.swatch.selected, [data-option-value].active, button.selected, [aria-checked="true"]')
        swatch_count = await swatches.count()
        for i in range(swatch_count):
            sw = swatches.nth(i)
            val = await sw.get_attribute("data-value") or await sw.get_attribute("data-option-value") or await sw.inner_text()
            val = val.strip() if val else ""
            opt_name_attr = await sw.get_attribute("data-option-name") or ""
            if opt_name_attr and val:
                selected_options[opt_name_attr] = val

        # Complete options check: whether all required option names are mapped
        complete = len(option_names) > 0 and all(name in selected_options for name in option_names)
        return selected_options, complete

    async def _select_variant_in_dom(self, page, main_form, target_variant: dict[str, Any], options_list: list[Any]) -> None:
        """Click or change DOM controls to select target_variant options."""
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

            # 1. Try select dropdown
            select_loc = form_loc.locator(
                f'select[name*="{opt_name}" i], select[name*="option{idx+1}" i], select[id*="{opt_name}" i], select'
            )
            select_count = await select_loc.count()
            selected = False
            for i in range(select_count):
                sel = select_loc.nth(i)
                if not await sel.is_visible():
                    continue
                try:
                    changed = await sel.evaluate("""(el, targetVal) => {
                        for (let opt of el.options) {
                            if (opt.value.toLowerCase() === targetVal.toLowerCase() || opt.text.trim().toLowerCase() === targetVal.toLowerCase()) {
                                el.value = opt.value;
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                return true;
                            }
                        }
                        return false;
                    }""", val)
                    if changed:
                        selected = True
                        break
                except Exception:
                    pass

            if selected:
                continue

            # 2. Try radio button or swatch button/label
            btn_loc = form_loc.locator(
                f'input[value="{val}" i], button:has-text("{val}"), [data-value="{val}"], label:has-text("{val}")'
            ).first
            if await btn_loc.count() > 0 and await btn_loc.is_visible():
                try:
                    await btn_loc.click(timeout=2000)
                except Exception:
                    pass

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
        """Check if PDP pricing context is ambiguous (e.g. multiple disparate prices displayed)."""
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

    async def _fetch_cart_snapshot(self, page, current_origin: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Fetch safe cart.js snapshot."""
        meta = {
            "status": "failed",
            "captured_at": _now(),
            "item_count": 0,
        }
        items = []

        try:
            res = await page.request.get(f"{current_origin}/cart.js", timeout=10000)
            if res.ok:
                data = await res.json()
                item_count = data.get("item_count", 0)
                meta["status"] = "empty" if item_count == 0 else "nonempty"
                meta["item_count"] = item_count
                for item in data.get("items", []):
                    items.append({
                        "variant_id": item.get("variant_id"),
                        "product_id": item.get("product_id"),
                        "quantity": item.get("quantity", 0),
                        "selling_plan_id": item.get("selling_plan_id"),
                    })
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
        current_origin: str,
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
        meta_before, items_before = await self._fetch_cart_snapshot(page, current_origin)
        qty_before = sum(item["quantity"] for item in items_before if item["variant_id"] == expected_variant_id)

        # Intercept ATC network request
        add_request_data = {"captured": False, "variant_id": None, "quantity": None, "status": None}

        def on_request(request):
            if "/cart/add" in request.url:
                add_request_data["captured"] = True
                # Strictly capture allowed fields ONLY (NO cookies, headers, tokens or raw body)
                try:
                    post_data = request.post_data
                    if post_data:
                        match_id = re.search(r'(?:id|variant_id)=([^&]+)', post_data)
                        if match_id:
                            add_request_data["variant_id"] = int(match_id.group(1)) if match_id.group(1).isdigit() else match_id.group(1)
                except Exception:
                    pass

        def on_response(response):
            if "/cart/add" in response.url:
                add_request_data["status"] = response.status

        page.on("request", on_request)
        page.on("response", on_response)

        # Click Add to Cart Button
        atc_button = main_form.locator('button[name="add"], button.add-to-cart, button:has-text("ADD TO CART"), button:has-text("ADD TO BAG")').first
        if await atc_button.count() == 0:
            atc_button = page.locator('button[name="add"], button:has-text("ADD TO CART")').first

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
            await asyncio.sleep(2)  # Wait for request & cart update
        except Exception as e:
            logger.debug("Click ATC failed: %s", e)

        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)

        # Take Cart Snapshot 2 (After ATC)
        meta_after, items_after = await self._fetch_cart_snapshot(page, current_origin)

        if meta_after["status"] in ("request_error", "failed") or meta_after["status"].startswith("http_"):
            return {
                "status": "WARN",
                "summary": "Could not read cart after Add to Cart",
                "meta_before": meta_before,
                "meta_after": meta_after,
            }

        qty_after = sum(item["quantity"] for item in items_after if item["variant_id"] == expected_variant_id)
        delta_quantity = qty_after - qty_before

        # Check for added variant ID in request/cart
        added_variant_id = add_request_data.get("variant_id")
        if not added_variant_id and items_after:
            # If request body didn't yield ID, inspect newly added items in cart
            added_variant_id = expected_variant_id if delta_quantity > 0 else None

        # Validation
        if delta_quantity <= 0:
            return {
                "status": "FAIL",
                "summary": f"Add to Cart failed: quantity did not increase (delta_quantity={delta_quantity})",
                "expected_variant_id": expected_variant_id,
                "delta_quantity": delta_quantity,
                "meta_before": meta_before,
                "meta_after": meta_after,
            }

        if added_variant_id and str(added_variant_id) != str(expected_variant_id):
            return {
                "status": "FAIL",
                "summary": f"Added wrong variant ID {added_variant_id} instead of expected {expected_variant_id}",
                "expected_variant_id": expected_variant_id,
                "added_variant_id": added_variant_id,
                "delta_quantity": delta_quantity,
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
        """Check 2: Variant Price Verification."""
        if is_ambiguous_pricing:
            return {
                "status": "WARN",
                "summary": "Unresolved or ambiguous pricing context on PDP",
            }

        # Determine expected price amount
        v_price = expected_variant.get("price")
        if v_price is None:
            return {"status": "WARN", "summary": "Expected variant price missing in product JSON"}

        if isinstance(v_price, int):
            # Cents to float (e.g. 1599 -> 15.99, 150000 -> 1500.00)
            expected_amount = round(v_price / 100.0, 2)
        else:
            try:
                expected_amount = round(float(v_price), 2)
            except ValueError:
                return {"status": "WARN", "summary": f"Invalid expected price format: {v_price}"}

        # Strictly locate price inside main PDP form / section (excluding recommendations & compare-at)
        form_loc = main_form if await main_form.count() > 0 else page
        price_loc = form_loc.locator(
            '.price-item--regular:visible, [data-product-price]:visible, .price:not(.compare-at-price):visible, .product-single__price:visible'
        ).first

        # Ensure we avoid recommendations cards
        if await price_loc.count() == 0:
            price_loc = page.locator('.product-single__price:visible, .product__price:visible, span.price:visible').first

        if await price_loc.count() == 0:
            return {
                "status": "WARN",
                "summary": "Could not locate main PDP price element",
            }

        price_text = await price_loc.inner_text()
        parsed_price = parse_price_amount(price_text)

        if parsed_price is None:
            return {
                "status": "WARN",
                "summary": f"Could not parse displayed price text '{price_text}'",
            }

        if abs(parsed_price - expected_amount) < 0.01:
            return {
                "status": "PASS",
                "summary": f"Displayed PDP price {parsed_price} matches expected variant price {expected_amount}",
                "displayed_price": parsed_price,
                "expected_price": expected_amount,
            }

        return {
            "status": "FAIL",
            "summary": f"Displayed PDP price {parsed_price} does not match expected variant price {expected_amount}",
            "displayed_price": parsed_price,
            "expected_price": expected_amount,
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

        # Locate active main product image element
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

        # Verify active loaded main resource (naturalWidth > 0) and identity
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

        # Extract filename / base path to compare
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
        # Find an unavailable variant
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

        # Track network requests to verify NO add to cart request is fired
        add_clicked_request = {"fired": False}

        def on_add_request(req):
            if "/cart/add" in req.url:
                add_clicked_request["fired"] = True

        page.on("request", on_add_request)

        # Select unavailable variant in UI
        await self._select_variant_in_dom(page, main_form, unavailable_variant, options_list)
        await asyncio.sleep(1)  # Bounded state verification wait

        # Locate ATC button on page
        atc_button = None
        for loc in (
            main_form.locator('button[name="add"], button[type="submit"], button.add-to-cart, button') if await main_form.count() > 0 else None,
            page.locator('button[name="add"], button[type="submit"], button.add-to-cart, button'),
        ):
            if loc is not None and await loc.count() > 0:
                atc_button = loc.first
                break

        if not atc_button or await atc_button.count() == 0:
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
