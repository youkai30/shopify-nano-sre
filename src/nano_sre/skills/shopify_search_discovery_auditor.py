"""Shopify Search Discovery Auditor Skill - Phase 7 Search & Discovery Leaks."""

import asyncio
import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from nano_sre.agent.core import Skill, SkillResult
from nano_sre.agent.privacy import Redactor

logger = logging.getLogger(__name__)

_redactor = Redactor()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_value(val: Any) -> Any:
    """Recursively redact strings, dicts, and lists without corrupting local screenshot file paths."""
    if isinstance(val, str):
        if val.startswith("reports/") or val.startswith("phase7_") or val.startswith("verification_output/") or ("/" in val and val.endswith(".png")):
            return val
        s = _redactor.redact_text(val)
        s = re.sub(r"\b[a-f0-9]{32,64}\b", "[REDACTED_TOKEN]", s, flags=re.IGNORECASE)
        return s
    if isinstance(val, list):
        return [_redact_value(item) for item in val]
    if isinstance(val, dict):
        return {k: _redact_value(v) for k, v in val.items()}
    return val


def _sanitize_url(url: str) -> str:
    """Sanitize URL to remove tokens, keys, or credentials without breaking scheme/host/path."""
    if not url:
        return ""
    parsed = urlparse(url)
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc += f":{parsed.port}"
    scheme = parsed.scheme or "http"
    path = parsed.path or "/"
    q_params = parse_qs(parsed.query)
    safe_params = {k: v for k, v in q_params.items() if k.lower() not in ("token", "key", "cart_token", "secret", "auth")}
    clean_path = re.sub(r"/[a-f0-9]{32,64}", "/[REDACTED_TOKEN]", path)
    return f"{scheme}://{netloc}{clean_path}"


