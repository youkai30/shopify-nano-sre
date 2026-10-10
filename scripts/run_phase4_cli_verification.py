"""Phase 4 standalone CLI verification script for shopify_mobile_purchase_blocker_auditor.

Tests PASS, FAIL (mobile-specific), and WARN scenarios outside pytest using real Chromium and mobile viewports.
Asserts exit codes, skill name, status, reason_code, desktop comparison, reproduction, evidence, screenshot file existence, and privacy redaction.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    carts = {}
    active_scenario = ["pass"]

    class Phase4Storefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            # PASS scenario product
            if self.path == "/products/pass.js":
                data = {
                    "id": 1,
                    "handle": "pass",
                    "options": [],
                    "variants": [
                        {"id": 101, "title": "Default", "price": 1599, "available": True, "options": []}
                    ]
                }
                self.send(200, json.dumps(data), "application/json")
            # FAIL scenario product (blocking sticky overlay on mobile only)
            elif self.path == "/products/fail.js":
                data = {
                    "id": 2,
                    "handle": "fail",
                    "options": [],
                    "variants": [
                        {"id": 201, "title": "Default", "price": 1599, "available": True, "options": []}
                    ]
                }
                self.send(200, json.dumps(data), "application/json")
            # WARN scenario product (500 server error on cart add)
            elif self.path == "/products/warn.js":
                data = {
                    "id": 3,
                    "handle": "warn",
                    "options": [],
                    "variants": [
                        {"id": 301, "title": "Default", "price": 1599, "available": True, "options": []}
                    ]
                }
                self.send(200, json.dumps(data), "application/json")
            elif self.path == "/cart.js":
                cookie_hdr = self.headers.get("Cookie", "")
                session_id = "default"
                if "cart_session=" in cookie_hdr:
                    session_id = cookie_hdr.split("cart_session=")[1].split(";")[0]
                c = carts.get(session_id, [])
                self.send(200, json.dumps({"item_count": sum(i["quantity"] for i in c), "items": c}), "application/json")
            elif self.path.startswith("/products/pass"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=101'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")
            elif self.path.startswith("/products/fail"):
                # Blocking modal overlay on mobile (max-width 767px)
                html = """<!doctype html><html><head>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>
                  @media (max-width: 767px) {
                    .mobile-sticky-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(0,0,0,0.8); z-index: 9999; }
                  }
                  @media (min-width: 768px) {
                    .mobile-sticky-overlay { display: none; }
                  }
                </style>
                </head><body>
                <div class="mobile-sticky-overlay">Mobile Blocking Modal</div>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=201'});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")
            elif self.path.startswith("/products/warn"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  await fetch('/cart/add.js', {method:'POST', body:'id=301'});
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
                headers = [("Set-Cookie", f"cart_session={session_id}; Path=/")]
                if active_scenario[0] == "pass":
                    carts[session_id].append({"variant_id": 101, "product_id": 1, "quantity": 1})
                    self.send_with_headers(200, "{}", "application/json", headers)
                elif active_scenario[0] == "fail":
                    carts[session_id].append({"variant_id": 201, "product_id": 2, "quantity": 1})
                    self.send_with_headers(200, "{}", "application/json", headers)
                elif active_scenario[0] == "warn":
                    self.send_with_headers(500, '{"error": "server error"}', "application/json", headers)
                else:
                    self.send_with_headers(400, '{"error": "bad request"}', "application/json", headers)

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

    server = ThreadingHTTPServer(("127.0.0.1", 0), Phase4Storefront)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        env = os.environ.copy()
        env.update({
            "PYTHONPATH": "src",
            "MCP_ENABLED": "false",
            "LLM_PROVIDER": "openai",
            "LLM_API_KEY": "",
        })

        out_dir = Path("phase4_verification_output_fixed")
        out_dir.mkdir(exist_ok=True)

        scenarios = [
            ("pass", f"{origin}/products/pass", "PASS", "NONE"),
            ("fail", f"{origin}/products/fail", "FAIL", "BUTTON_BLOCKED_BY_OVERLAY"),
            ("warn", f"{origin}/products/warn", "WARN", "NETWORK_OR_RESPONSE_UNRESOLVED"),
        ]

        for name, url, expected_status, expected_reason in scenarios:
            active_scenario[0] = name
            print(f"\n--- Running Phase 4 CLI audit for scenario: {name} (expected {expected_status}) ---")
            json_out = out_dir / f"result_{name}.json"
            reports_dir = out_dir / f"reports_{name}"
            cmd = [
                sys.executable, "-m", "nano_sre.cli",
                "--report-dir", str(reports_dir),
                "audit",
                "--url", url,
                "--skill", "shopify_mobile_purchase_blocker_auditor",
                "--output", str(json_out),
            ]
            res = subprocess.run(cmd, env=env, text=True, capture_output=True, timeout=120)
            assert res.returncode == 0, f"CLI command failed with exit code {res.returncode}\nSTDERR: {res.stderr}"

            assert json_out.exists(), f"Output JSON missing for {name}"
            data = json.loads(json_out.read_text(encoding="utf-8"))
            assert len(data["results"]) == 1, "Expected exactly 1 skill result"
            skill_res = data["results"][0]

            assert skill_res["skill_name"] == "shopify_mobile_purchase_blocker_auditor", f"Unexpected skill_name: {skill_res['skill_name']}"
            status = skill_res["status"]
            reason = skill_res["details"].get("reason_code")

            print(f"Scenario {name} status: {status}, reason: {reason} (summary: {skill_res['summary']})")
            assert status == expected_status, f"Expected status {expected_status} for {name}, got {status}"
            assert reason == expected_reason, f"Expected reason_code {expected_reason} for {name}, got {reason}"

            # Verify details structure
            details = skill_res["details"]
            for field in ("reason_code", "steps", "proven_purchase_conditions", "expected", "observed", "timestamp", "reproduction_steps", "network_cart_evidence", "tested_viewports", "desktop_comparison", "reproduction_summary", "environment"):
                assert field in details, f"Missing required field '{field}' in details"

            if expected_status == "FAIL":
                assert details["desktop_comparison"]["classification"] == "MOBILE_SPECIFIC", f"Expected MOBILE_SPECIFIC classification, got {details['desktop_comparison']['classification']}"
                assert details["reproduction_summary"]["reproduction_confirmed"] is True, "Expected reproduction confirmed for FAIL scenario"

            # Check that screenshot file reference exists on disk if provided
            screenshot_path = details.get("redacted_screenshot")
            if screenshot_path:
                assert Path(screenshot_path).exists(), f"Screenshot file '{screenshot_path}' does not exist on disk!"

            # Check privacy - verify no sensitive tokens or passwords in details or summary
            dumped_details = json.dumps(details) + " " + skill_res["summary"]
            for forbidden in ("shpat_", "bearer ", "password=", "secret"):
                assert forbidden not in dumped_details.lower(), f"Forbidden secret string '{forbidden}' found in output details"

            # Inspect Markdown report
            reports = sorted(reports_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
            assert len(reports) > 0, f"No markdown report generated for {name}"
            print(f"Report path: {reports[0]}")

        print("\nAll Phase 4 CLI scenario verifications PASSED successfully!")

    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
