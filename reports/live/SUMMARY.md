# Live Store Audit Summary

| Store | shopify_shopper | shopify_variant_auditor | shopify_purchase_blocker_auditor | shopify_mobile_purchase_blocker_auditor | shopify_cart_consistency_auditor | shopify_checkout_blocker_auditor | shopify_search_discovery_auditor |
| --- | --- | --- | --- | --- | --- | --- | --- |
| daisyjewellery.com | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN |
| ettaloves.com | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ❌ FAIL | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN |
| hiutdenim.co.uk | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN |
| marloewatchcompany.com | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| mustardmade.com | ✅ PASS | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN |
| regalrose.co.uk | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN |
| rokalondon.com | ❌ FAIL | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN |
| sauceshop.co | ✅ PASS | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN |
| sophieallport.com | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | N/A |
| upcirclebeauty.com | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN | ⚠️ WARN |

## Non-PASS Details

- **daisyjewellery.com** [shopify_shopper] (WARN): Checkout navigation did not reach an expected checkout URL
- **daisyjewellery.com** [shopify_variant_auditor] (WARN): Variant audit completed with warnings: variant_identity, variant_price, variant_image
- **daisyjewellery.com** [shopify_purchase_blocker_auditor] (WARN): Option selection issue: Option selection in DOM could not be proven for all options ({})
- **daisyjewellery.com** [shopify_mobile_purchase_blocker_auditor] (WARN): Mobile option selection issue: Option selection in DOM could not be proven for all options ({})
- **daisyjewellery.com** [shopify_cart_consistency_auditor] (WARN): Variant selection unproven in DOM: Option control 'Denomination=20' exists but produced no active selection state in DOM
- **daisyjewellery.com** [shopify_checkout_blocker_auditor] (WARN): Target URL is outside local sandbox origin; checkout audit skipped for safety
- **daisyjewellery.com** [shopify_search_discovery_auditor] (WARN): Search query missing; search audit skipped with WARN
- **ettaloves.com** [shopify_shopper] (WARN): Could not find a product with an enabled Add to Cart button
- **ettaloves.com** [shopify_variant_auditor] (WARN): Could not establish main product form binding on PDP
- **ettaloves.com** [shopify_purchase_blocker_auditor] (WARN): Main product form or Add to Cart button binding unconfirmed
- **ettaloves.com** [shopify_mobile_purchase_blocker_auditor] (FAIL): Add to Cart button blocked on mobile by un-dismissable overlay or sticky element (xgeo-widget#.) (Shared issue: reproduced on desktop as well)
- **ettaloves.com** [shopify_cart_consistency_auditor] (WARN): Could not locate Add to Cart button in DOM
- **ettaloves.com** [shopify_checkout_blocker_auditor] (WARN): Target URL is outside local sandbox origin; checkout audit skipped for safety
- **ettaloves.com** [shopify_search_discovery_auditor] (WARN): Search query missing; search audit skipped with WARN
- **hiutdenim.co.uk** [shopify_shopper] (WARN): Could not find a product with an enabled Add to Cart button
- **hiutdenim.co.uk** [shopify_variant_auditor] (WARN): Could not fetch product JSON from https://hiutdenim.co.uk/products/repair-shipping.js
- **hiutdenim.co.uk** [shopify_purchase_blocker_auditor] (WARN): Could not fetch product JSON from https://hiutdenim.co.uk/products/repair-shipping.js
- **hiutdenim.co.uk** [shopify_mobile_purchase_blocker_auditor] (WARN): Could not fetch product JSON from https://hiutdenim.co.uk/products/the-astrid-barrel-fit-organic-denim.js
- **hiutdenim.co.uk** [shopify_cart_consistency_auditor] (WARN): Could not fetch product JSON from https://hiutdenim.co.uk/products/repair-shipping.js
- **hiutdenim.co.uk** [shopify_checkout_blocker_auditor] (WARN): Target URL is outside local sandbox origin; checkout audit skipped for safety
- **hiutdenim.co.uk** [shopify_search_discovery_auditor] (WARN): Search query missing; search audit skipped with WARN
- **mustardmade.com** [shopify_variant_auditor] (WARN): Variant audit completed with warnings: variant_identity, variant_price, variant_image
- **mustardmade.com** [shopify_purchase_blocker_auditor] (WARN): Main product form or Add to Cart button binding unconfirmed
- **mustardmade.com** [shopify_mobile_purchase_blocker_auditor] (WARN): Add to Cart tap executed on mobile but request dispatch could not be confirmed
- **mustardmade.com** [shopify_cart_consistency_auditor] (WARN): Could not locate Add to Cart button in DOM
- **mustardmade.com** [shopify_checkout_blocker_auditor] (WARN): Target URL is outside local sandbox origin; checkout audit skipped for safety
- **mustardmade.com** [shopify_search_discovery_auditor] (WARN): Search query missing; search audit skipped with WARN
- **regalrose.co.uk** [shopify_shopper] (WARN): Could not find a product with an enabled Add to Cart button
- **regalrose.co.uk** [shopify_variant_auditor] (WARN): Variant audit completed with warnings: variant_identity, variant_price, variant_image
- **regalrose.co.uk** [shopify_purchase_blocker_auditor] (WARN): Option selection issue: Option selection in DOM could not be proven for all options ({})
- **regalrose.co.uk** [shopify_mobile_purchase_blocker_auditor] (WARN): Mobile option selection issue: Option selection in DOM could not be proven for all options ({})
- **regalrose.co.uk** [shopify_cart_consistency_auditor] (WARN): Clicking Add to Cart button failed: Locator.click: Timeout 10000ms exceeded.
Call log:
  - waiting for locator("form[action*=\"/cart/add\"], form.prd-ProductOffers_Form, [data-type=\"add-to-cart-form\"]").first.locator("button[type=\"submit\"]:has-text(\"add\"):visible:not([disabled])").first
    - locator resolved to <button type="submit" class="button w-full klatc">Add to basket</button>
  - attempting click action
    2 × waiting for element to be visible, enabled and stable
      - element is visible, enabled and stable
      - scrolling into view if needed
      - done scrolling
      - <div role="dialog" aria-modal="true" aria-label="POPUP Form" data-kl-scroll-locking-modal="true" class="needsclick  kl-private-reset-css-Xuajs1">…</div> from <div>…</div> subtree intercepts pointer events
    - retrying click action
    - waiting 20ms
    2 × waiting for element to be visible, enabled and stable
      - element is visible, enabled and stable
      - scrolling into view if needed
      - done scrolling
      - <div role="dialog" aria-modal="true" aria-label="POPUP Form" data-kl-scroll-locking-modal="true" class="needsclick  kl-private-reset-css-Xuajs1">…</div> from <div>…</div> subtree intercepts pointer events
    - retrying click action
      - waiting 100ms
    16 × waiting for element to be visible, enabled and stable
       - element is visible, enabled and stable
       - scrolling into view if needed
       - done scrolling
       - <div role="dialog" aria-modal="true" aria-label="POPUP Form" data-kl-scroll-locking-modal="true" class="needsclick  kl-private-reset-css-Xuajs1">…</div> from <div>…</div> subtree intercepts pointer events
     - retrying click action
       - waiting 500ms

- **regalrose.co.uk** [shopify_checkout_blocker_auditor] (WARN): Target URL is outside local sandbox origin; checkout audit skipped for safety
- **regalrose.co.uk** [shopify_search_discovery_auditor] (WARN): Search query missing; search audit skipped with WARN
- **rokalondon.com** [shopify_shopper] (FAIL): Shopper journey failed
- **rokalondon.com** [shopify_variant_auditor] (WARN): Variant audit completed with warnings: variant_identity, variant_price, variant_image
- **rokalondon.com** [shopify_purchase_blocker_auditor] (WARN): Option selection issue: Option selection in DOM could not be proven for all options ({})
- **rokalondon.com** [shopify_mobile_purchase_blocker_auditor] (WARN): Could not fetch product JSON from https://rokalondon.com/products/roka-london-gift-card.js
- **rokalondon.com** [shopify_cart_consistency_auditor] (WARN): Could not locate Add to Cart button in DOM
- **rokalondon.com** [shopify_checkout_blocker_auditor] (WARN): Target URL is outside local sandbox origin; checkout audit skipped for safety
- **rokalondon.com** [shopify_search_discovery_auditor] (WARN): Search query missing; search audit skipped with WARN
- **sauceshop.co** [shopify_variant_auditor] (WARN): Could not establish main product form binding on PDP
- **sauceshop.co** [shopify_purchase_blocker_auditor] (WARN): Option selection issue: Option selection in DOM could not be proven for all options ({})
- **sauceshop.co** [shopify_mobile_purchase_blocker_auditor] (WARN): Mobile option selection issue: Option selection in DOM could not be proven for all options ({})
- **sauceshop.co** [shopify_cart_consistency_auditor] (WARN): Could not locate Add to Cart button in DOM
- **sauceshop.co** [shopify_checkout_blocker_auditor] (WARN): Target URL is outside local sandbox origin; checkout audit skipped for safety
- **sauceshop.co** [shopify_search_discovery_auditor] (WARN): Search query missing; search audit skipped with WARN
- **sophieallport.com** [shopify_shopper] (WARN): Visible checkout button not found in cart
- **sophieallport.com** [shopify_variant_auditor] (WARN): Could not establish main product form binding on PDP
- **sophieallport.com** [shopify_purchase_blocker_auditor] (WARN): Main product form or Add to Cart button binding unconfirmed
- **sophieallport.com** [shopify_mobile_purchase_blocker_auditor] (WARN): Main product form or Add to Cart button binding unconfirmed on mobile
- **sophieallport.com** [shopify_cart_consistency_auditor] (WARN): Could not locate Add to Cart button in DOM
- **sophieallport.com** [shopify_checkout_blocker_auditor] (WARN): Target URL is outside local sandbox origin; checkout audit skipped for safety
- **upcirclebeauty.com** [shopify_shopper] (WARN): Could not find a product with an enabled Add to Cart button
- **upcirclebeauty.com** [shopify_variant_auditor] (WARN): Could not fetch product JSON from https://upcirclebeauty.com/products/natural-lip-balm.js
- **upcirclebeauty.com** [shopify_purchase_blocker_auditor] (WARN): Could not fetch product JSON from https://upcirclebeauty.com/products/natural-lip-balm.js
- **upcirclebeauty.com** [shopify_mobile_purchase_blocker_auditor] (WARN): Main product form or Add to Cart button binding unconfirmed on mobile
- **upcirclebeauty.com** [shopify_cart_consistency_auditor] (WARN): Could not fetch product JSON from https://upcirclebeauty.com/products/natural-lip-balm.js
- **upcirclebeauty.com** [shopify_checkout_blocker_auditor] (WARN): Target URL is outside local sandbox origin; checkout audit skipped for safety
- **upcirclebeauty.com** [shopify_search_discovery_auditor] (WARN): Search query missing; search audit skipped with WARN
