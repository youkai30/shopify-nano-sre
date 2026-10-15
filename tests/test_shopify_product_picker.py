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
