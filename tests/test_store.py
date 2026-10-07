"""Tests for the SQLite-based store module."""

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from nano_sre.db.store import Store


@pytest.fixture
def in_memory_store():
    """Create an in-memory SQLite database for testing."""
    # Use :memory: for in-memory database
    store = Store(":memory:")
    return store


@pytest.fixture
def temp_store():
    """Create a temporary file-based SQLite database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name

    store = Store(tmp_path)
    try:
        yield store
    finally:
        store.db.close()
        Path(tmp_path).unlink(missing_ok=True)


class TestStoreInitialization:
    """Test store initialization and table creation."""

    def test_store_creation_in_memory(self, in_memory_store):
        """Test creating a store with in-memory database."""
        assert in_memory_store is not None
        assert in_memory_store.db_path == Path(":memory:")
        assert in_memory_store.db is not None

    def test_store_creation_with_file(self, temp_store):
        """Test creating a store with file-based database."""
        assert temp_store is not None
        assert temp_store.db is not None
        assert temp_store.db_path.exists()

    def test_check_runs_table_created(self, in_memory_store):
        """Test that check_runs table is created."""
        assert "check_runs" in in_memory_store.db.table_names()

    def test_baselines_table_created(self, in_memory_store):
        """Test that baselines table is created."""
        assert "baselines" in in_memory_store.db.table_names()

    def test_incidents_table_created(self, in_memory_store):
        """Test that incidents table is created."""
        assert "incidents" in in_memory_store.db.table_names()

    def test_check_runs_table_schema(self, in_memory_store):
        """Test check_runs table has correct schema."""
        table = in_memory_store.db["check_runs"]
        columns = {col.name for col in table.columns}

        expected_columns = {
            "id", "timestamp", "store_url", "skill_name",
            "status", "summary", "details", "screenshots", "error"
        }
        assert columns == expected_columns

    def test_baselines_table_schema(self, in_memory_store):
        """Test baselines table has correct schema."""
        table = in_memory_store.db["baselines"]
        columns = {col.name for col in table.columns}

        expected_columns = {
            "id", "skill_name", "store_url", "baseline_data",
            "created_at", "updated_at"
        }
        assert columns == expected_columns

    def test_incidents_table_schema(self, in_memory_store):
        """Test incidents table has correct schema."""
        table = in_memory_store.db["incidents"]
        columns = {col.name for col in table.columns}

        expected_columns = {
            "id", "created_at", "resolved_at", "store_url",
            "skill_name", "severity", "title", "details", "status"
        }
        assert columns == expected_columns

    def test_check_runs_index_created(self, in_memory_store):
        """Test that check_runs table has proper indexes."""
        indexes = in_memory_store.db["check_runs"].indexes
        index_names = [idx.name for idx in indexes]
        assert "idx_check_runs" in index_names

    def test_baselines_index_created(self, in_memory_store):
        """Test that baselines table has proper indexes."""
        indexes = in_memory_store.db["baselines"].indexes
        index_names = [idx.name for idx in indexes]
        assert "idx_baselines" in index_names

    def test_incidents_index_created(self, in_memory_store):
        """Test that incidents table has proper indexes."""
        indexes = in_memory_store.db["incidents"].indexes
        index_names = [idx.name for idx in indexes]
        assert "idx_incidents" in index_names


class TestRecordCheck:
    """Test record_check method."""

    def test_record_check_basic(self, in_memory_store):
        """Test basic check run recording."""
        check_id = in_memory_store.record_check(
            store_url="https://test.myshopify.com",
            skill_name="test_skill",
            status="PASS",
            summary="Test passed successfully",
        )

        assert isinstance(check_id, int)
        assert check_id > 0

    def test_record_check_with_details(self, in_memory_store):
        """Test check run recording with details."""
        details = {"key": "value", "nested": {"data": 123}}

        check_id = in_memory_store.record_check(
            store_url="https://test.myshopify.com",
            skill_name="test_skill",
            status="PASS",
            summary="Test passed",
            details=details,
        )

        # Retrieve and verify
        rows = list(in_memory_store.db["check_runs"].rows_where("id = ?", [check_id]))
        assert len(rows) == 1

        row = rows[0]
        assert json.loads(row["details"]) == details

    def test_record_check_with_screenshots(self, in_memory_store):
        """Test check run recording with screenshots."""
        screenshots = ["screenshot1.png", "screenshot2.png"]

        check_id = in_memory_store.record_check(
            store_url="https://test.myshopify.com",
            skill_name="test_skill",
            status="PASS",
            summary="Test passed",
            screenshots=screenshots,
        )

        # Retrieve and verify
        rows = list(in_memory_store.db["check_runs"].rows_where("id = ?", [check_id]))
        assert len(rows) == 1

        row = rows[0]
        assert json.loads(row["screenshots"]) == screenshots

    def test_record_check_with_error(self, in_memory_store):
        """Test check run recording with error."""
        error_msg = "Test failed with error"

        check_id = in_memory_store.record_check(
            store_url="https://test.myshopify.com",
            skill_name="test_skill",
            status="FAIL",
            summary="Test failed",
            error=error_msg,
        )

        # Retrieve and verify
        rows = list(in_memory_store.db["check_runs"].rows_where("id = ?", [check_id]))
        assert len(rows) == 1

        row = rows[0]
        assert row["error"] == error_msg
        assert row["status"] == "FAIL"

    def test_record_check_multiple(self, in_memory_store):
        """Test recording multiple check runs."""
        id1 = in_memory_store.record_check(
            store_url="https://test1.myshopify.com",
            skill_name="skill1",
            status="PASS",
            summary="Test 1 passed",
        )

        id2 = in_memory_store.record_check(
            store_url="https://test2.myshopify.com",
            skill_name="skill2",
            status="FAIL",
            summary="Test 2 failed",
        )

        assert id1 != id2
        assert id1 > 0
        assert id2 > 0

    def test_record_check_timestamp_format(self, in_memory_store):
        """Test that timestamp is stored in ISO format."""
        check_id = in_memory_store.record_check(
            store_url="https://test.myshopify.com",
            skill_name="test_skill",
            status="PASS",
            summary="Test passed",
        )

        rows = list(in_memory_store.db["check_runs"].rows_where("id = ?", [check_id]))
        timestamp_str = rows[0]["timestamp"]

        # Should be parseable as ISO format
        timestamp = datetime.fromisoformat(timestamp_str)
        assert isinstance(timestamp, datetime)


class TestGetLatestBaseline:
    """Test get_latest_baseline method."""

    def test_get_latest_baseline_not_found(self, in_memory_store):
        """Test getting baseline when none exists."""
        baseline = in_memory_store.get_latest_baseline(
            skill_name="test_skill",
            store_url="https://test.myshopify.com",
        )

        assert baseline is None

    def test_get_latest_baseline_found(self, in_memory_store):
        """Test getting baseline after it's created."""
        baseline_data = {"key": "value", "data": 123}

        in_memory_store.update_baseline(
            skill_name="test_skill",
            store_url="https://test.myshopify.com",
            baseline_data=baseline_data,
        )

        retrieved = in_memory_store.get_latest_baseline(
            skill_name="test_skill",
            store_url="https://test.myshopify.com",
        )

        assert retrieved == baseline_data

    def test_get_latest_baseline_multiple_updates(self, in_memory_store):
        """Test that get_latest_baseline returns most recent."""
        baseline_v1 = {"version": 1}
        baseline_v2 = {"version": 2}

        in_memory_store.update_baseline(
            skill_name="test_skill",
            store_url="https://test.myshopify.com",
            baseline_data=baseline_v1,
        )

        in_memory_store.update_baseline(
            skill_name="test_skill",
            store_url="https://test.myshopify.com",
            baseline_data=baseline_v2,
        )

        retrieved = in_memory_store.get_latest_baseline(
            skill_name="test_skill",
            store_url="https://test.myshopify.com",
        )

        assert retrieved == baseline_v2

    def test_get_latest_baseline_different_skills(self, in_memory_store):
        """Test that baselines are isolated by skill name."""
        baseline_skill1 = {"skill": 1}
        baseline_skill2 = {"skill": 2}

        in_memory_store.update_baseline(
            skill_name="skill1",
            store_url="https://test.myshopify.com",
            baseline_data=baseline_skill1,
        )

        in_memory_store.update_baseline(
            skill_name="skill2",
            store_url="https://test.myshopify.com",
            baseline_data=baseline_skill2,
        )

        retrieved1 = in_memory_store.get_latest_baseline(
            skill_name="skill1",
            store_url="https://test.myshopify.com",
        )

        retrieved2 = in_memory_store.get_latest_baseline(
            skill_name="skill2",
            store_url="https://test.myshopify.com",
        )

        assert retrieved1 == baseline_skill1
        assert retrieved2 == baseline_skill2

    def test_get_latest_baseline_different_stores(self, in_memory_store):
        """Test that baselines are isolated by store URL."""
        baseline_store1 = {"store": 1}
        baseline_store2 = {"store": 2}

        in_memory_store.update_baseline(
            skill_name="test_skill",
            store_url="https://store1.myshopify.com",
            baseline_data=baseline_store1,
        )

        in_memory_store.update_baseline(
            skill_name="test_skill",
            store_url="https://store2.myshopify.com",
            baseline_data=baseline_store2,
        )

        retrieved1 = in_memory_store.get_latest_baseline(
            skill_name="test_skill",
            store_url="https://store1.myshopify.com",
        )

        retrieved2 = in_memory_store.get_latest_baseline(
            skill_name="test_skill",
            store_url="https://store2.myshopify.com",
        )

        assert retrieved1 == baseline_store1
        assert retrieved2 == baseline_store2


