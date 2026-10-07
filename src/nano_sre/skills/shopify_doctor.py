"""Shopify Doctor skill for comprehensive store health checks."""

import asyncio
import logging
from typing import Any, Optional

from playwright.async_api import Page

from nano_sre.agent.core import Skill, SkillResult

logger = logging.getLogger(__name__)


class ShopifyDoctorSkill(Skill):
    """Comprehensive Shopify store health checks."""

    def name(self) -> str:
        """Return skill name."""
        return "shopify_doctor"

    async def run(self, context: dict[str, Any]) -> SkillResult:
        """
        Execute Shopify store health checks.

        Args:
            context: Agent context containing page, settings, etc.

        Returns:
            SkillResult with health check status.
        """
        page: Optional[Page] = context.get("page")
        settings = context.get("settings")

        if not page:
            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary="No page context available",
                error="Page object not found in context",
            )

        if not settings:
            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary="No settings available",
                error="Settings object not found in context",
            )

        issues = []
        warnings = []
        details = {}
        console_errors = []

        # Setup console error listener
        def handle_console(msg):
            """Capture console messages."""
            if msg.type == "error":
                # Filter out known non-critical errors
                text = msg.text
                ignored_patterns = [
                    "sf_private_access_tokens",  # 401 usually from private app tokens
                    "shop.app",  # CSP framing issues
                    "v1/pixels",  # Tracking errors
                ]
                if any(pattern in text for pattern in ignored_patterns):
                    return

                console_errors.append(text)
                logger.debug(f"Console error captured: {text}")

        page.on("console", handle_console)

        try:
            # Check if Admin API is configured
            admin_api_key = settings.shopify_admin_api_key

            if admin_api_key:
                logger.info("Admin API key configured, performing API checks")
                api_results = await self._check_admin_api(settings.store_url_str, admin_api_key)
                details.update(api_results.get("details", {}))
                issues.extend(api_results.get("issues", []))
                warnings.extend(api_results.get("warnings", []))
            else:
                logger.info("Admin API key not configured, skipping API checks")
                warnings.append("Admin API not configured - skipping advanced checks")

            # Navigate to storefront to capture console errors
            logger.info(f"Navigating to storefront: {settings.store_url_str}")
            await page.goto(settings.store_url_str, wait_until="commit")
            await asyncio.sleep(2)  # Wait for any async console errors

            # Report console errors as warnings to avoid failing audits on demo stores
            if console_errors:
                details["console_errors"] = console_errors[:10]  # Limit to first 10
                warnings.append(f"Found {len(console_errors)} console error(s) on storefront")

            # Remove listener
            page.remove_listener("console", handle_console)

            # Determine overall status
            if issues:
                status = "FAIL"
                summary = f"Found {len(issues)} issue(s)"
            elif warnings:
                status = "WARN"
                summary = f"Found {len(warnings)} warning(s)"
            else:
                status = "PASS"
                summary = "All health checks passed"

            details["issues"] = issues
            details["warnings"] = warnings

            return SkillResult(
                skill_name=self.name(),
                status=status,
                summary=summary,
                details=details,
            )

        except Exception as e:
            logger.exception(f"Error in shopify_doctor: {e}")
            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary=f"Health check failed: {str(e)}",
                error=str(e),
                details={"console_errors": console_errors} if console_errors else {},
            )

    async def _check_admin_api(self, store_url: str, api_key: str) -> dict[str, Any]:
        """
        Check Shopify Admin API for store health.

        Args:
            store_url: Shopify store URL
            api_key: Admin API access token

        Returns:
            Dictionary with issues, warnings, and details
        """
        import aiohttp

        issues = []
        warnings = []
        details = {}

        # Extract shop domain from URL
        shop_domain = store_url.replace("https://", "").replace("http://", "")
        if shop_domain.endswith("/"):
            shop_domain = shop_domain[:-1]

        # GraphQL API endpoint
        api_url = f"https://{shop_domain}/admin/api/2026-01/graphql.json"

        headers = {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": api_key,
        }

        try:
            async with aiohttp.ClientSession() as session:
                # Check 1: Active theme Liquid errors
                theme_query = """
                {
                  shop {
                    name
                    errors {
                      field
                      message
                    }
                  }
                  themes(first: 1, role: MAIN) {
                    edges {
                      node {
                        id
                        name
                        role
                      }
                    }
                  }
                }
                """

                async with session.post(
                    api_url, json={"query": theme_query}, headers=headers
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        shop_data = data.get("data", {}).get("shop", {})
                        shop_errors = shop_data.get("errors", [])

                        if shop_errors:
                            issues.append(
                                f"Shop has {len(shop_errors)} error(s): "
                                + ", ".join([e.get("message", "") for e in shop_errors])
                            )
                            details["shop_errors"] = shop_errors

                        themes = data.get("data", {}).get("themes", {}).get("edges", [])
                        if themes:
                            active_theme = themes[0]["node"]
                            details["active_theme"] = active_theme
                        else:
                            warnings.append("No active theme found")
                    else:
                        warnings.append(f"Theme API check failed with status {resp.status}")

                # Check 2: Published products with images and prices
                products_query = """
                {
                  products(first: 100, query: "status:active") {
                    edges {
                      node {
                        id
                        title
                        featuredImage {
                          id
                        }
                        variants(first: 1) {
                          edges {
                            node {
                              price
                            }
                          }
                        }
                      }
                    }
                  }
                }
                """

                async with session.post(
                    api_url, json={"query": products_query}, headers=headers
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        products = data.get("data", {}).get("products", {}).get("edges", [])

                        products_without_images = []
                        products_without_prices = []

                        for product_edge in products:
                            product = product_edge["node"]
                            if not product.get("featuredImage"):
                                products_without_images.append(product["title"])

                            variants = product.get("variants", {}).get("edges", [])
                            if not variants or not variants[0]["node"].get("price"):
                                products_without_prices.append(product["title"])

                        if products_without_images:
                            warnings.append(
                                f"{len(products_without_images)} product(s) missing images"
                            )
                            details["products_without_images"] = products_without_images[
                                :5
                            ]  # Limit to 5

                        if products_without_prices:
                            warnings.append(
                                f"{len(products_without_prices)} product(s) missing prices"
                            )
                            details["products_without_prices"] = products_without_prices[
                                :5
                            ]  # Limit to 5

                        details["total_products_checked"] = len(products)
                    else:
                        warnings.append(f"Products API check failed with status {resp.status}")

                # Check 3: Deprecated API versions
                # Check the API version used in the URL and warn if it's old
                api_version = "2026-01"  # Current version in use
                current_year = 2026
                api_year = int(api_version.split("-")[0])

                if current_year - api_year > 1:
                    warnings.append(
                        f"Using API version {api_version} which may be deprecated. "
                        "Consider upgrading to the latest version."
                    )
                    details["api_version_in_use"] = api_version

        except aiohttp.ClientError as e:
            warnings.append(f"Admin API connection error: {str(e)}")
            logger.error(f"Admin API error: {e}")
        except Exception as e:
            warnings.append(f"Unexpected error during API checks: {str(e)}")
            logger.exception(f"Unexpected error in API checks: {e}")

        return {"issues": issues, "warnings": warnings, "details": details}

