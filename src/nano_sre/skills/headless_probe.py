"""Headless storefront monitoring skill for Shopify Hydrogen/Next.js."""

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from playwright.async_api import Page, Response

from nano_sre.agent.core import Skill, SkillResult

logger = logging.getLogger(__name__)


class HeadlessProbeSkill(Skill):
    """
    Monitors headless Shopify storefronts for:
    - Storefront API rate-limit detection (429 responses + retry headers)
    - Hydration mismatch detection (React/Next.js warnings in console)
    - ISR staleness check (compare API endpoints vs DOM prices)
    """

    def name(self) -> str:
        """Return the skill name."""
        return "headless_probe"

    async def run(self, context: dict[str, Any]) -> SkillResult:
        """
        Execute headless storefront checks.

        Args:
            context: Must contain:
                - page: Playwright Page instance
                - url: Target storefront URL
                - storefront_api_url: Optional Storefront API endpoint
                - storefront_access_token: Optional Storefront API access token

        Returns:
            SkillResult with PASS/WARN/FAIL status and details.
        """
        page = context.get("page")
        url = context.get("url")

        if not page or not url:
            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary="Missing required context: page and url",
                error="page and url are required in context",
            )

        details: dict[str, Any] = {
            "rate_limit_issues": [],
            "hydration_mismatches": [],
            "stale_data_issues": [],
            "console_warnings": [],
        }
        issues_found = []

        # Setup console and network listeners
        console_logs: list[dict[str, Any]] = []
        api_responses: list[dict[str, Any]] = []

        async def handle_console(msg):
            """Capture console messages."""
            console_logs.append(
                {
                    "type": msg.type,
                    "text": msg.text,
                    "location": msg.location,
                }
            )

        async def handle_response(response: Response):
            """Capture API responses, especially Storefront API calls."""
            url = response.url
            status = response.status
            headers = await response.all_headers()

            # Track Storefront API calls
            if "/api/" in url or "storefront" in url.lower():
                api_responses.append(
                    {
                        "url": url,
                        "status": status,
                        "headers": headers,
                    }
                )

        page.on("console", handle_console)
        page.on("response", handle_response)

        try:
            # Navigate to the target URL
            logger.info(f"Navigating to {url}")
            await page.goto(url, wait_until="commit", timeout=60000)

            # Wait for hydration to complete
            await asyncio.sleep(2)

            # Check 1: Rate-limit detection
            rate_limit_results = await self._check_rate_limits(api_responses)
            if rate_limit_results:
                details["rate_limit_issues"] = rate_limit_results
                issues_found.append(f"Rate limit issues: {len(rate_limit_results)}")

            # Check 2: Hydration mismatch detection
            hydration_results = self._check_hydration_mismatches(console_logs)
            if hydration_results:
                details["hydration_mismatches"] = hydration_results
                issues_found.append(f"Hydration mismatches: {len(hydration_results)}")

            # Check 3: ISR staleness check (compare API vs DOM for product prices)
            staleness_results = await self._check_isr_staleness(page, context)
            if staleness_results:
                details["stale_data_issues"] = staleness_results
                issues_found.append(f"Stale data: {len(staleness_results)}")

            # Capture all console warnings for reference
            details["console_warnings"] = [
                log for log in console_logs if log["type"] in ["warning", "error"]
            ]

            # Determine overall status
            if details["rate_limit_issues"]:
                status = "FAIL"
                summary = f"Rate-limit issues detected: {'; '.join(issues_found)}"
            elif details["hydration_mismatches"] or details["stale_data_issues"]:
                status = "WARN"
                summary = f"Issues detected: {'; '.join(issues_found)}"
            elif details["console_warnings"]:
                status = "WARN"
                summary = f"Console warnings detected: {len(details['console_warnings'])}"
            else:
                status = "PASS"
                summary = "All headless checks passed"

            return SkillResult(
                skill_name=self.name(),
                status=status,
                summary=summary,
                details=details,
            )

        except Exception as e:
            logger.exception(f"Error in headless probe: {e}")
            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary=f"Headless probe failed: {str(e)}",
                error=str(e),
                details=details,
            )
        finally:
            # Cleanup listeners
            page.remove_listener("console", handle_console)
            page.remove_listener("response", handle_response)

    async def _check_rate_limits(self, api_responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Check for rate-limit responses (429) and retry headers.

        Args:
            api_responses: List of captured API responses

        Returns:
            List of rate-limit issues found
        """
        issues = []
        for response in api_responses:
            status = response["status"]
            headers = response["headers"]
            url = response["url"]

            if status == 429:
                retry_after = headers.get("retry-after", "unknown")
                issues.append(
                    {
                        "url": url,
                        "status": status,
                        "retry_after": retry_after,
                        "issue": "Rate limit exceeded (429)",
                    }
                )
                logger.warning(f"Rate limit detected on {url}, retry after: {retry_after}")

            # Also check for X-Shopify-Shop-Api-Call-Limit header
            if "x-shopify-shop-api-call-limit" in headers:
                call_limit = headers["x-shopify-shop-api-call-limit"]
                # Parse format like "32/40" to check if close to limit
                if "/" in call_limit:
                    try:
                        current, maximum = map(int, call_limit.split("/"))
                        usage_percent = (current / maximum) * 100
                        if usage_percent >= 90:
                            issues.append(
                                {
                                    "url": url,
                                    "call_limit": call_limit,
                                    "usage_percent": f"{usage_percent:.1f}%",
                                    "issue": "Approaching rate limit threshold",
                                }
                            )
                    except ValueError:
                        pass

        return issues

    def _check_hydration_mismatches(
        self, console_logs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Check for React/Next.js hydration mismatch warnings.

        Args:
            console_logs: List of captured console messages

        Returns:
            List of hydration mismatch issues found
        """
        issues = []
        hydration_patterns = [
            r"hydration",
            r"did not match",
            r"server.*client.*mismatch",
            r"suppressHydrationWarning",
            r"Text content does not match",
            r"Hydration failed",
            r"There was an error while hydrating",
        ]

        for log in console_logs:
            if log["type"] in ["warning", "error"]:
                text = log["text"].lower()
                for pattern in hydration_patterns:
                    if re.search(pattern, text, re.IGNORECASE):
                        issues.append(
                            {
                                "type": log["type"],
                                "message": log["text"],
                                "location": log.get("location", {}),
                                "issue": "Hydration mismatch detected",
                            }
                        )
                        logger.warning(f"Hydration mismatch: {log['text'][:100]}")
                        break

        return issues

    async def _check_isr_staleness(
        self, page: Page, context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """
        Check for ISR staleness by comparing API data vs DOM content.
        Focuses on product prices as a common indicator.

        Args:
            page: Playwright Page instance
            context: Agent context with optional API credentials

        Returns:
            List of staleness issues found
        """
        issues: list[dict[str, str]] = []
        current_url = page.url

        # Only check if we're on a product page
        if "/products/" not in current_url:
            return issues

        try:
            # Extract product handle from URL
            parsed_url = urlparse(current_url)
            path_parts = parsed_url.path.split("/")
            if "products" in path_parts:
                product_index = path_parts.index("products")
                if len(path_parts) > product_index + 1:
                    product_handle = path_parts[product_index + 1]

                    # Get price from DOM
                    dom_price = await self._extract_price_from_dom(page)

                    # Get price from API
                    api_price = await self._fetch_product_price_from_api(product_handle, context)

                    if dom_price and api_price:
                        # Compare prices (allow small floating point differences)
                        if abs(dom_price - api_price) > 0.01:
                            issues.append(
                                {
                                    "product_handle": product_handle,
                                    "dom_price": f"${dom_price:.2f}",
                                    "api_price": f"${api_price:.2f}",
                                    "difference": f"${abs(dom_price - api_price):.2f}",
                                    "issue": "Price mismatch between DOM and API (possible stale ISR)",
                                }
                            )
                            logger.warning(
                                f"Price mismatch for {product_handle}: "
                                f"DOM=${dom_price:.2f} vs API=${api_price:.2f}"
                            )

        except Exception as e:
            logger.error(f"Error checking ISR staleness: {e}")

        return issues

    async def _extract_price_from_dom(self, page: Page) -> float | None:
        """Extract product price from DOM."""
        try:
            # Common price selectors for Shopify themes
            price_selectors = [
                "[data-price]",
                ".price",
                '[class*="price"]',
                '[data-testid="price"]',
                'meta[property="product:price:amount"]',
            ]

            for selector in price_selectors:
                element = page.locator(selector).first
                if await element.count() > 0:
                    # Try data attribute first
                    price_value = await element.get_attribute("data-price")
                    if not price_value and selector.startswith("meta"):
                        price_value = await element.get_attribute("content")
                    if not price_value:
                        price_value = await element.inner_text()

                    if price_value:
                        # Extract numeric value
                        price_match = re.search(r"[\d,]+\.?\d*", price_value.replace(",", ""))
                        if price_match:
                            return float(price_match.group())

        except Exception as e:
            logger.debug(f"Could not extract price from DOM: {e}")

        return None

    async def _fetch_product_price_from_api(
        self, product_handle: str, context: dict[str, Any]
    ) -> float | None:
        """Fetch product price from Storefront API."""
        api_url = context.get("storefront_api_url")
        access_token = context.get("storefront_access_token")

        if not api_url or not access_token:
            logger.debug("Storefront API credentials not provided, skipping API check")
            return None

        try:
            query = """
            query getProduct($handle: String!) {
              product(handle: $handle) {
                priceRange {
                  minVariantPrice {
                    amount
                  }
                }
              }
            }
            """

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    api_url,
                    json={"query": query, "variables": {"handle": product_handle}},
                    headers={
                        "X-Shopify-Storefront-Access-Token": access_token,
                        "Content-Type": "application/json",
                    },
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    product = data.get("data", {}).get("product")
                    if product:
                        amount = (
                            product.get("priceRange", {}).get("minVariantPrice", {}).get("amount")
                        )
                        if amount:
                            return float(amount)

        except Exception as e:
            logger.debug(f"Could not fetch price from API: {e}")

        return None

