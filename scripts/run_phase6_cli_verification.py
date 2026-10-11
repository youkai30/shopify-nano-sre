#!/usr/bin/env python3
"""Phase 6 CLI Verification Script.

Executes real CLI sub-processes using `subprocess.run` with --skill shopify_checkout_blocker_auditor
against local HTTP servers producing PASS, WARN, and FAIL outcomes.
Captures exit codes, stdout/stderr, JSON, Markdown reports, screenshots, and SHA256 checksums.
Assesses expected status matches strictly.
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def start_mock_server():
    carts = {}

    class VerificationHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path

            if path == "/cart.js":
                cookie_hdr = self.headers.get("Cookie", "")
                session_id = "default"
                if "cart_session=" in cookie_hdr:
                    session_id = cookie_hdr.split("cart_session=")[1].split(";")[0]
                c = carts.get(session_id, [])
                self.send(200, json.dumps({"item_count": sum(i["quantity"] for i in c), "currency": "USD", "items": c}), "application/json")

            elif path.startswith("/checkout/pass-demo"):
                html = """<!doctype html><html><body>
                <div id="checkout">
                  <h1>Checkout</h1>
                  <input type="email" name="email" value="">
                  <input type="text" name="first_name" value="">
                  <input type="text" name="last_name" value="">
                  <input type="text" name="address1" value="">
                  <input type="text" name="city" value="">
                  <input type="text" name="zip" value="">
                  <div id="payment-methods">Payment Options</div>
                </div></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/pass-demo"):
                html = """<!doctype html><html><body>
                <form action="/checkout" method="post">
                  <button type="submit" name="checkout">Checkout</button>
                </form>
                <script>
                (async () => {
                  await fetch('/cart/add.js', {method: 'POST', body: 'id=1001&quantity=1'});
                })();
                document.querySelector('form').onsubmit = (e) => {
                  e.preventDefault();
                  window.location.href = '/checkout/pass-demo';
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/warn-demo"):
                html = """<!doctype html><html><body>
                <div>Cart Empty Page</div>
                </body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/fail-demo"):
                html = """<!doctype html><html><body>
                <button name="checkout" disabled>Checkout</button>
                <script>
                (async () => {
                  await fetch('/cart/add.js', {method: 'POST', body: 'id=1001&quantity=1'});
                })();
                </script></body></html>"""
                self.send(200, html, "text/html")

            else:
                self.send(404, "Not found", "text/plain")

        def do_POST(self):
            cookie_hdr = self.headers.get("Cookie", "")
            session_id = "default"
            if "cart_session=" in cookie_hdr:
                session_id = cookie_hdr.split("cart_session=")[1].split(";")[0]
            else:
                import uuid
                session_id = str(uuid.uuid4())

            if session_id not in carts:
                carts[session_id] = []

            if self.path == "/cart/add.js":
                headers = [("Set-Cookie", f"cart_session={session_id}; Path=/")]
                carts[session_id].append({"variant_id": 1001, "product_id": 100, "quantity": 1, "price": 2500, "final_price": 2500})
                self.send_with_headers(200, json.dumps({"id": 1001, "quantity": 1}), "application/json", headers)
            else:
                self.send(404, "Not found", "text/plain")

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

    server = ThreadingHTTPServer(("127.0.0.1", 0), VerificationHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def compute_dir_sha256(dir_path: Path) -> dict:
    hashes = {}
    for p in sorted(dir_path.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(dir_path))
            content = p.read_bytes()
            hashes[rel] = hashlib.sha256(content).hexdigest()
    return hashes


def main():
    server, base_url = start_mock_server()
    run_dir = Path("verification_output/phase6_run_" + str(int(time.time())))
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running Phase 6 Subprocess CLI Verification against {base_url}...")

    scenarios = [
        ("pass", f"{base_url}/products/pass-demo", "PASS"),
        ("warn", f"{base_url}/products/warn-demo", "WARN"),
        ("fail", f"{base_url}/products/fail-demo", "FAIL"),
    ]

    all_scenarios_passed = True

    for name, url, expected_status in scenarios:
        scen_dir = run_dir / name
        scen_dir.mkdir(parents=True, exist_ok=True)
        json_out = scen_dir / "audit_result.json"
        rep_dir = scen_dir / "reports"
        log_out = scen_dir / "cli_run.log"

        cmd = [
            sys.executable,
            "-m",
            "nano_sre.cli",
            "--report-dir",
            str(rep_dir),
            "audit",
            "--url",
            url,
            "--skill",
            "shopify_checkout_blocker_auditor",
            "--output",
            str(json_out),
        ]

        env = os.environ.copy()
        env["PYTHONPATH"] = "src"
        env["MCP_ENABLED"] = "false"
        env["LLM_PROVIDER"] = "openai"
        env["LLM_API_KEY"] = ""

        print(f"--- Running subprocess CLI for scenario: {name.upper()} ({url}) ---")
        proc = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        log_out.write_text(f"EXIT CODE: {proc.returncode}\n\nSTDOUT/STDERR:\n{proc.stdout}", encoding="utf-8")

        if proc.returncode != 0:
            print(f"FAILED scenario {name}: Subprocess exited with non-zero code {proc.returncode}")
            all_scenarios_passed = False
            continue

        if not json_out.exists():
            print(f"FAILED scenario {name}: Output JSON file missing!")
            all_scenarios_passed = False
            continue

        data = json.loads(json_out.read_text(encoding="utf-8"))
        results = data.get("results", [])
        if not results:
            print(f"FAILED scenario {name}: Empty results in JSON!")
            all_scenarios_passed = False
            continue

        observed_status = results[0].get("status")
        print(f"Scenario {name.upper()}: Expected = {expected_status}, Observed = {observed_status}, ExitCode = {proc.returncode}")

        if observed_status != expected_status:
            print(f"MISMATCH in scenario {name.upper()}!")
            all_scenarios_passed = False

    sha_map = compute_dir_sha256(run_dir)
    sha_file = run_dir / "SHA256SUMS.json"
    sha_file.write_text(json.dumps(sha_map, indent=2), encoding="utf-8")

    server.shutdown()
    server.server_close()

    if not all_scenarios_passed:
        print("\nCLI Verification FAILED due to mismatched scenario outcomes!")
        sys.exit(1)

    print(f"\nPhase 6 Subprocess CLI verification completed successfully! All expected statuses matched. Artifacts saved to: {run_dir}")


if __name__ == "__main__":
    main()