class TestUpdateBaseline:
    """Test update_baseline method."""

    def test_update_baseline_new(self, in_memory_store):
        """Test creating a new baseline."""
        baseline_data = {"test": "data"}

        baseline_id = in_memory_store.update_baseline(
            skill_name="test_skill",
            store_url="https://test.myshopify.com",
            baseline_data=baseline_data,
        )

        assert isinstance(baseline_id, int)
        assert baseline_id > 0

    def test_update_baseline_existing(self, in_memory_store):
        """Test updating an existing baseline."""
        baseline_v1 = {"version": 1}
        baseline_v2 = {"version": 2}

        id1 = in_memory_store.update_baseline(
            skill_name="test_skill",
            store_url="https://test.myshopify.com",
            baseline_data=baseline_v1,
        )

        id2 = in_memory_store.update_baseline(
            skill_name="test_skill",
            store_url="https://test.myshopify.com",
            baseline_data=baseline_v2,
        )

        # Should update the same record
        assert id1 == id2

    def test_update_baseline_preserves_created_at(self, in_memory_store):
        """Test that updating baseline preserves created_at timestamp."""
        baseline_v1 = {"version": 1}
        baseline_v2 = {"version": 2}

        in_memory_store.update_baseline(
            skill_name="test_skill",
            store_url="https://test.myshopify.com",
            baseline_data=baseline_v1,
        )

        # Get the created_at timestamp
        rows = list(in_memory_store.db["baselines"].rows_where(
            "skill_name = ? AND store_url = ?",
            ["test_skill", "https://test.myshopify.com"]
        ))
        created_at_v1 = rows[0]["created_at"]

        # Update the baseline
        in_memory_store.update_baseline(
            skill_name="test_skill",
            store_url="https://test.myshopify.com",
            baseline_data=baseline_v2,
        )

        # Get the created_at timestamp again
        rows = list(in_memory_store.db["baselines"].rows_where(
            "skill_name = ? AND store_url = ?",
            ["test_skill", "https://test.myshopify.com"]
        ))
        created_at_v2 = rows[0]["created_at"]

        # Should be the same
        assert created_at_v1 == created_at_v2

    def test_update_baseline_updates_timestamp(self, in_memory_store):
        """Test that updating baseline updates updated_at timestamp."""
        baseline_v1 = {"version": 1}
        baseline_v2 = {"version": 2}

        in_memory_store.update_baseline(
            skill_name="test_skill",
            store_url="https://test.myshopify.com",
            baseline_data=baseline_v1,
        )

        # Update the baseline
        in_memory_store.update_baseline(
            skill_name="test_skill",
            store_url="https://test.myshopify.com",
            baseline_data=baseline_v2,
        )

        # Get the updated_at timestamp
        rows = list(in_memory_store.db["baselines"].rows_where(
            "skill_name = ? AND store_url = ?",
            ["test_skill", "https://test.myshopify.com"]
        ))
        updated_at_v2 = rows[0]["updated_at"]

        # Should be set to current time
        assert updated_at_v2 is not None


