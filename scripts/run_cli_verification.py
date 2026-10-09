"""Run real CLI verification outside pytest with real Chromium browser on PASS, FAIL, and WARN scenarios."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    carts = {"pass": [], "fail": []}
    active_scenario = ["pass"]

    class MultiScenarioStorefront(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            # PASS scenario product
            if self.path == "/products/pass.js":
                data = {
                    "id": 1,
                    "handle": "pass",
                    "options": [{"name": "Size", "values": ["Small", "Large"]}],
                    "variants": [
                        {"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"], "featured_image": {"src": "/img.svg"}},
                        {"id": 102, "title": "Large", "price": 2500, "available": True, "options": ["Large"], "featured_image": {"src": "/img.svg"}}
                    ]
                }
                self.send(200, json.dumps(data), "application/json")
            # FAIL scenario product
            elif self.path == "/products/fail.js":
                data = {
                    "id": 2,
                    "handle": "fail",
                    "options": [{"name": "Size", "values": ["Small", "Large"]}],
                    "variants": [
                        {"id": 201, "title": "Small", "price": 1599, "options": ["Small"]},
                        {"id": 202, "title": "Large", "price": 2500, "options": ["Large"]}
                    ]
                }
                self.send(200, json.dumps(data), "application/json")
            # WARN scenario product
            elif self.path == "/products/warn.js":
                data = {
                    "id": 3,
                    "handle": "warn",
                    "options": [{"name": "Size", "values": ["Small", "Large"]}, {"name": "Color", "values": ["Red"]}],
                    "variants": [
                        {"id": 301, "title": "Small / Red", "price": 1599, "options": ["Small", "Red"]}
                    ]
                }
                self.send(200, json.dumps(data), "application/json")
            elif self.path == "/img.svg":
                svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="green"/></svg>'
                self.send(200, svg, "image/svg+xml")
            elif self.path == "/cart.js":
                c = carts.get(active_scenario[0], [])
                self.send(200, json.dumps({"item_count": sum(i["quantity"] for i in c), "items": c}), "application/json")
            elif self.path.startswith("/products/pass"):
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Small">Small</option>
                    <option value="Large">Large</option>
                  </select>
                  <span class="price-item--regular">$15.99</span>
                  <img class="product-featured-media" src="/img.svg" width="100" height="100"/>
                  <button type="submit" name="add">Add to cart</button>
                </form>
                <script>
                const sel = document.querySelector('select');
                const price = document.querySelector('.price-item--regular');
                sel.onchange = () => {
                  if (sel.value === 'Large') price.textContent = '$25.00';
                  else price.textContent = '$15.99';
                };
                document.querySelector('form').onsubmit = async (e) => {
                  e.preventDefault();
                  const vid = sel.value === 'Large' ? 102 : 101;
                  await fetch('/cart/add.js', {method:'POST', body:'id='+vid});
                };
                </script></body></html>"""
                self.send(200, html, "text/html")
            elif self.path.startswith("/products/fail"):
                # Always sends wrong variant ID 201
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Small">Small</option>
                    <option value="Large">Large</option>
                  </select>
                  <span class="price-item--regular">$10.00</span>
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
                # Partial options in DOM
                html = """<!doctype html><html><body>
                <form action="/cart/add" method="post">
                  <select name="options[Size]">
                    <option value="Small">Small</option>
                  </select>
                  <span class="price-item--regular">$15.99</span>
                  <button type="submit" name="add">Add to cart</button>
                </form></body></html>"""
                self.send(200, html, "text/html")
            else:
                self.send(404, "Not found", "text/plain")

        def do_POST(self):
            if self.path == "/cart/add.js":
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                if active_scenario[0] == "pass":
                    vid = 102 if "102" in body else 101
                    carts["pass"].append({"variant_id": vid, "product_id": 1, "quantity": 1})
                else:
                    carts["fail"].append({"variant_id": 201, "product_id": 2, "quantity": 1})
                self.send(200, "{}", "application/json")

        def send(self, status, body, content_type):
            encoded = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), MultiScenarioStorefront)
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

        out_dir = Path("verification_output")
        out_dir.mkdir(exist_ok=True)

        scenarios = [
            ("pass", f"{origin}/products/pass", "PASS"),
            ("fail", f"{origin}/products/fail", "FAIL"),
            ("warn", f"{origin}/products/warn", "WARN"),
        ]

        for name, url, expected_status in scenarios:
            active_scenario[0] = name
            print(f"\n--- Running CLI audit for scenario: {name} (expected {expected_status}) ---")
            json_out = out_dir / f"result_{name}.json"
            reports_dir = out_dir / f"reports_{name}"
            cmd = [
                sys.executable, "-m", "nano_sre.cli",
                "--report-dir", str(reports_dir),
                "audit",
                "--url", url,
                "--skill", "shopify_variant_auditor",
                "--output", str(json_out),
            ]
            res = subprocess.run(cmd, env=env, text=True, capture_output=True, timeout=60)
            print("Exit code:", res.returncode)
            print("STDOUT:", res.stdout)
            if res.stderr:
                print("STDERR:", res.stderr)

            assert json_out.exists(), f"Output JSON missing for {name}"
            data = json.loads(json_out.read_text(encoding="utf-8"))
            skill_res = data["results"][0]
            status = skill_res["status"]
            print(f"Scenario {name} status: {status} (summary: {skill_res['summary']})")
            assert status == expected_status, f"Expected {expected_status} for {name}, got {status}"

            # Inspect Markdown report
            reports = list(reports_dir.glob("*.md"))
            assert len(reports) > 0, f"No markdown report generated for {name}"
            print(f"Report path: {reports[0]}")
            print("Report preview:")
            print(reports[0].read_text(encoding="utf-8")[:300])

        print("\nAll CLI scenario verifications PASSED successfully!")

    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
