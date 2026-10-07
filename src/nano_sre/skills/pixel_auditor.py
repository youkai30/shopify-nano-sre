"""Pixel Auditor Skill - Monitor and validate analytics pixels and events."""

import asyncio
import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Page, Route

from nano_sre.agent.core import Skill, SkillResult

logger = logging.getLogger(__name__)


class PixelAuditor(Skill):
    """
    Monitors analytics pixels and validates tracking events.

    Tracks:
    - page_view, view_item, add_to_cart, begin_checkout, purchase events
    - Validates payload fields (value, currency, transaction_id)
    - Checks pixel hits for Facebook, GA, TikTok
    """

    def __init__(self, mock_mode: bool = False):
        """
        Initialize the PixelAuditor.

        Args:
            mock_mode: If True, enables mock store verification mode for testing
        """
        self.mock_mode = mock_mode
        self.tracked_events: list[dict[str, Any]] = []
        self.pixel_hits: dict[str, list[dict[str, Any]]] = {
            "facebook": [],
            "google_analytics": [],
            "tiktok": [],
        }
        self.validation_errors: list[dict[str, Any]] = []

    def name(self) -> str:
        """Return the skill name."""
        return "pixel_auditor"

    async def run(self, context: dict[str, Any]) -> SkillResult:
        """
        Execute pixel auditing and return health report.

        Args:
            context: Agent context containing 'page' (Playwright Page object)

        Returns:
            SkillResult with pixel health report
        """
        page: Page | None = context.get("page")
        if not page:
            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary="No Playwright page object in context",
                error="Missing required 'page' in context",
            )

        try:
            # Reset tracking data
            self.tracked_events = []
            self.pixel_hits = {"facebook": [], "google_analytics": [], "tiktok": []}
            self.validation_errors = []

            # Hook Shopify.analytics API
            await self._inject_analytics_hook(page)

            # Intercept network requests for pixel tracking
            await page.route("**/*", self._intercept_request)

            # Mock mode: inject test events
            if self.mock_mode:
                await self._inject_mock_events(page)

            # Wait for events to be captured
            await asyncio.sleep(2)
            # Collect events captured by the injected Shopify analytics hook
            await self._collect_tracked_events(page)

            # Allow pending Playwright network interception callbacks to finish
            await asyncio.sleep(1)

            # Validate tracked events
            self._validate_events()

            # Generate health report
            return self._generate_health_report()

        except Exception as e:
            logger.exception(f"Error in pixel auditor: {e}")
            return SkillResult(
                skill_name=self.name(),
                status="FAIL",
                summary=f"Pixel auditing failed: {str(e)}",
                error=str(e),
            )

    async def _inject_analytics_hook(self, page: Page) -> None:
        """
        Inject JavaScript to hook Shopify.analytics API.

        Intercepts calls to track analytics events.
        """
        hook_script = """
        (function() {
            // Store original analytics object
            const originalAnalytics = window.Shopify?.analytics;

            // Create tracked events array
            window._pixelAuditorEvents = [];

            // Hook Shopify.analytics.publish if it exists
            if (window.Shopify && window.Shopify.analytics) {
                const originalPublish = window.Shopify.analytics.publish;

                window.Shopify.analytics.publish = function(eventName, data) {
                    // Capture event
                    window._pixelAuditorEvents.push({
                        event: eventName,
                        data: data || {},
                        timestamp: Date.now()
                    });

                    // Call original function
                    if (originalPublish) {
                        return originalPublish.call(this, eventName, data);
                    }
                };
            }

            // Also hook common analytics patterns
            ['page_view', 'view_item', 'add_to_cart', 'begin_checkout', 'purchase'].forEach(eventType => {
                const originalHandler = window[eventType];
                window[eventType] = function(data) {
                    window._pixelAuditorEvents.push({
                        event: eventType,
                        data: data || {},
                        timestamp: Date.now()
                    });
                    if (originalHandler) {
                        return originalHandler.call(this, data);
                    }
                };
            });
        })();
        """
        await page.add_init_script(hook_script)
        logger.info("Analytics hook injected")

    async def _intercept_request(self, route: Route) -> None:
        """
        Intercept network requests to detect pixel hits.

        Captures requests to Facebook Pixel, Google Analytics, TikTok Pixel.
        """
        request = route.request
        url = request.url

        # Continue the request
        await route.continue_()

        # Detect pixel hits
        parsed = urlparse(url)

        # Facebook Pixel
        if "facebook.com" in parsed.netloc and ("/tr" in parsed.path or "/pixel" in parsed.path):
            query_params = parse_qs(parsed.query)
            self.pixel_hits["facebook"].append(
                {
                    "url": url,
                    "params": query_params,
                    "timestamp": asyncio.get_event_loop().time(),
                }
            )
            logger.debug(f"Facebook pixel hit: {url}")

        # Google Analytics (GA4 or Universal)
        elif "google-analytics.com" in parsed.netloc or "analytics.google.com" in parsed.netloc:
            query_params = parse_qs(parsed.query)
            self.pixel_hits["google_analytics"].append(
                {
                    "url": url,
                    "params": query_params,
                    "timestamp": asyncio.get_event_loop().time(),
                }
            )
            logger.debug(f"Google Analytics hit: {url}")

        # TikTok Pixel
        elif "tiktok.com" in parsed.netloc and "/pixel" in parsed.path:
            query_params = parse_qs(parsed.query)
            self.pixel_hits["tiktok"].append(
                {
                    "url": url,
                    "params": query_params,
                    "timestamp": asyncio.get_event_loop().time(),
                }
            )
            logger.debug(f"TikTok pixel hit: {url}")

    async def _inject_mock_events(self, page: Page) -> None:
        """
        Inject mock analytics events for testing.

        Simulates common e-commerce events.
        """
        mock_events = [
            {
                "event": "page_view",
                "data": {"page_type": "product", "value": 49.99, "currency": "USD"},
            },
            {
                "event": "view_item",
                "data": {
                    "product_id": "12345",
                    "value": 49.99,
                    "currency": "USD",
                    "product_name": "Test Product",
                },
            },
            {
                "event": "add_to_cart",
                "data": {
                    "product_id": "12345",
                    "quantity": 1,
                    "value": 49.99,
                    "currency": "USD",
                },
            },
            {
                "event": "begin_checkout",
                "data": {"value": 49.99, "currency": "USD", "items_count": 1},
            },
            {
                "event": "purchase",
                "data": {
                    "transaction_id": "ORDER-12345",
                    "value": 49.99,
                    "currency": "USD",
                    "tax": 4.50,
                    "shipping": 5.00,
                },
            },
        ]

        for event in mock_events:
            await page.evaluate(
                f"""
                if (window._pixelAuditorEvents) {{
                    window._pixelAuditorEvents.push({json.dumps(event)});
                }}
                """
            )

        logger.info(f"Injected {len(mock_events)} mock events")

    async def _collect_tracked_events(self, page: Page) -> None:
        """Collect events from the injected hook."""
        try:
            events = await page.evaluate("window._pixelAuditorEvents || []")
            logger.info(f"Pixel hook currently contains {len(events)} events")
            self.tracked_events.extend(events)
            logger.info(f"Collected {len(events)} analytics events")
        except Exception as e:
            logger.warning(f"Failed to collect tracked events: {e}")

    def _validate_events(self) -> None:
        """
        Validate tracked analytics events.

        Checks for required fields based on event type:
        - purchase: value, currency, transaction_id
        - add_to_cart/begin_checkout: value, currency
        - view_item: value, currency (optional but recommended)
        """
        required_fields: dict[str, list[str]] = {
            "purchase": ["value", "currency", "transaction_id"],
            "add_to_cart": ["value", "currency"],
            "begin_checkout": ["value", "currency"],
            "view_item": ["value", "currency"],
            "page_view": [],
        }

        for event in self.tracked_events:
            event_name = event.get("event", "unknown")
            event_data = event.get("data", {})

            if event_name in required_fields:
                missing_fields = []
                for field in required_fields[event_name]:
                    if field not in event_data or event_data[field] is None:
                        missing_fields.append(field)

                if missing_fields:
                    self.validation_errors.append(
                        {
                            "event": event_name,
                            "error": f"Missing required fields: {', '.join(missing_fields)}",
                            "data": event_data,
                        }
                    )
                    logger.warning(f"Event {event_name} missing fields: {missing_fields}")

                # Validate currency format (should be 3-letter ISO code)
                if "currency" in event_data:
                    currency = event_data["currency"]
                    if not isinstance(currency, str) or len(currency) != 3:
                        self.validation_errors.append(
                            {
                                "event": event_name,
                                "error": f"Invalid currency format: {currency}",
                                "data": event_data,
                            }
                        )

                # Validate value is numeric
                if "value" in event_data:
                    value = event_data["value"]
                    if not isinstance(value, (int, float)):
                        self.validation_errors.append(
                            {
                                "event": event_name,
                                "error": f"Invalid value type: {type(value).__name__}",
                                "data": event_data,
                            }
                        )

    def _generate_health_report(self) -> SkillResult:
        """
        Generate pixel health report.

        Network pixel hits and browser-hook events are treated as
        separate evidence sources. A missing browser-hook event does
        not imply that analytics are absent when network activity exists.
        """
        total_events = len(self.tracked_events)
        total_errors = len(self.validation_errors)

        # Count events by type
        event_counts: dict[str, int] = {}
        for event in self.tracked_events:
            event_name = event.get("event", "unknown")
            event_counts[event_name] = event_counts.get(event_name, 0) + 1

        # Count network pixel hits by platform
        pixel_status = {
            platform: len(hits)
            for platform, hits in self.pixel_hits.items()
        }
        total_pixel_hits = sum(pixel_status.values())

        # Determine overall status
        if total_errors > 0:
            if total_events == 0:
                status = "WARN"
                summary = (
                    f"Pixel Health: {total_errors} validation errors found; "
                    f"no browser-hook events captured"
                )
            else:
                status = "WARN" if total_errors < total_events / 2 else "FAIL"
                summary = f"Pixel Health: {total_errors} validation errors found"

        elif total_events == 0 and total_pixel_hits == 0:
            status = "WARN"
            summary = (
                "Pixel Health: No browser analytics events or "
                "recognized network pixel hits detected"
            )

        elif total_events == 0 and total_pixel_hits > 0:
            status = "WARN"
            summary = (
                f"Pixel Health: {total_pixel_hits} network pixel hit(s) detected; "
                "browser event hook captured no events"
            )

        else:
            status = "PASS"
            summary = (
                f"Pixel Health: {total_events} browser event(s) captured; "
                f"{total_pixel_hits} network pixel hit(s) detected"
            )

        details = {
            "total_events": total_events,
            "event_counts": event_counts,
            "validation_errors": self.validation_errors,
            "pixel_hits": pixel_status,
            "total_pixel_hits": total_pixel_hits,
            "pixel_details": self.pixel_hits,
            "mock_mode": self.mock_mode,
        }

        logger.info(
            f"Pixel audit complete: {total_events} browser events, "
            f"{total_pixel_hits} network pixel hits, "
            f"{total_errors} validation errors, status={status}"
        )

        return SkillResult(
            skill_name=self.name(),
            status=status,
            summary=summary,
            details=details,
        )

