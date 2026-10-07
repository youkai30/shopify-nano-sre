"""Cart snapshot validation and request ownership tests."""

import pytest

from nano_sre.skills.shopify_shopper import (
    _CartEvidenceCollector,
    _cart_root,
    _empty_cart_meta,
    _safe_url,
    _validate_cart_payload,
)
from nano_sre.agent.reporter import _format_details


class Response:
    def __init__(self, data, status=200):
        self.data = data
        self.status = status
        self.ok = status < 400
        self.disposed = False

    async def json(self):
        return self.data

    async def dispose(self):
        self.disposed = True


class Request:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class Page:
    url = "https://shop.example/fr/products/item?campaign=private"

    def __init__(self, response, root="/fr/"):
        self.request = Request(response)
        self.root = root

    async def evaluate(self, _expression):
        return self.root


def test_cart_counts_preserve_distinct_lines_and_only_safe_fields():
    payload = {
        "item_count": 5,
        "items": [
            {"key": "line-a", "variant_id": 7, "product_id": 9,
             "product_title": "Tea", "variant_title": "Small", "quantity": 2,
             "handle": "tea", "properties": {"private": "discard"}},
            {"key": "line-b", "variant_id": 7, "product_id": 9,
             "product_title": "Tea", "variant_title": "Small", "quantity": 3,
             "handle": "tea", "selling_plan_id": 11},
        ],
    }
    lines, total, line_count = _validate_cart_payload(payload)
    assert total == 5
    assert line_count == 2
    assert [line["key"] for line in lines] == ["line-a", "line-b"]
    assert "properties" not in lines[0]
    assert lines[1]["selling_plan_id"] == 11


@pytest.mark.parametrize("payload", [
    {}, {"items": []}, {"item_count": 0, "items": [{"quantity": 0}]},
    {"item_count": 4, "items": [{"quantity": 3}]},
])
def test_invalid_cart_payload_fails_closed(payload):
    with pytest.raises(ValueError):
        _validate_cart_payload(payload)


@pytest.mark.parametrize("field, value", [
    ("key", {"nested": "value"}),
    ("variant_id", [7]),
    ("product_id", {"id": 9}),
    ("product_title", {"title": "Tea"}),
    ("variant_title", ["Small"]),
    ("handle", {"handle": "tea"}),
    ("selling_plan_id", {"id": 11}),
])
def test_cart_payload_rejects_nested_identifier_and_text_fields(field, value):
    item = {"key": "line", "variant_id": 7, "product_id": 9,
            "product_title": "Tea", "variant_title": "Small", "handle": "tea",
            "quantity": 1}
    item[field] = value
    with pytest.raises(ValueError):
        _validate_cart_payload({"item_count": 1, "items": [item]})


@pytest.mark.asyncio
async def test_collector_uses_locale_root_once_and_disposes_response():
    response = Response({"item_count": 5, "items": [
        {"key": "a", "variant_id": 7, "quantity": 2},
        {"key": "b", "variant_id": 7, "quantity": 3},
    ]})
    page = Page(response)
    evidence, meta = [], _empty_cart_meta()
    collector = _CartEvidenceCollector(evidence, meta)
    await collector.collect(page, "auto_open_drawer")
    await collector.collect(page, "should_not_retry")
    assert len(page.request.calls) == 1
    url, options = page.request.calls[0]
    assert url == "https://shop.example/fr/cart.js"
    assert options == {"timeout": 10000, "max_redirects": 0}
    assert response.disposed
    assert meta["status"] == "nonempty"
    assert meta["api_total_quantity"] == 5
    assert meta["api_line_count"] == 2
    assert meta["source"] == "api"
    assert meta["trigger"] == "auto_open_drawer"


@pytest.mark.asyncio
async def test_cart_root_rejects_cross_origin_and_traversal():
    with pytest.raises(ValueError):
        await _cart_root(Page(Response({}), "https://evil.example/"))
    with pytest.raises(ValueError):
        await _cart_root(Page(Response({}), "/fr/../"))


@pytest.mark.asyncio
@pytest.mark.parametrize("root", ["/fr/%2e%2e/", "/fr/%2E%2E/", "/fr\\..\\evil/", "/fr/%5c..%5c/"])
async def test_cart_root_rejects_encoded_and_backslash_traversal(root):
    with pytest.raises(ValueError):
        await _cart_root(Page(Response({}), root))


@pytest.mark.parametrize("status, payload, timeout", [
    (503, {}, False), (200, ValueError("bad json"), False),
    (200, {"unexpected": []}, False), (200, {}, True),
])
@pytest.mark.asyncio
async def test_failed_collection_is_attempted_once_and_reportable(status, payload, timeout):
    class FailingResponse(Response):
        async def json(self):
            if isinstance(payload, Exception):
                raise payload
            return payload

    page = Page(FailingResponse(payload, status=status))
    if timeout:
        async def fail_get(url, **kwargs):
            page.request.calls.append((url, kwargs))
            raise TimeoutError("fixture timeout")
        page.request.get = fail_get
    evidence, meta = [], _empty_cart_meta()
    collector = _CartEvidenceCollector(evidence, meta)
    await collector.collect(page, "api_only_fallback")
    await collector.collect(page, "retry_forbidden")
    assert len(page.request.calls) == 1
    assert meta["status"] == "failed"
    assert meta["source"] == "api"
    assert meta["captured_at"] is None
    assert evidence == []
    rendered = _format_details({"cart_evidence": evidence, "cart_evidence_meta": meta})
    assert "failed" in rendered


@pytest.mark.asyncio
async def test_http_error_fixture_records_real_503_status():
    page = Page(Response({}, status=503))
    evidence, meta = [], _empty_cart_meta()
    await _CartEvidenceCollector(evidence, meta).collect(page, "api_only_fallback")
    assert meta["http_status"] == 503
    assert meta["error_code"] == "http_503"


@pytest.mark.asyncio
async def test_collector_never_copies_exception_code_into_evidence_metadata():
    class SecretCodedError(Exception):
        code = "session-token-must-not-persist"

    page = Page(Response({}))

    async def fail_get(*_args, **_kwargs):
        raise SecretCodedError()

    page.request.get = fail_get
    evidence, meta = [], _empty_cart_meta()
    await _CartEvidenceCollector(evidence, meta).collect(page, "api_only_fallback")
    assert meta["error_code"] == "request_error"


@pytest.mark.parametrize("status", ["not_attempted", "failed", "empty", "nonempty"])
def test_markdown_displays_each_cart_evidence_state(status):
    rendered = _format_details({
        "cart_evidence": [],
        "cart_evidence_meta": {"status": status, "source": "api"},
    })
    assert "cart_evidence" in rendered
    assert status in rendered


def test_checkout_path_session_identifier_is_redacted():
    rendered = _safe_url(
        "https://shop.example/checkouts/cn_very_secret_session?key=also-secret"
    )
    assert rendered == "https://shop.example/checkouts/[redacted]"
    assert "very_secret" not in rendered
    assert "also-secret" not in rendered
