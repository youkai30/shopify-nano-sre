"""Real Playwright shopper journey against an isolated local storefront."""

import asyncio
import json
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.async_api import async_playwright

from nano_sre.skills.shopify_shopper import ShopifyShopper


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("api_mode, expected_evidence, expected_trigger", [
    ("valid", "nonempty", "auto_open_drawer"),
    ("http_error", "failed", "auto_open_drawer"),
    ("api_only", "nonempty", "api_only_fallback"),
])
async def test_real_browser_cart_journey_and_shared_session(api_mode, expected_evidence, expected_trigger):
    observed = {"cart_cookie": None, "drawer_shown": False, "added": False}

    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, status, body, content_type="text/html; charset=utf-8", cookie=False):
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            if cookie:
                self.send_header("Set-Cookie", "fixture_cart_session=only-local; Path=/; SameSite=Lax")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path.startswith("/products/item"):
                body = """<!doctype html>
                  <form class="prd-ProductOffers_Form">
                    <input type="hidden" name="id" value="4" />
                    <input type="hidden" name="quantity" value="1" />
                    <button type="submit" name="add" class="prd-ProductOffers_Submit">Add to cart</button>
                  </form>
                  <script>window.Shopify={routes:{root:'/'}};
                  window.ShopifyAnalytics={meta:{product:{id:8,handle:'item'},selectedVariantId:4}};
                  document.querySelector('form').onsubmit=async(e)=>{
                    e.preventDefault();
                    await fetch('/cart/add.js',{method:'POST',body:'fixture'});
                    if (AUTO_DRAWER) {
                      document.body.insertAdjacentHTML('beforeend',
                        `<div role="dialog" aria-modal="true"><h2>CART (1 ITEM)</h2><button name="checkout">CHECKOUT</button></div>`);
                      document.querySelector('button[name="checkout"]').onclick=()=>
                        window.location.assign('/checkouts/fixture-session-secret?session=secret');
                    }
                  };</script>"""
                self.send(200, body.replace("AUTO_DRAWER", str(api_mode != "api_only").lower()), cookie=True)
            elif self.path == "/cart.js":
                observed["cart_cookie"] = self.headers.get("Cookie")
                if api_mode == "http_error":
                    self.send(503, "unavailable", "text/plain")
                else:
                    items = []
                    if observed["added"]:
                        items = [{
                            "key": "fixture-line", "variant_id": 4, "product_id": 8,
                            "product_title": "Fixture product", "variant_title": "Default",
                            "quantity": 1, "handle": "item", "properties": {"private": "drop"},
                        }]
                    payload = {"item_count": len(items), "items": items}
                    self.send(200, json.dumps(payload), "application/json")
            elif self.path.startswith("/checkouts/fixture"):
                self.send(200, "checkout fixture")
            elif self.path == "/cart":
                self.send(200, "<button name='checkout' onclick=\"location.href='/checkouts/fixture-session-secret?session=secret'\">CHECKOUT</button>")
            else:
                self.send(200, "fixture")

        def do_POST(self):
            if self.path == "/cart/add.js":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                observed["added"] = True
                self.send(200, "{}", "application/json")
            else:
                self.send(404, "missing", "text/plain")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Storefront)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    browser = None
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            result = await ShopifyShopper().run({"page": page, "base_url": origin + "/products/item"})
            assert result.details["cart_evidence_meta"]["status"] == expected_evidence
            assert result.details["cart_evidence_meta"]["trigger"] == expected_trigger
            assert result.details["cart_evidence_meta"]["source"] == "api"
            assert observed["cart_cookie"] and "fixture_cart_session=only-local" in observed["cart_cookie"]
            if api_mode == "valid":
                assert result.details["cart_evidence"][0]["quantity"] == 1
                assert "properties" not in result.details["cart_evidence"][0]
                assert "session=secret" not in result.details["checkout_url"]
                assert "fixture-session-secret" not in result.details["checkout_url"]
                assert result.details["checkout_url"].endswith("/checkouts/[redacted]")
                assert result.status == "PASS"
            await context.close()
    finally:
        if browser:
            await browser.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario, expected_status", [
    ("empty_then_valid", "PASS"),
    ("pre_existing_no_addition", "WARN"),
    ("http_200_no_change", "WARN"),
    ("different_variant_added", "WARN"),
    ("quantity_increased", "PASS"),
    ("delayed_cart_update", "PASS"),
    ("unreadable_cart", "WARN"),
    ("invalid_form_quantity", "WARN"),
    ("baseline_failed_then_ok", "WARN"),
    ("quantity_3_increased", "PASS"),
    ("two_forms_data_bound_to_clicked", "PASS"),
    ("unbound_button_analytics_present", "WARN"),
    ("html5_form_attr_binding", "PASS"),
    ("stalled_polling_timeout", "WARN"),
])
async def test_phase1_seven_cart_evidence_scenarios(scenario, expected_status):
    state = {
        "cart": [],
        "add_called": False,
        "delay_count": 0,
    }

    state["click_count"] = 0

    if scenario == "pre_existing_no_addition":
        state["cart"] = [{
            "key": "old-line", "variant_id": 101, "product_id": 1,
            "product_title": "Old item", "variant_title": "Default",
            "quantity": 1, "handle": "old-item",
        }]
    elif scenario in ("quantity_increased", "quantity_3_increased"):
        state["cart"] = [{
            "key": "target-line", "variant_id": 4, "product_id": 8,
            "product_title": "Target item", "variant_title": "Default",
            "quantity": 1, "handle": "item",
        }]

    class SevenScenariosStorefront(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, status, body, content_type="text/html; charset=utf-8"):
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path.startswith("/products/item"):
                if scenario == "two_forms_data_bound_to_clicked":
                    body = """<!doctype html>
                      <form id="form-unclicked" class="other-form">
                        <input type="hidden" name="id" value="999" />
                        <input type="hidden" name="quantity" value="10" />
                        <button type="button">OTHER FORM BUTTON</button>
                      </form>
                      <form id="form-target" class="prd-ProductOffers_Form">
                        <input type="hidden" name="id" value="4" />
                        <input type="hidden" name="quantity" value="1" />
                        <button type="submit" name="add" class="prd-ProductOffers_Submit">ADD TO CART</button>
                      </form>
                      <script>
                        window.Shopify={routes:{root:'/'}};
                        document.querySelector('#form-target').onsubmit=async(e)=>{
                          e.preventDefault();
                          await fetch('/cart/add.js',{method:'POST',body:'add'});
                        };
                      </script>"""
                elif scenario == "unbound_button_analytics_present":
                    body = """<!doctype html>
                      <form id="unclicked-product-form" class="prd-ProductOffers_Form">
                        <input type="hidden" name="id" value="999" />
                        <input type="hidden" name="quantity" value="1" />
                      </form>
                      <div>
                        <button type="button" class="add-to-cart">ADD TO CART UNBOUND</button>
                      </div>
                      <script>
                        window.Shopify={routes:{root:'/'}};
                        window.ShopifyAnalytics={meta:{product:{id:8,handle:'item'},selectedVariantId:4}};
                        document.querySelector('.add-to-cart').onclick=async(e)=>{
                          e.preventDefault();
                          await fetch('/cart/add.js',{method:'POST',body:'add'});
                        };
                      </script>"""
                elif scenario == "html5_form_attr_binding":
                    body = """<!doctype html>
                      <form id="html5-target-form">
                        <input type="hidden" name="id" value="4" />
                        <input type="hidden" name="quantity" value="1" />
                      </form>
                      <div>
                        <button type="submit" form="html5-target-form" class="add-to-cart">ADD TO CART VIA FORM ATTR</button>
                      </div>
                      <script>
                        window.Shopify={routes:{root:'/'}};
                        document.querySelector('button').onclick=async(e)=>{
                          e.preventDefault();
                          await fetch('/cart/add.js',{method:'POST',body:'add'});
                        };
                      </script>"""
                elif scenario == "stalled_polling_timeout":
                    body = """<!doctype html>
                      <form class="prd-ProductOffers_Form">
                        <input type="hidden" name="id" value="4" />
                        <input type="hidden" name="quantity" value="1" />
                        <button type="submit" name="add" class="prd-ProductOffers_Submit">ADD TO CART</button>
                      </form>
                      <script>
                        window.Shopify={routes:{root:'/'}};
                        document.querySelector('form').onsubmit=async(e)=>{
                          e.preventDefault();
                          await fetch('/cart/add.js',{method:'POST',body:'add'});
                        };
                      </script>"""
                else:
                    qty_val = "invalid_qty" if scenario == "invalid_form_quantity" else ("3" if scenario == "quantity_3_increased" else "1")
                    body = f"""<!doctype html>
                      <form class="prd-ProductOffers_Form">
                        <input type="hidden" name="id" value="4" />
                        <input type="hidden" name="quantity" value="{qty_val}" />
                        <button type="submit" name="add" class="prd-ProductOffers_Submit">ADD TO CART</button>
                      </form>
                      <script>
                        window.Shopify={{routes:{{root:'/'}}}};
                        window.ShopifyAnalytics={{meta:{{product:{{id:8,handle:'item'}},selectedVariantId:4}}}};
                        document.querySelector('form').onsubmit=async(e)=>{{
                          e.preventDefault();
                          await fetch('/cart/add.js',{{method:'POST',body:'add'}});
                        }};
                      </script>"""
                self.send(200, body)

            elif self.path == "/cart.js":
                if scenario == "unreadable_cart":
                    self.send(500, "Internal Error", "text/plain")
                    return

                if scenario == "stalled_polling_timeout" and state["add_called"]:
                    # Stall for 6 seconds to trigger polling timeout
                    time.sleep(6)
                    self.send(500, "Stalled Timeout", "text/plain")
                    return

                if scenario == "baseline_failed_then_ok" and not state["add_called"]:
                    self.send(500, "Baseline Error", "text/plain")
                    return

                items = list(state["cart"])
                if scenario == "delayed_cart_update" and state["add_called"]:
                    state["delay_count"] += 1
                    if state["delay_count"] < 2:
                        items = []

                total = sum(i["quantity"] for i in items)
                payload = {"item_count": total, "items": items}
                self.send(200, json.dumps(payload), "application/json")

            elif self.path == "/cart":
                self.send(200, "<button name='checkout' onclick=\"location.href='/checkouts/123'\">CHECKOUT</button>")

            elif self.path.startswith("/checkouts/"):
                self.send(200, "checkout page")

            else:
                self.send(200, "ok")

        def do_POST(self):
            if self.path == "/cart/add.js":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                state["add_called"] = True
                state["click_count"] += 1

                if scenario in ("empty_then_valid", "delayed_cart_update", "baseline_failed_then_ok", "invalid_form_quantity", "two_forms_data_bound_to_clicked", "unbound_button_analytics_present", "html5_form_attr_binding", "stalled_polling_timeout"):
                    state["cart"] = [{
                        "key": "target-line", "variant_id": 4, "product_id": 8,
                        "product_title": "Target item", "variant_title": "Default",
                        "quantity": 1, "handle": "item",
                    }]
                elif scenario == "quantity_increased":
                    state["cart"] = [{
                        "key": "target-line", "variant_id": 4, "product_id": 8,
                        "product_title": "Target item", "variant_title": "Default",
                        "quantity": 2, "handle": "item",
                    }]
                elif scenario == "quantity_3_increased":
                    state["cart"] = [{
                        "key": "target-line", "variant_id": 4, "product_id": 8,
                        "product_title": "Target item", "variant_title": "Default",
                        "quantity": 4, "handle": "item",
                    }]
                elif scenario == "different_variant_added":
                    state["cart"] = [{
                        "key": "other-line", "variant_id": 999, "product_id": 8,
                        "product_title": "Target item", "variant_title": "Other",
                        "quantity": 1, "handle": "item",
                    }]
                # For pre_existing_no_addition and http_200_no_change, state["cart"] is unchanged.

                self.send(200, "{}", "application/json")
            else:
                self.send(404, "not found")

    server = ThreadingHTTPServer(("127.0.0.1", 0), SevenScenariosStorefront)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    browser = None
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            t0 = asyncio.get_event_loop().time()
            result = await ShopifyShopper().run({"page": page, "base_url": origin + "/products/item", "cart_evidence_only": True})
            duration = asyncio.get_event_loop().time() - t0
            assert result.status == expected_status, f"Scenario '{scenario}' expected {expected_status}, got {result.status}"
            assert state["click_count"] == 1, f"Expected exactly 1 Add to Cart click, got {state['click_count']}"
            if scenario == "stalled_polling_timeout":
                assert 5.0 <= duration <= 8.0, f"Expected polling duration between 5.0s and 8.0s, got {duration:.2f}s"
            await context.close()
    finally:
        if browser:
            await browser.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