def _is_local_origin(url: str) -> bool:
    """Verify that URL points strictly to a local host origin."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return host in ("127.0.0.1", "localhost", "0.0.0.0", "::1")
    except Exception:
        return False


def _extract_handle_from_url(url: str) -> Optional[str]:
    """Extract exact product handle from URL path (e.g., /products/board-other -> 'board-other')."""
    try:
        path = urlparse(url).path.rstrip("/")
        parts = [p for p in path.split("/") if p]
        if "products" in parts:
            idx = parts.index("products")
            if idx + 1 < len(parts):
                return parts[idx + 1].lower()
    except Exception:
        pass
    return None


class ShopifySearchDiscoveryAuditor(Skill):
    """Audits search and discovery journeys across search forms, catalog eligibility, result links, PDP navigation, and collection product links."""

    LOCAL_MOCK_LIMITATION = (
        "Local mock search audit verifies DOM interactions, query handling, and discovery link navigation on local fixtures, "
        "but does NOT prove performance or compatibility with live Shopify search engines or external discovery apps. "
        "Local fixture failures demonstrate technical logic state only and do NOT represent actual customer leads or monetary loss."
    )

    def name(self) -> str:
        return "shopify_search_discovery_auditor"

    async def run(self, context: dict[str, Any]) -> SkillResult:
        page = context.get("page")
        base_url: str = context.get("base_url", "")

        if not page or not base_url:
            return SkillResult(
                skill_name=self.name(),
                status="WARN",
                summary="Missing page or base_url in context",
                details=_redact_value({
                    "reason_code": "MISSING_CONTEXT",
                    "steps": ["Initialization failed: missing page or base_url"],
                    "limitation_disclaimer": self.LOCAL_MOCK_LIMITATION,
                    "checks": {},
                }),
            )

        # Enforce scope before any interaction
        allow_live_audit = context.get("allow_live_audit", False)
        if not allow_live_audit and not _is_local_origin(base_url) and not _is_local_origin(page.url or base_url):
            return SkillResult(
                skill_name=self.name(),
                status="WARN",
                summary="Target URL is outside local sandbox origin; search audit skipped for safety",
                details=_redact_value({
                    "reason_code": "NON_LOCAL_SCOPE_BLOCKED",
                    "steps": ["Local origin scope check failed"],
                    "limitation_disclaimer": self.LOCAL_MOCK_LIMITATION,
                    "checks": {},
                }),
            )

        salt = str(uuid.uuid4())[:8]

        res, run1_data = await self._run_single_audit_pass(context, page, base_url, salt)

        # 2-run local reproduction if first pass resulted in FAIL
        if res.status == "FAIL":
            logger.info("First search audit pass returned FAIL. Running second isolated reproduction pass...")
            res2_status = "UNKNOWN"
            run2_data = {}
            try:
                if hasattr(page, "context") and page.context and hasattr(page.context, "browser") and page.context.browser:
                    new_context = await page.context.browser.new_context()
                    new_page = await new_context.new_page()

                    # Guardrail route for pass 2
                    async def p2_guardrail_route(route):
                        req_url = route.request.url.lower()
                        req_path = urlparse(route.request.url).path.lower()
                        blocked_patterns = ["/cart/add", "/checkout", "/cart/update", "/cart/clear", "/account", "/challenge"]
                        if any(p in req_path for p in blocked_patterns):
                            await route.abort("blockedbyclient")
                            return
                        if not allow_live_audit:
                            req_host = (urlparse(route.request.url).hostname or "").lower()
                            if req_host and not _is_local_origin(route.request.url):
                                await route.abort("blockedbyclient")
                                return
                        await route.continue_()

                    await new_page.route("**/*", p2_guardrail_route)

                    try:
                        await new_page.goto(base_url, wait_until="commit", timeout=60000)
                        try:
                            await new_page.wait_for_load_state("networkidle", timeout=3000)
                        except Exception:
                            pass

                        ctx2 = dict(context, page=new_page)
                        res2, run2_data = await self._run_single_audit_pass(ctx2, new_page, base_url, salt)
                        res2_status = res2.status
                    finally:
                        try:
                            await new_page.unroute("**/*", p2_guardrail_route)
                        except Exception:
                            pass
                        await new_context.close()
                else:
                    res2_status = "UNRESOLVED"
            except Exception as e:
                logger.debug("Isolated search run 2 execution note: %s", e)

            if res2_status != "FAIL":
                res.status = "WARN"
                res.summary = f"Search failure not reproduced on second pass (Run 1: FAIL, Run 2: {res2_status}). Classified as WARN."
                if isinstance(res.details, dict):
                    res.details["reproduction_summary"] = {
                        "confirmed": False,
                        "run1_status": "FAIL",
                        "run2_status": res2_status,
                        "run1_data": run1_data,
                        "run2_data": run2_data,
                    }
            else:
                id1 = (
                    run1_data.get("search_query"),
                    run1_data.get("target_product_id"),
                    run1_data.get("failed_stage"),
                    run1_data.get("reason_code"),
                    run1_data.get("broken_element"),
                    run1_data.get("expected"),
                    run1_data.get("observed"),
                )
                id2 = (
                    run2_data.get("search_query"),
                    run2_data.get("target_product_id"),
                    run2_data.get("failed_stage"),
                    run2_data.get("reason_code"),
                    run2_data.get("broken_element"),
                    run2_data.get("expected"),
                    run2_data.get("observed"),
                )

                req_fields_present = all(v is not None for v in id1)

                if id1 == id2 and req_fields_present:
                    if isinstance(res.details, dict):
                        res.details["reproduction_summary"] = {
                            "confirmed": True,
                            "failed_stage": run1_data.get("failed_stage"),
                            "reason_code": run1_data.get("reason_code"),
                            "search_query": run1_data.get("search_query"),
                            "target_product_id": run1_data.get("target_product_id"),
                            "run1_data": run1_data,
                            "run2_data": run2_data,
                        }
                        # Extract lead_candidate=true strictly for confirmed FAIL blocking eligible product search/access
                        res.details["lead_candidate"] = True
                        res.details["lead_summary"] = {
                            "obstacle": run1_data.get("reason_code"),
                            "product_id": run1_data.get("target_product_id"),
                            "search_query": run1_data.get("search_query"),
                            "expected": run1_data.get("expected"),
                            "observed": run1_data.get("observed"),
                            "reproduction_steps": run1_data.get("steps", []),
                            "coverage_disclaimer": "Local fixture audit proves logic state only. Contains no financial or sales lead valuation.",
                        }
                else:
                    res.status = "WARN"
                    res.summary = f"Failure identity mismatch across reproduction runs or missing required identity details (Run 1: {run1_data.get('reason_code')}, Run 2: {run2_data.get('reason_code')}). Classified as WARN."
                    if isinstance(res.details, dict):
                        res.details["reproduction_summary"] = {
                            "confirmed": False,
                            "mismatch_reason": "Differing failure identity details or missing required identity fields across runs",
                            "run1_data": run1_data,
                            "run2_data": run2_data,
                        }

        return res

    async def _run_single_audit_pass(
        self, context: dict[str, Any], page: Any, base_url: str, salt: str
    ) -> tuple[SkillResult, dict[str, Any]]:
        steps = []
        checks: dict[str, dict[str, Any]] = {}
        pass_timestamp = _now()

        current_url = _sanitize_url(page.url or base_url)
        allow_live_audit = context.get("allow_live_audit", False)

        # Search guardrail route: Abort mutation endpoints and enforce origin scope if live audit disabled
        async def search_guardrail_route(route):
            req_url = route.request.url.lower()
            req_path = urlparse(route.request.url).path.lower()

            # Abort any cart mutation / checkout / account submission / captcha challenge endpoints
            blocked_patterns = ["/cart/add", "/checkout", "/cart/update", "/cart/clear", "/account", "/challenge"]
            if any(p in req_path for p in blocked_patterns):
                logger.warning("Active Search Guardrail: Aborted mutation endpoint request to %s", req_url)
                await route.abort("blockedbyclient")
                return

            if not allow_live_audit:
                req_host = (urlparse(route.request.url).hostname or "").lower()
                if req_host and not _is_local_origin(route.request.url):
                    logger.warning("Active Sandbox Scope Guardrail: Blocked non-local request to %s", route.request.url)
                    await route.abort("blockedbyclient")
                    return
            await route.continue_()

        await page.route("**/*", search_guardrail_route)

        try:
            try:
                await page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass

            # --- STAGE 1: Catalog Fixture & Eligibility Gate ---
            catalog_fixture = context.get("catalog_fixture") or await self._extract_catalog_fixture(page)

            if catalog_fixture:
                expected_product_id = catalog_fixture.get("product_id")
                expected_handle = catalog_fixture.get("handle")
                expected_title = catalog_fixture.get("title")
                published_status = catalog_fixture.get("published_status", catalog_fixture.get("published", False))
                search_eligible = catalog_fixture.get("eligible_for_search", catalog_fixture.get("search_eligible", False))
                search_query = context.get("search_query") or catalog_fixture.get("expected_query") or ""

                is_fixture_matching_query = (
                    search_query.lower() in (expected_title or "").lower()
                    or search_query.lower() in (expected_handle or "").lower()
                    or catalog_fixture.get("expected_query") == search_query
                )

                if not published_status or not search_eligible or not is_fixture_matching_query:
                    checks["product_eligibility"] = {
                        "status": "WARN",
                        "reason_code": "PRODUCT_NOT_ELIGIBLE_FOR_QUERY",
                        "summary": (
                            f"Product (ID={expected_product_id}) is non-published, search-ineligible, "
                            f"or does not match query '{search_query}'. Ineligible product state is classified as WARN."
                        ),
                        "expected": "Ineligible product handled safely",
                        "observed": f"published={published_status}, search_eligible={search_eligible}, query_match={is_fixture_matching_query}",
                    }
                    steps.append("Product ineligible or query non-matching; search audit completed with WARN")
                    screenshot_path = await self._take_screenshot(page, "search_ineligible_notice")
                    return SkillResult(
                        skill_name=self.name(),
                        status="WARN",
                        summary=_redact_value(f"Product ineligible or query non-matching for '{search_query}'"),
                        details=_redact_value({
                            "reason_code": "PRODUCT_NOT_ELIGIBLE_FOR_QUERY",
                            "steps": steps,
                            "limitation_disclaimer": self.LOCAL_MOCK_LIMITATION,
                            "checks": checks,
                            "redacted_screenshot": screenshot_path,
                        }),
                    ), {
                        "search_query": search_query,
                        "target_product_id": expected_product_id,
                        "failed_stage": "product_eligibility",
                        "reason_code": "PRODUCT_NOT_ELIGIBLE_FOR_QUERY",
                        "broken_element": "catalog_fixture",
                        "expected": "Ineligible product handled safely",
                        "observed": "Ineligible notice confirmed",
                        "steps": steps,
                    }

                checks["product_eligibility"] = {
                    "status": "PASS",
                    "reason_code": "NONE",
                    "summary": f"Product (ID={expected_product_id}, handle={expected_handle}) verified eligible for query '{search_query}'",
                }
            else:
                expected_product_id = None
                expected_handle = None
                expected_title = None
                published_status = None
                search_eligible = None
                search_query = context.get("search_query", "")

                checks["catalog_fixture"] = {
                    "status": "WARN",
                    "reason_code": "MISSING_CATALOG_FIXTURE",
                    "summary": "No explicit catalog fixture provided; target product identity is unproven",
                    "expected": "Explicit catalog fixture metadata",
                    "observed": "Catalog fixture absent",
                }
                checks["product_eligibility"] = {
                    "status": "WARN",
                    "reason_code": "PRODUCT_ELIGIBILITY_UNKNOWN",
                    "summary": "No catalog fixture provided; target product eligibility is UNKNOWN/WARN",
                    "expected": "Explicit catalog fixture metadata",
                    "observed": "Catalog fixture absent",
                }

            if not search_query:
                checks["catalog_fixture"] = {
                    "status": "WARN",
                    "reason_code": "MISSING_SEARCH_QUERY",
                    "summary": "No search query specified in context or fixture",
                }
                return SkillResult(
                    skill_name=self.name(),
                    status="WARN",
                    summary=_redact_value("Search query missing; search audit skipped with WARN"),
                    details=_redact_value({
                        "reason_code": "MISSING_SEARCH_QUERY",
                        "steps": steps,
                        "limitation_disclaimer": self.LOCAL_MOCK_LIMITATION,
                        "checks": checks,
                    }),
                ), {
                    "failed_stage": "catalog_fixture",
                    "reason_code": "MISSING_SEARCH_QUERY",
                    "broken_element": "search_query",
                    "expected": "Explicit search query",
                    "observed": "Missing query",
                    "steps": steps,
                }

            steps.append(f"Initiating UI search audit for query: '{search_query}'")

            # --- STAGE 2: UI Search Execution ---
            search_input, input_err = await self._find_search_input(page)

            if not search_input:
                alt_input = await self._find_alt_search_input(page)
                if alt_input:
                    search_input = alt_input
                    input_err = None
                    steps.append("Successfully opened search UI trigger and revealed search input field")

            if not search_input:
                checks["search_ui_execution"] = {
                    "status": "WARN",
                    "reason_code": "SEARCH_TRIGGER_UNRESOLVED",
                    "summary": "Unable to detect or open search UI trigger to reveal search input field",
                    "broken_element": "search_ui_trigger",
                    "expected": "Accessible search UI trigger and search input field",
                    "observed": input_err or "Search trigger or input field unresolved",
                }
            else:
                overlay_blocked = await self._check_overlay_blocked(page, search_input)
                if overlay_blocked:
                    checks["search_ui_execution"] = {
                        "status": "FAIL",
                        "reason_code": "SEARCH_INPUT_OVERLAY_BLOCKED",
                        "summary": "Search input field is obscured by an unhandled overlay or modal",
                        "broken_element": "search_input_overlay",
                        "expected": "Unobscured search input field",
                        "observed": "Search input covered by overlay element",
                    }
                else:
                    try:
                        await search_input.fill(search_query)
                    except Exception as e:
                        logger.debug("Failed to fill search field: %s", e)

                    search_btn, btn_err, btn_code = await self._find_search_submit_button(page, search_input)

                    if btn_code == "BUTTON_DISABLED":
                        checks["search_ui_execution"] = {
                            "status": "FAIL",
                            "reason_code": "SEARCH_BUTTON_DISABLED",
                            "summary": "Search submit button is disabled in DOM",
                            "broken_element": "search_submit_button",
                            "expected": "Enabled search submit button",
                            "observed": "Submit button disabled",
                        }
                    else:
                        start_results_html = await self._get_results_container_html(page)
                        start_url = page.url

                        try:
                            if search_btn and await search_btn.is_visible():
                                await search_btn.click(timeout=5000)
                            else:
                                await search_input.press("Enter", timeout=5000)

                            try:
                                await page.wait_for_load_state("networkidle", timeout=5000)
                            except Exception:
                                pass
                        except Exception as e:
                            logger.debug("Search submission exception: %s", e)

                        end_results_html = await self._get_results_container_html(page)
                        end_url = page.url

                        if page.url.startswith("chrome-error://") or "500 Internal Server Error" in await page.content():
                            checks["search_ui_execution"] = {
                                "status": "FAIL",
                                "reason_code": "SEARCH_RESPONSE_ERROR",
                                "summary": "Search request produced server error or 500 response",
                                "broken_element": "search_response",
                                "expected": "Successful search results page response",
                                "observed": f"Server error response at {_sanitize_url(page.url)}",
                            }
                        else:
                            # Wait up to 5000ms for delayed results container rendering
                            has_dom_results = await self._wait_for_results_container(page, search_query, timeout_ms=5000)
                            results_updated = has_dom_results and (
                                (start_results_html != end_results_html)
                                or (not start_results_html)
                                or (start_url != end_url)
                            )

                            if results_updated:
                                checks["search_ui_execution"] = {
                                    "status": "PASS",
                                    "reason_code": "NONE",
                                    "summary": f"Search executed and results updated successfully at {_sanitize_url(page.url)}",
                                }
                            elif start_url != end_url or "/search" in end_url.lower():
                                checks["search_ui_execution"] = {
                                    "status": "WARN",
                                    "reason_code": "SEARCH_RESULTS_UNRESOLVED",
                                    "summary": f"Search form submission navigated to {_sanitize_url(end_url)}, but dedicated search results DOM container remained unconfirmed",
                                }
                            else:
                                checks["search_ui_execution"] = {
                                    "status": "FAIL",
                                    "reason_code": "SEARCH_SUBMIT_NO_ACTION",
                                    "summary": "Submitting search form produced no URL navigation or DOM results update",
                                    "broken_element": "search_submit_action",
                                    "expected": "Search result DOM container update",
                                    "observed": "Search results container remained unrendered/unchanged",
                                }

            if checks.get("search_ui_execution", {}).get("status") in ("FAIL", "WARN"):
                exec_status = checks["search_ui_execution"]["status"]
                reason = checks["search_ui_execution"]["reason_code"]
                screenshot_path = await self._take_screenshot(page, f"search_ui_{exec_status.lower()}")
                return SkillResult(
                    skill_name=self.name(),
                    status=exec_status,
                    summary=_redact_value(f"Search UI execution status: {exec_status} ({reason})"),
                    details=_redact_value({
                        "reason_code": reason,
                        "steps": steps,
                        "limitation_disclaimer": self.LOCAL_MOCK_LIMITATION,
                        "checks": checks,
                        "redacted_screenshot": screenshot_path,
                    }),
                ), {
                    "search_query": search_query,
                    "target_product_id": expected_product_id,
                    "failed_stage": "search_ui_execution",
                    "reason_code": reason,
                    "broken_element": checks["search_ui_execution"].get("broken_element"),
                    "expected": checks["search_ui_execution"].get("expected"),
                    "observed": checks["search_ui_execution"].get("observed"),
                    "steps": steps,
                }

            # --- STAGE 3: Discovery Link Integrity & PDP Navigation ---
            steps.append("Verifying search result links and PDP navigation")

            search_results_page_url = page.url

            if not catalog_fixture:
                checks["discovery_links"] = {
                    "status": "WARN",
                    "reason_code": "GENERIC_SEARCH_NO_CATALOG_FIXTURE",
                    "summary": "Search UI execution verified; specific product result link validation requires explicit catalog fixture proof",
                }
            else:
                # Locate product result link strictly scoped inside search results grid/container
                result_link, link_err, link_code = await self._find_product_result_link(page, expected_handle, expected_title)

                if not result_link:
                    checks["discovery_links"] = {
                        "status": "FAIL",
                        "reason_code": link_code or "ELIGIBLE_PRODUCT_MISSING_FROM_RESULTS",
                        "summary": f"Eligible product (handle={expected_handle}) missing from search results for query '{search_query}'",
                        "broken_element": "search_result_grid",
                        "expected": f"Eligible product '{expected_title}' present in search results",
                        "observed": link_err or "Product link not found in search results",
                    }
                else:
                    href = await result_link.get_attribute("href") or ""
                    clean_href = href.strip()

                    if not clean_href or clean_href == "#":
                        checks["discovery_links"] = {
                            "status": "FAIL",
                            "reason_code": "BROKEN_RESULT_LINK",
                            "summary": "Search result item contains empty or broken href attribute",
                            "broken_element": "result_link_href",
                            "expected": "Valid destination URL in href",
                            "observed": f"href='{clean_href}'",
                        }
                    else:
                        pdp_nav_ok = False
                        try:
                            await result_link.click(timeout=5000)
                            try:
                                await page.wait_for_load_state("networkidle", timeout=5000)
                            except Exception:
                                pass
                            pdp_nav_ok = True
                        except Exception as e:
                            logger.debug("Result link click exception: %s", e)

                        dest_url = _sanitize_url(page.url)

                        if page.url.startswith("chrome-error://") or "404 Not Found" in await page.content():
                            checks["discovery_links"] = {
                                "status": "FAIL",
                                "reason_code": "BROKEN_RESULT_LINK_404",
                                "summary": f"Product result link navigated to 404 Not Found error page ({dest_url})",
                                "broken_element": "result_link_destination",
                                "expected": "Successful product page load",
                                "observed": f"404 Not Found at {dest_url}",
                            }
                        else:
                            # Validate exact product handle match from URL path or trusted metadata (prevent substring match or plain text match)
                            pdp_identity_ok = await self._verify_pdp_identity(page, expected_handle, expected_product_id)

                            if not pdp_identity_ok:
                                checks["discovery_links"] = {
                                    "status": "FAIL",
                                    "reason_code": "WRONG_PRODUCT_DESTINATION",
                                    "summary": f"Result link for '{expected_title}' opened a different product page ({dest_url})",
                                    "broken_element": "product_destination_identity",
                                    "expected": f"PDP with exact handle '{expected_handle}' or ID {expected_product_id}",
                                    "observed": f"Mismatch destination page at {dest_url}",
                                }
                            else:
                                checks["discovery_links"] = {
                                    "status": "PASS",
                                    "reason_code": "NONE",
                                    "summary": f"Search result link navigated successfully to valid PDP ({dest_url})",
                                }

            # --- STAGE 4: Collection Page Product Link Journey ---
            if search_results_page_url and page.url != search_results_page_url:
                try:
                    await page.goto(search_results_page_url, wait_until="commit", timeout=10000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=3000)
                    except Exception:
                        pass
                except Exception:
                    pass

            coll_check = await self._check_collection_product_link_journey(page, expected_handle, expected_product_id)
            checks["collection_links"] = coll_check

            screenshot_path = await self._take_screenshot(page, "search_discovery_summary")

            statuses = [c["status"] for c in checks.values()]
            failed_stage = None
            failed_reason_code = None

            if "FAIL" in statuses:
                overall_status = "FAIL"
                failed_stages = [k for k, v in checks.items() if v["status"] == "FAIL"]
                failed_stage = failed_stages[0]
                failed_reason_code = checks[failed_stage].get("reason_code", "FAIL")
                summary = f"Search & discovery auditor failed: {', '.join(failed_stages)}"
            elif "WARN" in statuses:
                overall_status = "WARN"
                warned_stages = [k for k, v in checks.items() if v["status"] == "WARN"]
                summary = f"Search & discovery auditor completed with warnings: {', '.join(warned_stages)}"
            else:
                overall_status = "PASS"
                summary = f"Search & discovery journey verified successfully for query '{search_query}'"

            run_meta = {
                "search_query": search_query,
                "target_product_id": expected_product_id,
                "failed_stage": failed_stage,
                "reason_code": failed_reason_code or ("NONE" if overall_status == "PASS" else "WARN"),
                "broken_element": checks.get(failed_stage, {}).get("broken_element") if failed_stage else None,
                "expected": checks.get(failed_stage, {}).get("expected"),
                "observed": checks.get(failed_stage, {}).get("observed"),
                "steps": steps,
                "timestamp": pass_timestamp,
            }

            return SkillResult(
                skill_name=self.name(),
                status=overall_status,
                summary=_redact_value(summary),
                details=_redact_value({
                    "reason_code": run_meta["reason_code"],
                    "steps": steps,
                    "limitation_disclaimer": self.LOCAL_MOCK_LIMITATION,
                    "checks": checks,
                    "redacted_screenshot": screenshot_path,
                    "pass_timestamp": pass_timestamp,
                }),
            ), run_meta

        finally:
            try:
                await page.unroute("**/*", search_guardrail_route)
            except Exception:
                pass

    async def _extract_catalog_fixture(self, page) -> Optional[dict[str, Any]]:
        """Extract catalog expectations strictly from DOM metadata script element without default hardcoded fallbacks."""
        try:
            fixture_json = await page.evaluate("""
                () => {
                    const el = document.querySelector('[data-catalog-fixture]');
                    if (el) {
                        try { return JSON.parse(el.textContent); } catch(e) {}
                    }
                    return null;
                }
            """)
            if isinstance(fixture_json, dict):
                return fixture_json
        except Exception:
            pass
        return None

    async def _find_search_input(self, page) -> tuple[Optional[Any], Optional[str]]:
        """Find visible search input field in DOM."""
        selectors = [
            'input[type="search"]:visible',
            'input[name="q"]:visible',
            'input[placeholder*="Search"]:visible',
            'input[placeholder*="search"]:visible',
            '[data-search-input]:visible',
            'input.search-box:visible',
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                return loc, None
        return None, "No visible search input field found in DOM"

    async def _find_alt_search_input(self, page) -> Optional[Any]:
        """Find alternative search input trigger or modal in DOM, click trigger if necessary, and return revealed search input."""
        try:
            triggers = page.locator('.navbar__desktop--menu__search-button:visible, [class*="search-button"]:visible, [class*="search-toggle"]:visible, [class*="search-btn"]:visible, [class*="search-icon"]:visible, button[aria-label*="search" i]:visible, summary[aria-label*="search" i]:visible, a[href*="/search"]:visible, [data-search-trigger]:visible')
            count = await triggers.count()
            for i in range(count):
                trig = triggers.nth(i)
                try:
                    if await trig.is_visible():
                        await trig.click(timeout=3000)
                        await asyncio.sleep(0.5)
                        inp = page.locator('input[type="search"]:visible, input[name="q"]:visible, input.search-box:visible').first
                        if await inp.count() > 0 and await inp.is_visible():
                            return inp
                except Exception:
                    continue
        except Exception:
            pass
        return None

    async def _find_search_submit_button(self, page, search_input) -> tuple[Optional[Any], Optional[str], Optional[str]]:
        """Find search submit button near search input."""
        try:
            form = search_input.locator('xpath=ancestor::form[1]')
            if await form.count() > 0:
                btn = form.locator('button[type="submit"]:visible, input[type="submit"]:visible, button[aria-label*="Search"]:visible').first
                if await btn.count() > 0:
                    if await btn.is_disabled():
                        return btn, "Submit button exists but is disabled", "BUTTON_DISABLED"
                    return btn, None, None
        except Exception:
            pass
        return None, None, None

    async def _check_overlay_blocked(self, page, target_loc) -> bool:
        """Check if an overlay blocks interaction with the target locator."""
        try:
            overlays = page.locator('.modal-backdrop:visible, .overlay:visible, [data-modal-overlay]:visible, .drawer-overlay:visible')
            if await overlays.count() > 0:
                is_blocked = await page.evaluate("""
                    el => {
                        const rect = el.getBoundingClientRect();
                        const topEl = document.elementFromPoint(rect.left + rect.width/2, rect.top + rect.height/2);
                        return topEl && !el.contains(topEl) && topEl !== el;
                    }
                """, await target_loc.element_handle())
                return is_blocked
        except Exception:
            pass
        return False

    async def _get_results_container_html(self, page) -> str:
        """Get HTML snippet of dedicated search results container to detect dynamic rendering changes."""
        try:
            selectors = [
                '#search-results',
                '.search-results',
                '[data-search-results]',
                '.product-grid',
                '[class*="product-results"]',
                '[class*="search-results"]',
                '.search-drawer__products',
                '.collection-grid',
            ]
            for sel in selectors:
                container = page.locator(sel).first
                if await container.count() > 0 and await container.is_visible():
                    return await container.inner_html()

            # Heading-adjacent product results container (e.g., Sauce Shop "Product Results" grid)
            heading = page.locator('h1:has-text("Product Results"), h2:has-text("Product Results"), h3:has-text("Product Results"), h1:has-text("Search Results"), h2:has-text("Search Results"), h3:has-text("Search Results")').first
            if await heading.count() > 0 and await heading.is_visible():
                sibling_grid = heading.locator('xpath=following-sibling::div[contains(@class, "grid") or contains(@class, "product")][1]').first
                if await sibling_grid.count() > 0 and await sibling_grid.is_visible():
                    return await sibling_grid.inner_html()
        except Exception:
            pass
        return ""

    async def _has_results_container(self, page, search_query: Optional[str] = None) -> bool:
        """Verify dedicated search results container is present and contains query-matching cards or explicit no-results notice."""
        try:
            selectors = [
                '#search-results',
                '.search-results',
                '[data-search-results]',
                '.product-grid',
                '[class*="product-results"]',
                '[class*="search-results"]',
                '.search-drawer__products',
                '.collection-grid',
            ]
            for sel in selectors:
                container = page.locator(sel).first
                if await container.count() > 0 and await container.is_visible():
                    txt = (await container.inner_text() or "").lower()
                    prod_count = await container.locator('a[href*="/products/"]').count()

                    # Explicit no results notice in dedicated container
                    if any(msg in txt for msg in ["no results", "no products", "nothing found", "0 results", "no matches"]):
                        return True

                    # Dedicated results container with product cards
                    if prod_count > 0:
                        return True

            # Heading-adjacent product results container (e.g., Sauce Shop "Product Results" grid)
            heading = page.locator('h1:has-text("Product Results"), h2:has-text("Product Results"), h3:has-text("Product Results"), h1:has-text("Search Results"), h2:has-text("Search Results"), h3:has-text("Search Results")').first
            if await heading.count() > 0 and await heading.is_visible():
                sibling_grid = heading.locator('xpath=following-sibling::div[contains(@class, "grid") or contains(@class, "product")][1]').first
                if await sibling_grid.count() > 0 and await sibling_grid.is_visible():
                    prod_count = await sibling_grid.locator('a[href*="/products/"]').count()
                    if prod_count > 0:
                        return True
        except Exception:
            pass
        return False

    async def _wait_for_results_container(self, page, search_query: Optional[str] = None, timeout_ms: int = 5000) -> bool:
        """Poll and wait for delayed/asynchronous search results rendering within timeout limit."""
        start_time = asyncio.get_event_loop().time()
        while (asyncio.get_event_loop().time() - start_time) * 1000 < timeout_ms:
            if await self._has_results_container(page, search_query):
                return True
            await asyncio.sleep(0.3)
        return False

    async def _find_product_result_link(
        self, page, expected_handle: Optional[str], expected_title: Optional[str]
    ) -> tuple[Optional[Any], Optional[str], Optional[str]]:
        """Find result product link strictly scoped inside confirmed search results container. Do not fall back to page."""
        results_container = page.locator('#search-results, .search-results, [data-search-results], .product-grid, .search-drawer__products').first
        if await results_container.count() == 0 or not await results_container.is_visible():
            # Check heading-adjacent container
            heading = page.locator('h1:has-text("Product Results"), h2:has-text("Product Results"), h3:has-text("Product Results"), h1:has-text("Search Results"), h2:has-text("Search Results"), h3:has-text("Search Results")').first
            if await heading.count() > 0 and await heading.is_visible():
                sibling_grid = heading.locator('xpath=following-sibling::div[contains(@class, "grid") or contains(@class, "product")][1]').first
                if await sibling_grid.count() > 0 and await sibling_grid.is_visible():
                    results_container = sibling_grid

        if await results_container.count() == 0 or not await results_container.is_visible():
            return None, "No confirmed search results container found on DOM", "SEARCH_RESULTS_UNRESOLVED"

        search_root = results_container

        if expected_handle:
            link = search_root.locator(f'a[href*="/products/{expected_handle}"]:visible').first
            if await link.count() > 0:
                # Check exact handle match from URL path
                found_href = await link.get_attribute("href") or ""
                found_handle = _extract_handle_from_url(found_href)
                if found_handle == expected_handle.lower():
                    return link, None, None

        if expected_title:
            link = search_root.locator(f'a:has-text("{expected_title}"):visible').first
            if await link.count() > 0:
                return link, None, None

        gen_link = search_root.locator('.product-card a[href*="/products/"]:visible, .grid-product a[href*="/products/"]:visible, [data-product-link]:visible').first
        if await gen_link.count() > 0:
            return gen_link, None, None

        return None, f"No product result link found for handle '{expected_handle}' or title '{expected_title}'", "ELIGIBLE_PRODUCT_MISSING_FROM_RESULTS"

    async def _verify_pdp_identity(
        self, page, expected_handle: Optional[str], expected_product_id: Optional[str]
    ) -> bool:
        """Verify destination PDP identity strictly using exact handle match in URL path or trusted JSON-LD metadata. Plain body text is NOT accepted."""
        try:
            dest_handle = _extract_handle_from_url(page.url)
            if expected_handle and dest_handle == expected_handle.lower():
                return True

            # Check trusted structured JSON metadata
            meta_json = await page.evaluate("""
                () => {
                    const scripts = document.querySelectorAll('script[type="application/ld+json"], script[id*="product"]');
                    for (const s of scripts) {
                        try {
                            const d = JSON.parse(s.textContent);
                            if (d && (d.product_id || d.sku || d.url)) return d;
                        } catch(e) {}
                    }
                    return null;
                }
            """)
            if isinstance(meta_json, dict):
                p_id = str(meta_json.get("product_id") or meta_json.get("id") or "")
                p_url = str(meta_json.get("url") or "")
                meta_handle = _extract_handle_from_url(p_url) if p_url else None

                if expected_product_id and p_id == str(expected_product_id):
                    return True
                if expected_handle and meta_handle == expected_handle.lower():
                    return True
        except Exception:
            pass
        return False

    async def _check_collection_product_link_journey(
        self, page, expected_handle: Optional[str] = None, expected_product_id: Optional[str] = None
    ) -> dict[str, Any]:
        """Test a visible product link within a collection page via actual click and destination PDP verification."""
        try:
            coll_product_link = page.locator('.collection-grid a[href*="/products/"]:visible, .collection a[href*="/products/"]:visible, [data-collection-product-link]:visible').first
            if await coll_product_link.count() == 0:
                return {
                    "status": "NOT_APPLICABLE",
                    "reason_code": "NO_COLLECTION_PRODUCT_LINKS",
                    "summary": "No collection product links present on current page view; collection journey skipped",
                }

            href = await coll_product_link.get_attribute("href") or ""
            if not href or href == "#":
                return {
                    "status": "FAIL",
                    "reason_code": "BROKEN_COLLECTION_PRODUCT_LINK",
                    "summary": f"Collection product link has broken href attribute ('{href}')",
                    "broken_element": "collection_product_link_href",
                    "expected": "Valid product destination URL in href",
                    "observed": f"href='{href}'",
                }

            try:
                await coll_product_link.click(timeout=5000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
            except Exception as e:
                return {
                    "status": "WARN",
                    "reason_code": "COLLECTION_PRODUCT_CLICK_UNPROVEN",
                    "summary": f"Collection product link click did not complete navigation: {e}",
                }

            dest_url = _sanitize_url(page.url)

            if page.url.startswith("chrome-error://") or "404 Not Found" in await page.content():
                return {
                    "status": "FAIL",
                    "reason_code": "BROKEN_COLLECTION_PRODUCT_LINK_404",
                    "summary": f"Collection product link navigated to 404 Not Found page ({dest_url})",
                    "broken_element": "collection_product_link_destination",
                    "expected": "Successful product PDP page load",
                    "observed": f"404 Not Found at {dest_url}",
                }

            # Verify destination PDP handle or identity
            pdp_identity_ok = await self._verify_pdp_identity(page, expected_handle, expected_product_id)
            dest_handle = _extract_handle_from_url(page.url)

            if pdp_identity_ok or (dest_handle and expected_handle and dest_handle == expected_handle.lower()):
                return {
                    "status": "PASS",
                    "reason_code": "NONE",
                    "summary": f"Collection product link navigated successfully to valid PDP ({dest_url})",
                }

            if dest_handle:
                return {
                    "status": "FAIL",
                    "reason_code": "WRONG_COLLECTION_PRODUCT_DESTINATION",
                    "summary": f"Collection product link navigated to wrong product page (expected '{expected_handle}', got '{dest_handle}')",
                    "broken_element": "collection_product_destination",
                    "expected": f"PDP for product handle '{expected_handle}'",
                    "observed": f"Navigated to '{dest_handle}' at {dest_url}",
                }

            return {
                "status": "WARN",
                "reason_code": "COLLECTION_PRODUCT_DESTINATION_UNPROVEN",
                "summary": f"Collection product link navigated to non-product URL ({dest_url})",
            }
        except Exception as e:
            return {
                "status": "WARN",
                "reason_code": "COLLECTION_JOURNEY_WARN",
                "summary": f"Collection product link journey note: {e}",
            }

    async def _take_screenshot(self, page, tag: str) -> Optional[str]:
        """Take screenshot with non-mutative visual CSS PII masking. Returns None if safe redaction or element scoping cannot be guaranteed."""
        os.makedirs("reports/screenshots", exist_ok=True)
        filename = f"search_discovery_{tag}_{int(datetime.now().timestamp())}.png"
        path = os.path.join("reports/screenshots", filename)

        try:
            mask_success = await page.evaluate("""
                () => {
                    try {
                        const fieldSelector = 'input[type="text"], input[type="search"], textarea, [data-pii]';
                        document.querySelectorAll(fieldSelector).forEach(el => {
                            el.dataset.origBg = el.style.backgroundColor || '';
                            el.dataset.origColor = el.style.color || '';
                            el.dataset.origFilter = el.style.filter || '';
                            el.style.backgroundColor = '#000000';
                            el.style.color = '#000000';
                            el.style.filter = 'blur(10px) brightness(0)';
                        });
                        return true;
                    } catch (e) {
                        return false;
                    }
                }
            """)

            if not mask_success:
                return None

            container = page.locator('#search-results, .search-container, .product-grid, body').first
            if await container.count() > 0 and await container.is_visible():
                await container.screenshot(path=path)
            else:
                return None

            if os.path.exists(path) and os.path.getsize(path) > 0:
                return path
            return None
        except Exception as e:
            logger.debug("Screenshot capture failed: %s", e)
            return None
        finally:
            try:
                await page.evaluate("""
                    () => {
                        document.querySelectorAll('[data-orig-bg]').forEach(el => {
                            el.style.backgroundColor = el.dataset.origBg;
                            el.style.color = el.dataset.origColor;
                            el.style.filter = el.dataset.origFilter;
                            delete el.dataset.origBg;
                            delete el.dataset.origColor;
                            delete el.dataset.origFilter;
                        });
                    }
                """)
            except Exception:
                pass
