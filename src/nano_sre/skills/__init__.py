"""Skills package for Nano-SRE monitoring capabilities."""

from nano_sre.skills.headless_probe import HeadlessProbeSkill
from nano_sre.skills.mcp_advisor import MCPAdvisor
from nano_sre.skills.pixel_auditor import PixelAuditor
from nano_sre.skills.shopify_doctor import ShopifyDoctorSkill
from nano_sre.skills.shopify_mobile_purchase_blocker_auditor import ShopifyMobilePurchaseBlockerAuditor
from nano_sre.skills.shopify_purchase_blocker_auditor import ShopifyPurchaseBlockerAuditor
from nano_sre.skills.shopify_shopper import ShopifyShopper
from nano_sre.skills.shopify_variant_auditor import ShopifyVariantAuditor
from nano_sre.skills.visual_auditor import VisualAuditor

__all__ = [
    "HeadlessProbeSkill",
    "MCPAdvisor",
    "PixelAuditor",
    "ShopifyDoctorSkill",
    "ShopifyMobilePurchaseBlockerAuditor",
    "ShopifyPurchaseBlockerAuditor",
    "ShopifyShopper",
    "ShopifyVariantAuditor",
    "VisualAuditor",
]
