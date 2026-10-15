import pytest
from unittest.mock import AsyncMock, MagicMock
from nano_sre.skills.shopify_purchase_blocker_auditor import ShopifyPurchaseBlockerAuditor
from nano_sre.skills.shopify_mobile_purchase_blocker_auditor import ShopifyMobilePurchaseBlockerAuditor

def make_mock_locator(count_val=0, is_visible_val=True, eval_val=""):
    loc = MagicMock()
    loc.count = AsyncMock(return_value=count_val)
    loc.is_visible = AsyncMock(return_value=is_visible_val)
    loc.evaluate = AsyncMock(return_value=eval_val)
    loc.select_option = AsyncMock(return_value=None)
    loc.click = AsyncMock(return_value=None)
    loc.nth = MagicMock(return_value=loc)
    loc.first = loc
    return loc

@pytest.mark.asyncio
async def test_desktop_auditor_default_title_selection():
    auditor = ShopifyPurchaseBlockerAuditor()
    page = MagicMock()
    main_form = MagicMock()
    main_form.count = AsyncMock(return_value=1)

    target_variant = {"id": 101, "options": ["Default Title"]}
    options_list = [{"name": "Title", "values": ["Default Title"]}]

    success, err, proven = await auditor._select_variant_options_in_dom(
        page, main_form, target_variant, options_list
    )

    assert success is True
    assert err == ""
    assert proven == {}

@pytest.mark.asyncio
async def test_desktop_auditor_real_option_no_control():
    auditor = ShopifyPurchaseBlockerAuditor()
    page = MagicMock()
    main_form = MagicMock()
    main_form.count = AsyncMock(return_value=1)

    mock_loc = make_mock_locator(count_val=0)
    main_form.locator = MagicMock(return_value=mock_loc)

    target_variant = {"id": 102, "options": ["Red"]}
    options_list = [{"name": "Color", "values": ["Red", "Blue"]}]

    success, err, proven = await auditor._select_variant_options_in_dom(
        page, main_form, target_variant, options_list
    )

    assert success is False
    assert "Option selection in DOM could not be proven" in err

@pytest.mark.asyncio
async def test_desktop_auditor_real_option_with_select():
    auditor = ShopifyPurchaseBlockerAuditor()
    page = MagicMock()
    main_form = MagicMock()
    main_form.count = AsyncMock(return_value=1)

    mock_select = make_mock_locator(count_val=1, is_visible_val=True, eval_val="Red")
    main_form.locator = MagicMock(return_value=mock_select)

    target_variant = {"id": 103, "options": ["Red"]}
    options_list = [{"name": "Color", "values": ["Red", "Blue"]}]

    success, err, proven = await auditor._select_variant_options_in_dom(
        page, main_form, target_variant, options_list
    )

    assert success is True
    assert err == ""
    assert proven == {"Color": "Red"}


@pytest.mark.asyncio
async def test_mobile_auditor_default_title_selection():
    auditor = ShopifyMobilePurchaseBlockerAuditor()
    page = MagicMock()
    main_form = MagicMock()
    main_form.count = AsyncMock(return_value=1)
    mobile_cfg = {"has_touch": True}

    target_variant = {"id": 201, "options": ["Default Title"]}
    options_list = [{"name": "Title", "values": ["Default Title"]}]

    success, err, proven = await auditor._select_variant_options_in_dom(
        page, main_form, target_variant, options_list, mobile_cfg
    )

    assert success is True
    assert err == ""
    assert proven == {}

@pytest.mark.asyncio
async def test_mobile_auditor_real_option_no_control():
    auditor = ShopifyMobilePurchaseBlockerAuditor()
    page = MagicMock()
    main_form = MagicMock()
    main_form.count = AsyncMock(return_value=1)
    mobile_cfg = {"has_touch": True}

    mock_loc = make_mock_locator(count_val=0)
    main_form.locator = MagicMock(return_value=mock_loc)

    target_variant = {"id": 202, "options": ["Large"]}
    options_list = [{"name": "Size", "values": ["Small", "Large"]}]

    success, err, proven = await auditor._select_variant_options_in_dom(
        page, main_form, target_variant, options_list, mobile_cfg
    )

    assert success is False
    assert "Option selection in DOM could not be proven" in err

@pytest.mark.asyncio
async def test_mobile_auditor_real_option_with_select():
    auditor = ShopifyMobilePurchaseBlockerAuditor()
    page = MagicMock()
    main_form = MagicMock()
    main_form.count = AsyncMock(return_value=1)
    mobile_cfg = {"has_touch": True}

    mock_select = make_mock_locator(count_val=1, is_visible_val=True, eval_val="Large")
    main_form.locator = MagicMock(return_value=mock_select)

    target_variant = {"id": 203, "options": ["Large"]}
    options_list = [{"name": "Size", "values": ["Small", "Large"]}]

    success, err, proven = await auditor._select_variant_options_in_dom(
        page, main_form, target_variant, options_list, mobile_cfg
    )

    assert success is True
    assert err == ""
    assert proven == {"Size": "Large"}