class TestCreateIncident:
    """Test create_incident method."""

    def test_create_incident_basic(self, in_memory_store):
        """Test creating a basic incident."""
        incident_id = in_memory_store.create_incident(
            store_url="https://test.myshopify.com",
            skill_name="test_skill",
            severity="P1",
            title="Test incident",
        )

        assert isinstance(incident_id, int)
        assert incident_id > 0

    def test_create_incident_with_details(self, in_memory_store):
        """Test creating incident with details."""
        details = {"error_code": 500, "message": "Internal server error"}

        incident_id = in_memory_store.create_incident(
            store_url="https://test.myshopify.com",
            skill_name="test_skill",
            severity="P0",
            title="Critical incident",
            details=details,
        )

        # Retrieve and verify
        rows = list(in_memory_store.db["incidents"].rows_where("id = ?", [incident_id]))
        assert len(rows) == 1

        row = rows[0]
        assert json.loads(row["details"]) == details

    def test_create_incident_default_status(self, in_memory_store):
        """Test that new incidents have status 'open'."""
        incident_id = in_memory_store.create_incident(
            store_url="https://test.myshopify.com",
            skill_name="test_skill",
            severity="P2",
            title="Test incident",
        )

        rows = list(in_memory_store.db["incidents"].rows_where("id = ?", [incident_id]))
        row = rows[0]

        assert row["status"] == "open"

    def test_create_incident_no_resolved_at(self, in_memory_store):
        """Test that new incidents have no resolved_at timestamp."""
        incident_id = in_memory_store.create_incident(
            store_url="https://test.myshopify.com",
            skill_name="test_skill",
            severity="P3",
            title="Test incident",
        )

        rows = list(in_memory_store.db["incidents"].rows_where("id = ?", [incident_id]))
        row = rows[0]

        assert row["resolved_at"] is None

    def test_create_incident_has_created_at(self, in_memory_store):
        """Test that new incidents have created_at timestamp."""
        incident_id = in_memory_store.create_incident(
            store_url="https://test.myshopify.com",
            skill_name="test_skill",
            severity="P1",
            title="Test incident",
        )

        rows = list(in_memory_store.db["incidents"].rows_where("id = ?", [incident_id]))
        row = rows[0]

        assert row["created_at"] is not None
        # Should be parseable as ISO format
        timestamp = datetime.fromisoformat(row["created_at"])
        assert isinstance(timestamp, datetime)

    def test_create_multiple_incidents(self, in_memory_store):
        """Test creating multiple incidents."""
        id1 = in_memory_store.create_incident(
            store_url="https://test.myshopify.com",
            skill_name="skill1",
            severity="P0",
            title="Incident 1",
        )

        id2 = in_memory_store.create_incident(
            store_url="https://test.myshopify.com",
            skill_name="skill2",
            severity="P1",
            title="Incident 2",
        )

        assert id1 != id2
        assert id1 > 0
        assert id2 > 0


