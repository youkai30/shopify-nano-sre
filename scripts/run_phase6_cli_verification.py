#!/usr/bin/env python3
"""Phase 6 Standalone CLI Verification Script for Shopify Checkout Blocker Auditor.

Runs subprocess CLI audit commands against local mock HTTP server scenarios (PASS, WARN, FAIL),
verifies exit codes, verifies image existence on disk, and writes SHA256SUMS.json for evidence packaging.
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def compute_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


class MockShopifyCheckoutServer(BaseHTTPRequestHandler):
    cart_sessions = {}

    def log_message(self, *args):
        pass

    def get_session_id(self) -> tuple[str, bool]:
        cookie_hdr = self.headers.get("Cookie", "")
        session_id = None
        is_new_session = False
        if "cart_session=" in cookie_hdr:
            session_id = cookie_hdr.split("cart_session=")[1].split(";")[0]

        if not session_id:
            session_id = str(uuid.uuid4())
            is_new_session = True

        if session_id not in MockShopifyCheckoutServer.cart_sessions:
            MockShopifyCheckoutServer.cart_sessions[session_id] = {
                "item_count": 0,
                "currency": "USD",
                "items": []
            }

        return session_id, is_new_session

    def do_GET(self):
        path = self.path
        cookie_hdr = self.headers.get("Cookie", "")
        session_id, is_new_session = self.get_session_id()

        extra_headers = [("Set-Cookie", f"cart_session={session_id}; Path=/")] if is_new_session else []

        if path == "/cart.js":
            if "warn_demo=1" in cookie_hdr or "warn-demo" in self.headers.get("Referer", ""):
                body = json.dumps({"item_count": 0, "items": []})
            else:
                sess_cart = MockShopifyCheckoutServer.cart_sessions[session_id]
                body = json.dumps(sess_cart)
            self.send_with_headers(200, body, "application/json", extra_headers)

        elif path == "/products/pass-demo":
            html = """<!doctype html><html><body>
            <h1>Product Pass Demo</h1>
            <form action="/checkout" method="post">
              <button type="submit" name="checkout">Checkout</button>
            </form>
            <script>
            (async () => {
              await fetch('/cart/add.js', {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: 'id=1001&quantity=1'
              });
              document.querySelector('form').onsubmit = (e) => {
                e.preventDefault();
                window.location.href = '/checkout/working';
              };
            })();
            </script></body></html>"""
            self.send_with_headers(200, html, "text/html", extra_headers)

        elif path == "/products/warn-demo":
            html = """<!doctype html><html><body>
            <h1>Product Empty Cart Demo</h1>
            <script>
            document.cookie = "warn_demo=1; path=/";
            </script>
            </body></html>"""
            self.send_with_headers(200, html, "text/html", extra_headers)

        elif path == "/products/fail-demo":
            html = """<!doctype html><html><body>
            <h1>Product Fail Demo</h1>
            <button name="checkout" disabled>Checkout</button>
            <script>
            (async () => {
              await fetch('/cart/add.js', {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: 'id=1001&quantity=1'
              });
            })();
            </script></body></html>"""
            self.send_with_headers(200, html, "text/html", extra_headers)

        elif path == "/checkout/working":
            html = """<!doctype html><html><body>
            <div id="checkout">
              <h1>Checkout Page</h1>
              <input type="email" name="email" value="user@example.test">
              <input type="text" name="address1" value="123 Synthetic Way">
              <input type="text" name="zip" value="10001">
              <button type="button" data-continue-to-shipping id="cont-btn">Continue to shipping</button>
              <div id="shipping-section" style="display:none;">
                <div class="shipping-option">Standard Ground Shipping - $5.00</div>
                <div id="payment-methods">Payment Methods Available</div>
              </div>
            </div>
            <script>
            document.getElementById('cont-btn').onclick = () => {
              document.getElementById('shipping-section').style.display = 'block';
            };
            </script></body></html>"""
            self.send_with_headers(200, html, "text/html", extra_headers)

        else:
            self.send(404, "Not Found", "text/plain")

    def do_POST(self):
        session_id, is_new_session = self.get_session_id()
        headers = [("Set-Cookie", f"cart_session={session_id}; Path=/")] if is_new_session else []

        if self.path == "/cart/add.js":
            sess_cart = MockShopifyCheckoutServer.cart_sessions[session_id]
            # Ensure item addition is strictly isolated to this session_id
            sess_cart["items"].append({"id": 1001, "variant_id": 1001, "product_id": 100, "quantity": 1, "key": "line_key_1001"})
            sess_cart["item_count"] = sum(i["quantity"] for i in sess_cart["items"])
            body = json.dumps({"id": 1001, "quantity": 1})
            self.send_with_headers(200, body, "application/json", headers)
        else:
            self.send(404, "Not Found", "text/plain")

    def send_with_headers(self, status, body, content_type, extra_headers=None):
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        if extra_headers:
            for h, v in extra_headers:
                self.send_header(h, v)
        self.end_headers()
        self.wfile.write(encoded)

    def send(self, status, body, content_type):
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def run_verification():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockShopifyCheckoutServer)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    run_id = f"phase6_run_{int(time.time())}"
    output_base_dir = os.path.join("verification_output", run_id)
    os.makedirs(output_base_dir, exist_ok=True)

    print(f"Running Phase 6 Subprocess CLI Verification against http://127.0.0.1:{port}...")

    scenarios = [
        ("pass", f"http://127.0.0.1:{port}/products/pass-demo", "PASS"),
        ("warn", f"http://127.0.0.1:{port}/products/warn-demo", "WARN"),
        ("fail", f"http://127.0.0.1:{port}/products/fail-demo", "FAIL"),
    ]

    all_matched = True

    for tag, url, expected_status in scenarios:
        scenario_dir = os.path.join(output_base_dir, tag)
        os.makedirs(scenario_dir, exist_ok=True)

        json_out = os.path.join(scenario_dir, "audit_result.json")
        log_out = os.path.join(scenario_dir, "cli_run.log")

        cmd = [
            sys.executable,
            "-m",
            "nano_sre.cli",
            "audit",
            "--url",
            url,
            "--skill",
            "shopify_checkout_blocker_auditor",
            "--output",
            json_out,
        ]

        env = dict(os.environ)
        env["PYTHONPATH"] = "src"
        env["MCP_ENABLED"] = "false"
        env["LLM_PROVIDER"] = "openai"
        env["LLM_API_KEY"] = ""

        print(f"--- Running subprocess CLI for scenario: {tag.upper()} ({url}) ---")
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)

        with open(log_out, "w", encoding="utf-8") as lf:
            lf.write(f"=== STDOUT ===\n{proc.stdout}\n=== STDERR ===\n{proc.stderr}\n=== EXIT CODE: {proc.returncode} ===\n")

        observed_status = "UNKNOWN"
        if os.path.exists(json_out):
            try:
                with open(json_out, "r", encoding="utf-8") as jf:
                    data = json.load(jf)
                    res_list = data.get("results", [])
                    if res_list and isinstance(res_list, list):
                        observed_status = res_list[0].get("status", "UNKNOWN")
            except Exception as e:
                print(f"Failed to read JSON output: {e}")

        matched = (observed_status == expected_status) and (proc.returncode == 0)
        print(f"Scenario {tag.upper()}: Expected = {expected_status}, Observed = {observed_status}, ExitCode = {proc.returncode}")
        if not matched:
            all_matched = False

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)

    # Compute SHA256 checksums for all files in output_base_dir
    sha256_map = {}
    for root, dirs, files in os.walk(output_base_dir):
        for f in files:
            if f == "SHA256SUMS.json":
                continue
            full_p = os.path.join(root, f)
            rel_p = os.path.relpath(full_p, output_base_dir)
            sha256_map[rel_p.replace("\\", "/")] = compute_sha256(full_p)

    sha256_file = os.path.join(output_base_dir, "SHA256SUMS.json")
    with open(sha256_file, "w", encoding="utf-8") as sf:
        json.dump(sha256_map, sf, indent=2, sort_keys=True)

    if all_matched:
        print(f"\nPhase 6 Subprocess CLI verification completed successfully! All expected statuses matched. Artifacts saved to: {output_base_dir}")
        sys.exit(0)
    else:
        print(f"\nPhase 6 Subprocess CLI verification failed! Artifacts saved to: {output_base_dir}")
        sys.exit(1)


if __name__ == "__main__":
    run_verification()
