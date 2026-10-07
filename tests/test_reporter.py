"""Tests for the reporter module."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from nano_sre.agent.core import SkillResult
from nano_sre.agent.reporter import generate_report


@pytest.mark.asyncio
async def test_generate_report_basic():
    """Test basic report generation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        results = [
            SkillResult(
                skill_name="test_skill",
                status="PASS",
                summary="Test passed successfully",
                timestamp=datetime(2024, 1, 1, 12, 0, 0),
            )
        ]

        report_path = await generate_report(
            results=results,
            store_url="https://test.myshopify.com",
            report_dir=tmpdir,
        )

        assert Path(report_path).exists()
        assert report_path.startswith(tmpdir)
        assert "incident_report_" in report_path
        assert report_path.endswith(".md")


@pytest.mark.asyncio
async def test_generate_report_content():
    """Test report content structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        results = [
            SkillResult(
                skill_name="test_skill_1",
                status="PASS",
                summary="All checks passed",
                timestamp=datetime(2024, 1, 1, 12, 0, 0),
            ),
            SkillResult(
                skill_name="test_skill_2",
                status="WARN",
                summary="Minor issues detected",
                details={"warnings": ["Warning 1", "Warning 2"]},
                timestamp=datetime(2024, 1, 1, 12, 0, 0),
            ),
            SkillResult(
                skill_name="test_skill_3",
                status="FAIL",
                summary="Critical failure",
                error="Test error message",
                timestamp=datetime(2024, 1, 1, 12, 0, 0),
            ),
        ]

        report_path = await generate_report(
            results=results,
            store_url="https://test.myshopify.com",
            report_dir=tmpdir,
        )

        content = Path(report_path).read_text(encoding="utf-8")

        # Check header
        assert "# Incident Report" in content
        assert "https://test.myshopify.com" in content

        # Check summary table
        assert "| Skill | Status | Summary |" in content
        assert "test_skill_1" in content
        assert "test_skill_2" in content
        assert "test_skill_3" in content
        assert "✅ PASS" in content
        assert "⚠️ WARN" in content
        assert "❌ FAIL" in content

        # Check detailed findings
        assert "## Detailed Findings" in content
        assert "All checks passed" in content
        assert "Minor issues detected" in content
        assert "Critical failure" in content

        # Check error section
        assert "Test error message" in content

        # Check recommended actions
        assert "## Recommended Actions" in content


@pytest.mark.asyncio
async def test_generate_report_with_screenshots():
    """Test report generation with screenshots."""
    with tempfile.TemporaryDirectory() as tmpdir:
        results = [
            SkillResult(
                skill_name="visual_test",
                status="PASS",
                summary="Visual checks passed",
                screenshots=["screenshot1.png", "screenshot2.png"],
                timestamp=datetime(2024, 1, 1, 12, 0, 0),
            )
        ]

        report_path = await generate_report(
            results=results,
            store_url="https://test.myshopify.com",
            report_dir=tmpdir,
        )

        content = Path(report_path).read_text(encoding="utf-8")

        assert "**Screenshots:**" in content
        assert "screenshot1.png" in content
        assert "screenshot2.png" in content


@pytest.mark.asyncio
async def test_generate_report_with_ai_diagnosis():
    """Test report generation with AI diagnosis."""
    with tempfile.TemporaryDirectory() as tmpdir:
        results = [
            SkillResult(
                skill_name="test_skill",
                status="FAIL",
                summary="Test failed",
                timestamp=datetime(2024, 1, 1, 12, 0, 0),
            )
        ]

        ai_diagnosis = "The failure appears to be caused by a network timeout. Recommend increasing timeout values."

        report_path = await generate_report(
            results=results,
            store_url="https://test.myshopify.com",
            report_dir=tmpdir,
            llm_configured=True,
            ai_diagnosis=ai_diagnosis,
        )

        content = Path(report_path).read_text(encoding="utf-8")

        assert "## AI Diagnosis" in content
        assert ai_diagnosis in content


@pytest.mark.asyncio
async def test_generate_report_no_ai_diagnosis_when_not_configured():
    """Test that AI diagnosis is not included when LLM is not configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        results = [
            SkillResult(
                skill_name="test_skill",
                status="FAIL",
                summary="Test failed",
                timestamp=datetime(2024, 1, 1, 12, 0, 0),
            )
        ]

        report_path = await generate_report(
            results=results,
            store_url="https://test.myshopify.com",
            report_dir=tmpdir,
            llm_configured=False,
            ai_diagnosis="This should not appear",
        )

        content = Path(report_path).read_text(encoding="utf-8")

        assert "## AI Diagnosis" not in content
        assert "This should not appear" not in content