class TestResolveIncident:
    """Test resolve_incident method."""

    def test_resolve_incident(self, in_memory_store):
        """Test resolving an incident."""
        incident_id = in_memory_store.create_incident(
            store_url="https://test.myshopify.com",
            skill_name="test_skill",
            severity="P1",
            title="Test incident",
        )

        # Resolve it
        in_memory_store.resolve_incident(incident_id)

        # Verify it's resolved
        rows = list(in_memory_store.db["incidents"].rows_where("id = ?", [incident_id]))
        row = rows[0]

        assert row["status"] == "resolved"
        assert row["resolved_at"] is not None

    def test_resolve_incident_timestamp(self, in_memory_store):
        """Test that resolved incident has valid timestamp."""
        incident_id = in_memory_store.create_incident(
            store_url="https://test.myshopify.com",
            skill_name="test_skill",
            severity="P1",
            title="Test incident",
        )

        in_memory_store.resolve_incident(incident_id)

        rows = list(in_memory_store.db["incidents"].rows_where("id = ?", [incident_id]))
        row = rows[0]

        # Should be parseable as ISO format
        timestamp = datetime.fromisoformat(row["resolved_at"])
        assert isinstance(timestamp, datetime)


class TestGetOpenIncidents:
    """Test get_open_incidents method."""

    def test_get_open_incidents_empty(self, in_memory_store):
        """Test getting open incidents when none exist."""
        incidents = in_memory_store.get_open_incidents()
        assert incidents == []

    def test_get_open_incidents_all(self, in_memory_store):
        """Test getting all open incidents."""
        # Create some incidents
        in_memory_store.create_incident(
            store_url="https://test1.myshopify.com",
            skill_name="skill1",
            severity="P0",
            title="Incident 1",
        )

        in_memory_store.create_incident(
            store_url="https://test2.myshopify.com",
            skill_name="skill2",
            severity="P1",
            title="Incident 2",
        )

        incidents = in_memory_store.get_open_incidents()
        assert len(incidents) == 2

    def test_get_open_incidents_excludes_resolved(self, in_memory_store):
        """Test that resolved incidents are excluded."""
        id1 = in_memory_store.create_incident(
            store_url="https://test.myshopify.com",
            skill_name="skill1",
            severity="P0",
            title="Incident 1",
        )

        in_memory_store.create_incident(
            store_url="https://test.myshopify.com",
            skill_name="skill2",
            severity="P1",
            title="Incident 2",
        )

        # Resolve the first one
        in_memory_store.resolve_incident(id1)

        incidents = in_memory_store.get_open_incidents()
        assert len(incidents) == 1
        assert incidents[0]["skill_name"] == "skill2"

    def test_get_open_incidents_by_store(self, in_memory_store):
        """Test filtering open incidents by store URL."""
        in_memory_store.create_incident(
            store_url="https://store1.myshopify.com",
            skill_name="skill1",
            severity="P0",
            title="Incident 1",
        )

        in_memory_store.create_incident(
            store_url="https://store2.myshopify.com",
            skill_name="skill2",
            severity="P1",
            title="Incident 2",
        )

        incidents = in_memory_store.get_open_incidents(
            store_url="https://store1.myshopify.com"
        )

        assert len(incidents) == 1
        assert incidents[0]["store_url"] == "https://store1.myshopify.com"

    def test_get_open_incidents_ordered_by_created_at(self, in_memory_store):
        """Test that incidents are ordered by created_at descending."""
        id1 = in_memory_store.create_incident(
            store_url="https://test.myshopify.com",
            skill_name="skill1",
            severity="P0",
            title="Older incident",
        )

        id2 = in_memory_store.create_incident(
            store_url="https://test.myshopify.com",
            skill_name="skill2",
            severity="P1",
            title="Newer incident",
        )

        incidents = in_memory_store.get_open_incidents()

        # Should have both incidents
        assert len(incidents) == 2
        incident_ids = {inc["id"] for inc in incidents}
        assert incident_ids == {id1, id2}


