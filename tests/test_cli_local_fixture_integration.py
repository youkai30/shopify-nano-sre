"""Exercise the public CLI audit path against an isolated storefront."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.mark.integration
def test_cli_audit_runs_real_browser_shopper_and_writes_sanitized_reports(tmp_path):
    class Storefront(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, body, content_type="text/html; charset=utf-8"):
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Set-Cookie", "fixture_cli_session=local-only; Path=/; SameSite=Lax")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path.startswith("/products/item"):
                self.reply(200, """<!doctype html><button class='add-to-cart'>Add to cart</button>
                  <script>window.Shopify={routes:{root:'/'}};
                  document.querySelector('button').onclick=async()=>{
                    await fetch('/cart/add.js',{method:'POST',body:'fixture'});
                    setTimeout(()=>{document.body.insertAdjacentHTML('beforeend',
                    `<div role="dialog" aria-modal="true"><h2>CART (1 ITEM)</h2>
                    <button name="checkout">CHECKOUT</button></div>`);
                    document.querySelector('button[name="checkout"]').onclick=()=>
                    location.assign('/checkouts/cli-session-secret?query-secret=1');}, 250);
                  };</script>""")
            elif self.path == "/cart.js":
                self.reply(200, json.dumps({"item_count": 1, "items": [{
                    "key": "cli-line", "variant_id": 12, "product_id": 34,
                    "product_title": "CLI fixture", "variant_title": "Default",
                    "quantity": 1, "handle": "item",
                }]}), "application/json")
            elif self.path.startswith("/checkouts/cli-session-secret"):
                self.reply(200, "checkout fixture")
            else:
                self.reply(404, "missing", "text/plain")

        def do_POST(self):
            if self.path == "/cart/add.js":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.reply(200, "{}", "application/json")
            else:
                self.reply(404, "missing", "text/plain")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Storefront)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        project_root = Path(__file__).resolve().parents[1]
        reports = tmp_path / "reports"
        output = tmp_path / "result.json"
        origin = f"http://127.0.0.1:{server.server_port}"
        env = os.environ.copy()
        env.update({
            "PYTHONPATH": str(project_root / "src"),
            "MCP_ENABLED": "false",
            "LLM_API_KEY": "",
            "MCP_COMMAND": "",
            "MCP_SERVER_URL": "",
            "STORE_PASSWORD": "",
        })
        command = [
            sys.executable, "-m", "nano_sre.cli", "--report-dir", str(reports),
            "audit", "--url", f"{origin}/products/item", "--skill", "shopify_shopper",
            "--output", str(output),
        ]
        completed = subprocess.run(
            command, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=90,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        result = json.loads(output.read_text(encoding="utf-8"))
        shopper = result["results"][0]
        assert shopper["skill_name"] == "shopify_shopper"
        assert shopper["status"] == "PASS"
        assert shopper["details"]["checkout_url"].endswith("/checkouts/[redacted]")
        rendered = "\n".join(path.read_text(encoding="utf-8") for path in reports.glob("*.md"))
        persisted = output.read_text(encoding="utf-8") + rendered
        assert "cli-session-secret" not in persisted
        assert "query-secret" not in persisted
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
