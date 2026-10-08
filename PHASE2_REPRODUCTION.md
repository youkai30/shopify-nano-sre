# PHASE 2 - VARIANT LOGIC REPRODUCTION INSTRUCTIONS

This document outlines how to reproduce the Phase 2 Variant Logic audit runs and pytest test suite.

## Prerequisites
- Python 3.12+ with Playwright Chromium installed (`python -m playwright install chromium`)
- Environment variables: `PYTHONPATH=src MCP_ENABLED=false LLM_PROVIDER=openai LLM_API_KEY=""`

## 1. Running the Pytest Suite
To run all 273 unit and integration tests (including 268 Phase 1 tests + 5 Phase 2 tests):

```bash
PYTHONPATH=src MCP_ENABLED=false LLM_PROVIDER=openai LLM_API_KEY="" python3 -m pytest tests/ -v --tb=short --junitxml=report.xml
```

To run only the Phase 2 Variant Auditor skill tests:
```bash
PYTHONPATH=src MCP_ENABLED=false LLM_PROVIDER=openai LLM_API_KEY="" python3 -m pytest tests/test_variant_auditor.py -v --tb=short
```

## 2. Running Standalone Local Synthetic Store Audits
To launch the standalone synthetic variant storefront and run CLI audits for each scenario:

### Mode: PASS (All checks pass)
```bash
python3 scripts/local_synthetic_variant_store.py pass 9880 &
SERVER_PID=$!
sleep 1
PYTHONPATH=src MCP_ENABLED=false LLM_PROVIDER=openai LLM_API_KEY="" python3 -m nano_sre.cli audit --url http://127.0.0.1:9880/products/item --skill shopify_variant_auditor --output variant_pass_report.json
kill $SERVER_PID
```

### Mode: Identity Fail (Form ID mismatch)
```bash
python3 scripts/local_synthetic_variant_store.py identity_fail 9881 &
SERVER_PID=$!
sleep 1
PYTHONPATH=src MCP_ENABLED=false LLM_PROVIDER=openai LLM_API_KEY="" python3 -m nano_sre.cli audit --url http://127.0.0.1:9881/products/item --skill shopify_variant_auditor --output variant_identity_fail_report.json
kill $SERVER_PID
```

### Mode: Price Fail (Stale variant price)
```bash
python3 scripts/local_synthetic_variant_store.py price_fail 9882 &
SERVER_PID=$!
sleep 1
PYTHONPATH=src MCP_ENABLED=false LLM_PROVIDER=openai LLM_API_KEY="" python3 -m nano_sre.cli audit --url http://127.0.0.1:9882/products/item --skill shopify_variant_auditor --output variant_price_fail_report.json
kill $SERVER_PID
```

### Mode: Image Fail (Stale variant image)
```bash
python3 scripts/local_synthetic_variant_store.py image_fail 9883 &
SERVER_PID=$!
sleep 1
PYTHONPATH=src MCP_ENABLED=false LLM_PROVIDER=openai LLM_API_KEY="" python3 -m nano_sre.cli audit --url http://127.0.0.1:9883/products/item --skill shopify_variant_auditor --output variant_image_fail_report.json
kill $SERVER_PID
```

### Mode: Availability Fail (Sold out variant enabled)
```bash
python3 scripts/local_synthetic_variant_store.py availability_fail 9884 &
SERVER_PID=$!
sleep 1
PYTHONPATH=src MCP_ENABLED=false LLM_PROVIDER=openai LLM_API_KEY="" python3 -m nano_sre.cli audit --url http://127.0.0.1:9884/products/item --skill shopify_variant_auditor --output variant_availability_fail_report.json
kill $SERVER_PID
```
