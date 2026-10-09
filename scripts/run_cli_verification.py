"""CLI verification script for PASS/FAIL/WARN standalone execution."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def make_server(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t

# PASS Storefront
class PassStorefront(BaseHTTPRequestHandler):
    cart_items = []
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path == "/products/item.js":
            data = {"id": 1, "handle": "item", "options": [{"name": "Size", "values": ["Small"]}], "variants": [{"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}]}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
        elif self.path == "/cart.js":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = {"item_count": sum(i["quantity"] for i in PassStorefront.cart_items), "items": PassStorefront.cart_items}
            self.wfile.write(json.dumps(data).encode("utf-8"))
        elif self.path.startswith("/products/item") or self.path == "/":
            html = """<!doctype html><html><body>
            <form action="/cart/add" method="post">
              <select name="options[Size]"><option value="Small">Small</option></select>
              <button type="submit" name="add">Add to Cart</button>
            </form>
            <script>
            document.querySelector('form').onsubmit = async (e) => {
              e.preventDefault();
              await fetch('/cart/add.js', {method:'POST', body:'id=101'});
            };
            </script></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/cart/add.js":
            PassStorefront.cart_items.append({"variant_id": 101, "product_id": 1, "quantity": 1})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": 101, "quantity": 1}).encode("utf-8"))

# FAIL Storefront (Overlay Blocking Button)
class FailStorefront(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path == "/products/item.js":
            data = {"id": 1, "handle": "item", "options": [{"name": "Size", "values": ["Small"]}], "variants": [{"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}]}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
        elif self.path == "/cart.js":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"item_count": 0, "items": []}).encode("utf-8"))
        elif self.path.startswith("/products/item") or self.path == "/":
            html = """<!doctype html><html><body>
            <div id="blocking-modal" style="position:fixed;top:0;left:0;width:100%;height:100%;z-index:9999;background:rgba(0,0,0,0.8);">Uncloseable Modal</div>
            <form action="/cart/add" method="post">
              <select name="options[Size]"><option value="Small">Small</option></select>
              <button type="submit" name="add">Add to Cart</button>
            </form></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

# WARN Storefront (500 Server Error on Cart Add)
class WarnStorefront(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path == "/products/item.js":
            data = {"id": 1, "handle": "item", "options": [{"name": "Size", "values": ["Small"]}], "variants": [{"id": 101, "title": "Small", "price": 1599, "available": True, "options": ["Small"]}]}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
        elif self.path == "/cart.js":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"item_count": 0, "items": []}).encode("utf-8"))
        elif self.path.startswith("/products/item") or self.path == "/":
            html = """<!doctype html><html><body>
            <form action="/cart/add" method="post">
              <select name="options[Size]"><option value="Small">Small</option></select>
              <button type="submit" name="add">Add to Cart</button>
            </form>
            <script>
            document.querySelector('form').onsubmit = async (e) => {
              e.preventDefault();
              await fetch('/cart/add.js', {method:'POST', body:'id=101'});
            };
            </script></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/cart/add.js":
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Internal server error"}')

def run_verification():
    os.makedirs("reports/cli_artifacts", exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["MCP_ENABLED"] = "false"
    env["LLM_PROVIDER"] = "openai"
    env["LLM_API_KEY"] = ""

    results = {}

    # 1. PASS Case
    s_pass, t_pass = make_server(PassStorefront)
    url_pass = f"http://127.0.0.1:{s_pass.server_port}/products/item"
    out_pass = "reports/cli_artifacts/cli_audit_pass.json"
    cmd = [
        sys.executable, "-m", "nano_sre.cli", "audit",
        "--url", url_pass,
        "--skill", "shopify_purchase_blocker_auditor",
        "--output", out_pass
    ]
    res_pass = subprocess.run(cmd, env=env, capture_output=True, text=True)
    s_pass.shutdown()
    s_pass.server_close()
    results["PASS"] = {
        "returncode": res_pass.returncode,
        "stdout": res_pass.stdout,
        "stderr": res_pass.stderr,
        "json": json.loads(open(out_pass).read()) if os.path.exists(out_pass) else None,
    }

    # 2. FAIL Case
    s_fail, t_fail = make_server(FailStorefront)
    url_fail = f"http://127.0.0.1:{s_fail.server_port}/products/item"
    out_fail = "reports/cli_artifacts/cli_audit_fail.json"
    cmd = [
        sys.executable, "-m", "nano_sre.cli", "audit",
        "--url", url_fail,
        "--skill", "shopify_purchase_blocker_auditor",
        "--output", out_fail
    ]
    res_fail = subprocess.run(cmd, env=env, capture_output=True, text=True)
    s_fail.shutdown()
    s_fail.server_close()
    results["FAIL"] = {
        "returncode": res_fail.returncode,
        "stdout": res_fail.stdout,
        "stderr": res_fail.stderr,
        "json": json.loads(open(out_fail).read()) if os.path.exists(out_fail) else None,
    }

    # 3. WARN Case
    s_warn, t_warn = make_server(WarnStorefront)
    url_warn = f"http://127.0.0.1:{s_warn.server_port}/products/item"
    out_warn = "reports/cli_artifacts/cli_audit_warn.json"
    cmd = [
        sys.executable, "-m", "nano_sre.cli", "audit",
        "--url", url_warn,
        "--skill", "shopify_purchase_blocker_auditor",
        "--output", out_warn
    ]
    res_warn = subprocess.run(cmd, env=env, capture_output=True, text=True)
    s_warn.shutdown()
    s_warn.server_close()
    results["WARN"] = {
        "returncode": res_warn.returncode,
        "stdout": res_warn.stdout,
        "stderr": res_warn.stderr,
        "json": json.loads(open(out_warn).read()) if os.path.exists(out_warn) else None,
    }

    with open("reports/cli_artifacts/cli_verification_results.json", "w") as f:
        json.dump(results, f, indent=2)

    print("CLI verification completed successfully.")

if __name__ == "__main__":
    run_verification()
