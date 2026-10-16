import pytest
from nano_sre.utils.shopify import is_excluded_product, EXCLUDED_KEYWORDS

def test_is_excluded_product_gift_card():
    prod = {
        "handle": "daisy-egift-card",
        "title": "E-Gift Card",
        "product_type": "Gift Card",
        "tags": ["gift"],
        "variants": [{"id": 1, "available": True, "price": 20.0}]
    }
    excluded, reason = is_excluded_product(prod)
    assert excluded is True
    assert "Handle contains excluded keyword" in reason or "Product type contains excluded keyword" in reason

def test_is_excluded_product_repair_service():
    prod = {
        "handle": "repair-shipping",
        "title": "Jeans Repair Shipping Service",
        "product_type": "Service",
        "tags": ["repair"],
        "variants": [{"id": 2, "available": True, "price": 15.0}]
    }
    excluded, reason = is_excluded_product(prod)
    assert excluded is True
    assert "Handle contains excluded keyword" in reason or "Title contains excluded keyword" in reason

def test_is_excluded_product_zero_price():
    prod = {
        "handle": "free-swatch-sample",
        "title": "Free Fabric Swatch",
        "product_type": "Sample",
        "tags": ["free"],
        "variants": [{"id": 3, "available": True, "price": 0.0}]
    }
    excluded, reason = is_excluded_product(prod)
    assert excluded is True

def test_is_excluded_product_out_of_stock():
    prod = {
        "handle": "sold-out-jacket",
        "title": "Sold Out Leather Jacket",
        "product_type": "Jacket",
        "tags": ["apparel"],
        "variants": [{"id": 4, "available": False, "price": 100.0}]
    }
    excluded, reason = is_excluded_product(prod)
    assert excluded is True
    assert "no available variants" in reason

def test_is_excluded_product_valid_apparel():
    prod = {
        "handle": "oxford-shoulder-bag-in-dijon",
        "title": "Oxford Shoulder Bag",
        "product_type": "Bag",
        "tags": ["bags", "new"],
        "variants": [
            {"id": 10, "available": True, "price": 65.0, "title": "Dijon"},
            {"id": 11, "available": True, "price": 65.0, "title": "Black"}
        ]
    }
    excluded, reason = is_excluded_product(prod)
    assert excluded is False
    assert reason == ""


def _prod(handle, title=None, ptype="", tags=None, variants=None):
    return {
        "handle": handle,
        "title": title or handle.replace("-", " "),
        "product_type": ptype,
        "tags": tags if tags is not None else [],
        "variants": variants or [{"id": 1, "available": True, "price": "25.00"}],
    }


def test_substring_false_positives_are_not_excluded():
    from nano_sre.utils.shopify import is_excluded_product

    assert is_excluded_product(_prod("wool-cardigan"))[0] is False
    assert is_excluded_product(_prod("hot-sauce-gift-set"))[0] is False
    assert is_excluded_product(_prod("silk-scarf", tags=["gift", "gifts-for-her"]))[0] is False
    assert is_excluded_product(_prod("leather-cardholder"))[0] is False


def test_real_non_product_items_are_excluded():
    from nano_sre.utils.shopify import is_excluded_product

    for prod in (
        _prod("gift-card", ptype="Gift Cards"),
        _prod("roka-london-gift-card"),
        _prod("daisy-egift-card"),
        _prod("gift-voucher"),
        _prod("repair-shipping"),
        _prod("garden-floral-fabric-sample"),
        _prod("anything", tags=["Gift Card"]),
    ):
        assert is_excluded_product(prod)[0] is True, prod["handle"]


def test_custom_products_are_chosen_last():
    from nano_sre.utils.shopify import is_deprioritized_product

    assert is_deprioritized_product(_prod("custom-gift-box")) is True
    assert is_deprioritized_product(_prod("personalised-cat-print")) is True
    assert is_deprioritized_product(_prod("oxford-shoulder-bag")) is False


class _Resp:
    def __init__(self, data, ok=True):
        self._d, self.ok = data, ok

    async def json(self):
        return self._d


class _Req:
    def __init__(self, data, ok=True):
        self._r = _Resp(data, ok)

    async def get(self, url, timeout=None):
        return self._r


class _Page:
    def __init__(self, data, ok=True):
        self.request = _Req(data, ok)


async def test_picker_skips_gift_cards_and_prefers_multi_variant():
    from nano_sre.utils.shopify import pick_representative_product

    two = [{"id": 1, "available": True, "price": "30.00"}, {"id": 2, "available": True, "price": "30.00"}]
    data = {"products": [
        _prod("daisy-egift-card"),
        _prod("custom-gift-box", variants=two),
        _prod("repair-shipping"),
        _prod("plain-tee"),
        _prod("linen-shirt", variants=two),
    ]}
    handle, meta = await pick_representative_product(_Page(data), "https://x.com", "", "https://x.com")
    assert handle == "linen-shirt"
    assert {e["handle"] for e in meta["exclusions"]} == {"daisy-egift-card", "repair-shipping"}
    assert meta["chosen_handle"] == "linen-shirt"


async def test_picker_falls_back_to_custom_when_it_is_the_only_option():
    from nano_sre.utils.shopify import pick_representative_product

    data = {"products": [_prod("gift-card"), _prod("custom-gift-box")]}
    handle, _ = await pick_representative_product(_Page(data), "https://x.com", "", "https://x.com")
    assert handle == "custom-gift-box"