@pytest.mark.asyncio
async def test_generate_report_with_details():
    """Test report generation with complex details."""
    with tempfile.TemporaryDirectory() as tmpdir:
        results = [
            SkillResult(
                skill_name="headless_probe",
                status="WARN",
                summary="Issues detected",
                details={
                    "rate_limit_issues": [
                        {"url": "https://api.test.com", "status": 429}
                    ],
                    "hydration_mismatches": [
                        {"type": "warning", "message": "Hydration mismatch detected"}
                    ],
                    "stale_data_issues": [
                        {"product_handle": "test-product", "dom_price": "$10.00", "api_price": "$12.00"}
                    ],
                },
                timestamp=datetime(2024, 1, 1, 12, 0, 0),
            )
        ]

        report_path = await generate_report(
            results=results,
            store_url="https://test.myshopify.com",
            report_dir=tmpdir,
        )

        content = Path(report_path).read_text(encoding="utf-8")

        # Check that details are included
        assert "rate_limit_issues" in content
        assert "hydration_mismatches" in content
        assert "stale_data_issues" in content

        # Check recommended actions based on specific issues
        assert "rate limit backoff" in content.lower()
        assert "hydration" in content.lower()
        assert "cache invalidation" in content.lower() or "isr" in content.lower()


@pytest.mark.asyncio
async def test_generate_report_statistics():
    """Test that report includes correct statistics."""
    with tempfile.TemporaryDirectory() as tmpdir:
        results = [
            SkillResult(
                skill_name="skill_1",
                status="PASS",
                summary="Passed",
                timestamp=datetime(2024, 1, 1, 12, 0, 0),
            ),
            SkillResult(
                skill_name="skill_2",
                status="PASS",
                summary="Passed",
                timestamp=datetime(2024, 1, 1, 12, 0, 0),
            ),
            SkillResult(
                skill_name="skill_3",
                status="WARN",
                summary="Warning",
                timestamp=datetime(2024, 1, 1, 12, 0, 0),
            ),
            SkillResult(
                skill_name="skill_4",
                status="FAIL",
                summary="Failed",
                timestamp=datetime(2024, 1, 1, 12, 0, 0),
            ),
        ]

        report_path = await generate_report(
            results=results,
            store_url="https://test.myshopify.com",
            report_dir=tmpdir,
        )

        content = Path(report_path).read_text(encoding="utf-8")

        # Check statistics
        assert "**Total Skills:** 4" in content
        assert "**Passed:** 2" in content
        assert "**Warnings:** 1" in content
        assert "**Failed:** 1" in content


@pytest.mark.asyncio
async def test_generate_report_creates_directory():
    """Test that report generation creates the directory if it doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        report_dir = Path(tmpdir) / "nested" / "reports"
        results = [
            SkillResult(
                skill_name="test_skill",
                status="PASS",
                summary="Test passed",
                timestamp=datetime(2024, 1, 1, 12, 0, 0),
            )
        ]

        report_path = await generate_report(
            results=results,
            store_url="https://test.myshopify.com",
            report_dir=str(report_dir),
        )

        assert Path(report_path).exists()
        assert report_dir.exists()
