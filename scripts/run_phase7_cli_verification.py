#!/usr/bin/env python3
"""Phase 7 Standalone CLI Verification Script for Shopify Search & Discovery Auditor.

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def compute_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


class MockShopifySearchServer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path

        if path.startswith("/products/pass-demo"):
            html = """<!doctype html><html><body>
            <h1>Product Search Pass Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9001",
              "handle": "pass-snowboard",
              "title": "Pass Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form action="/search" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send(200, html, "text/html")

        elif path.startswith("/products/warn-demo"):
            html = """<!doctype html><html><body>
            <h1>Unpublished Product Warn Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9002",
              "handle": "secret-snowboard",
              "title": "Secret Snowboard",
              "published_status": false,
              "eligible_for_search": false,
              "expected_query": "snowboard"
            }
            </script>
            <form action="/search" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit">Search</button>
            </form>
            </body></html>"""
            self.send(200, html, "text/html")

        elif path.startswith("/products/fail-demo"):
            html = """<!doctype html><html><body>
            <h1>Disabled Search Submit Button Fail Demo</h1>
            <script data-catalog-fixture type="application/json">
            {
              "product_id": "9003",
              "handle": "fail-snowboard",
              "title": "Fail Snowboard",
              "published_status": true,
              "eligible_for_search": true,
              "expected_query": "snowboard"
            }
            </script>
            <form action="/search" method="get">
              <input type="search" name="q" placeholder="Search products...">
              <button type="submit" disabled>Search</button>
            </form>
            </body></html>"""
            self.send(200, html, "text/html")

        elif path.startswith("/search"):
            html = """<!doctype html><html><body>
            <h1>Search Results</h1>
            <div id="search-results">
              <div class="product-card">
                <a href="/products/pass-snowboard">Pass Snowboard</a>
              </div>
            </div>
            </body></html>"""
            self.send(200, html, "text/html")

        elif path.startswith("/products/pass-snowboard"):
            html = """<!doctype html><html><body>
            <h1>Pass Snowboard PDP</h1>
            <p>Product ID: 9001</p>
            </body></html>"""
            self.send(200, html, "text/html")

        else:
            self.send(404, "Not Found", "text/plain")

    def send(self, status, body, content_type):
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def run_verification():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockShopifySearchServer)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    run_id = f"phase7_run_{int(time.time())}"
    output_base_dir = os.path.join("verification_output", run_id)
    os.makedirs(output_base_dir, exist_ok=True)

    print(f"Running Phase 7 Subprocess CLI Verification against http://127.0.0.1:{port}...")

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
            "shopify_search_discovery_auditor",
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
        screenshot_ok = True
        if os.path.exists(json_out):
            try:
                with open(json_out, "r", encoding="utf-8") as jf:
                    data = json.load(jf)
                    res_list = data.get("results", [])
                    if res_list and isinstance(res_list, list):
                        res_obj = res_list[0]
                        observed_status = res_obj.get("status", "UNKNOWN")
                        details = res_obj.get("details", {})
                        screenshot_path = details.get("redacted_screenshot")
                        if screenshot_path:
                            if not os.path.exists(screenshot_path):
                                print(f"Referenced screenshot missing on disk: {screenshot_path}")
                                screenshot_ok = False
                            else:
                                print(f"Verified screenshot exists on disk: {screenshot_path}")
            except Exception as e:
                print(f"Failed to read JSON output: {e}")

        matched = (observed_status == expected_status) and (proc.returncode == 0) and screenshot_ok
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
        print(f"\nPhase 7 Subprocess CLI verification completed successfully! All expected statuses matched. Artifacts saved to: {output_base_dir}")
        sys.exit(0)
    else:
        print(f"\nPhase 7 Subprocess CLI verification failed! Artifacts saved to: {output_base_dir}")
        sys.exit(1)


if __name__ == "__main__":
    run_verification()