class TestGetRecentCheckRuns:
    """Test get_recent_check_runs method."""

    def test_get_recent_check_runs_empty(self, in_memory_store):
        """Test getting recent check runs when none exist."""
        runs = in_memory_store.get_recent_check_runs(
            store_url="https://test.myshopify.com"
        )
        assert runs == []

    def test_get_recent_check_runs_default_limit(self, in_memory_store):
        """Test getting recent check runs with default limit."""
        # Create 15 check runs
        for i in range(15):
            in_memory_store.record_check(
                store_url="https://test.myshopify.com",
                skill_name=f"skill_{i}",
                status="PASS",
                summary=f"Test {i}",
            )

        runs = in_memory_store.get_recent_check_runs(
            store_url="https://test.myshopify.com"
        )

        # Default limit is 10
        assert len(runs) == 10

    def test_get_recent_check_runs_custom_limit(self, in_memory_store):
        """Test getting recent check runs with custom limit."""
        # Create 15 check runs
        for i in range(15):
            in_memory_store.record_check(
                store_url="https://test.myshopify.com",
                skill_name=f"skill_{i}",
                status="PASS",
                summary=f"Test {i}",
            )

        runs = in_memory_store.get_recent_check_runs(
            store_url="https://test.myshopify.com",
            limit=5,
        )

        assert len(runs) == 5

    def test_get_recent_check_runs_filtered_by_store(self, in_memory_store):
        """Test that check runs are filtered by store URL."""
        in_memory_store.record_check(
            store_url="https://store1.myshopify.com",
            skill_name="skill1",
            status="PASS",
            summary="Test 1",
        )

        in_memory_store.record_check(
            store_url="https://store2.myshopify.com",
            skill_name="skill2",
            status="PASS",
            summary="Test 2",
        )

        runs = in_memory_store.get_recent_check_runs(
            store_url="https://store1.myshopify.com"
        )

        assert len(runs) == 1
        assert runs[0]["store_url"] == "https://store1.myshopify.com"

    def test_get_recent_check_runs_ordered_by_timestamp(self, in_memory_store):
        """Test that check runs are ordered by timestamp descending."""
        id1 = in_memory_store.record_check(
            store_url="https://test.myshopify.com",
            skill_name="skill1",
            status="PASS",
            summary="Older run",
        )

        id2 = in_memory_store.record_check(
            store_url="https://test.myshopify.com",
            skill_name="skill2",
            status="PASS",
            summary="Newer run",
        )

        runs = in_memory_store.get_recent_check_runs(
            store_url="https://test.myshopify.com"
        )

        # Should have both runs
        assert len(runs) == 2
        run_ids = {run["id"] for run in runs}
        assert run_ids == {id1, id2}
