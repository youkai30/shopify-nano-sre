#!/usr/bin/env python3
"""Phase 5 CLI Verification Script.

Executes real CLI sub-processes using `subprocess.run` with --skill shopify_cart_consistency_auditor
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

            if path in ["/products/pass-demo.js", "/products/warn-demo.js", "/products/fail-demo.js"]:
                handle = path.split("/")[2].replace(".js", "")
                data = {
                    "id": 100,
                    "title": f"Product {handle}",
                    "handle": handle,
                    "options": [],
                    "variants": [
                        {"id": 1001, "title": "Default", "price": 2500, "available": True, "options": []},
                        {"id": 2002, "title": "Wrong", "price": 2500, "available": True, "options": []},
                    ],
                }
                self.send(200, json.dumps(data), "application/json")

            elif path == "/cart.js":
                cookie_hdr = self.headers.get("Cookie", "")
                session_id = "default"
                if "cart_session=" in cookie_hdr:
                    session_id = cookie_hdr.split("cart_session=")[1].split(";")[0]
                c = carts.get(session_id, [])
                self.send(200, json.dumps({"item_count": sum(i["quantity"] for i in c), "currency": "USD", "items": c}), "application/json")

            elif path.startswith("/products/pass-demo"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <span class="price">$25.00 USD</span>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <div class="cart-drawer" style="display:block;">
                  <div class="cart-item">
                    <span class="title">Product pass-demo - Default</span>
                    <input class="quantity" value="1">
                    <span class="price">$25.00 USD</span>
                  </div>
                </div>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/warn-demo"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=1001&quantity=1'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")

            elif path.startswith("/products/fail-demo"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=2002&quantity=1'});
                };
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
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else ""

                headers = [("Set-Cookie", f"cart_session={session_id}; Path=/")]
                if "id=1001" in body:
                    carts[session_id].append({"variant_id": 1001, "product_id": 100, "quantity": 1, "price": 2500, "final_price": 2500})
                    self.send_with_headers(200, json.dumps({"id": 1001, "quantity": 1}), "application/json", headers)
                elif "id=2002" in body:
                    carts[session_id].append({"variant_id": 2002, "product_id": 100, "quantity": 1, "price": 2500, "final_price": 2500})
                    self.send_with_headers(200, json.dumps({"id": 2002, "quantity": 1}), "application/json", headers)
                else:
                    self.send_with_headers(400, json.dumps({"error": "bad request"}), "application/json", headers)

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
    run_dir = Path("verification_output/phase5_run_" + str(int(time.time())))
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running Phase 5 Subprocess CLI Verification against {base_url}...")

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
            "shopify_cart_consistency_auditor",
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

    print(f"\nPhase 5 Subprocess CLI verification completed successfully! All expected statuses matched. Artifacts saved to: {run_dir}")


if __name__ == "__main__":
    main()
