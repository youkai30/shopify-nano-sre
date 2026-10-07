"""Tests for the trigger module."""

import asyncio
from datetime import datetime, timedelta

import pytest

from nano_sre.agent.trigger import IntervalTrigger, TriggerManager, WebhookTrigger
import nano_sre.agent.trigger as trigger_module


class TestIntervalTrigger:
    """Test IntervalTrigger class."""

    def test_interval_trigger_creation(self):
        """Test creating an interval trigger."""
        trigger = IntervalTrigger(interval_minutes=5)

        assert trigger.interval_minutes == 5
        assert trigger.last_triggered is None

    def test_should_trigger_first_time(self):
        """Test that first trigger always fires."""
        trigger = IntervalTrigger(interval_minutes=5)

        assert trigger.should_trigger() is True

    def test_should_trigger_within_interval(self):
        """Test that trigger doesn't fire within interval."""
        trigger = IntervalTrigger(interval_minutes=5)

        # First trigger
        trigger.mark_triggered()

        # Immediately after should not trigger
        assert trigger.should_trigger() is False

    def test_should_trigger_after_interval(self):
        """Test that trigger fires after interval passes."""
        trigger = IntervalTrigger(interval_minutes=1)

        # First trigger
        trigger.mark_triggered()

        # Manually set last_triggered to past
        trigger.last_triggered = datetime.utcnow() - timedelta(minutes=2)

        # Should trigger now
        assert trigger.should_trigger() is True

    def test_mark_triggered(self):
        """Test marking trigger as fired."""
        trigger = IntervalTrigger(interval_minutes=5)

        assert trigger.last_triggered is None
        trigger.mark_triggered()
        assert trigger.last_triggered is not None
        assert isinstance(trigger.last_triggered, datetime)

    def test_mark_triggered_updates_timestamp(self, monkeypatch):
        """Test that marking triggered updates timestamp."""
        trigger = IntervalTrigger(interval_minutes=5)
        first = datetime(2026, 1, 1, 0, 0, 0)
        second = datetime(2026, 1, 1, 0, 0, 1)
        times = iter((first, second))

        class ControlledDateTime(datetime):
            @classmethod
            def utcnow(cls):
                return next(times)

        monkeypatch.setattr(trigger_module, "datetime", ControlledDateTime)

        trigger.mark_triggered()
        first_time = trigger.last_triggered

        # Manually set to past
        trigger.last_triggered = first - timedelta(minutes=10)

        trigger.mark_triggered()
        second_time = trigger.last_triggered

        # Should be different
        assert second_time > first_time

    @pytest.mark.asyncio
    async def test_wait_until_next_first_time(self):
        """Test waiting when never triggered."""
        trigger = IntervalTrigger(interval_minutes=5)

        # Should return immediately on first call
        start = asyncio.get_event_loop().time()
        await trigger.wait_until_next()
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed < 0.1  # Should be almost instant

    @pytest.mark.asyncio
    async def test_wait_until_next_waits(self):
        """Test that wait_until_next actually waits."""
        trigger = IntervalTrigger(interval_minutes=1)

        # Mark as triggered in the past
        trigger.last_triggered = datetime.utcnow() - timedelta(seconds=58)

        # Should wait ~2 seconds
        start = asyncio.get_event_loop().time()
        await trigger.wait_until_next()
        elapsed = asyncio.get_event_loop().time() - start

        # Should wait approximately 2 seconds (with some tolerance)
        assert 1.5 < elapsed < 3.0

    @pytest.mark.asyncio
    async def test_wait_until_next_already_past(self):
        """Test waiting when next trigger time already passed."""
        trigger = IntervalTrigger(interval_minutes=1)

        # Mark as triggered way in the past
        trigger.last_triggered = datetime.utcnow() - timedelta(minutes=10)

        # Should return immediately
        start = asyncio.get_event_loop().time()
        await trigger.wait_until_next()
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed < 0.1  # Should be almost instant


class TestWebhookTrigger:
    """Test WebhookTrigger class."""

    def test_webhook_trigger_creation(self):
        """Test creating a webhook trigger."""
        trigger = WebhookTrigger(port=8080)

        assert trigger.port == 8080
        assert trigger.events == []
        assert trigger.handlers == []

    def test_webhook_trigger_default_port(self):
        """Test default port for webhook trigger."""
        trigger = WebhookTrigger()

        assert trigger.port == 8000

    def test_register_handler(self):
        """Test registering a webhook handler."""
        trigger = WebhookTrigger()

        def handler(context):
            pass

        trigger.register_handler(handler)

        assert len(trigger.handlers) == 1
        assert trigger.handlers[0] == handler

    def test_register_multiple_handlers(self):
        """Test registering multiple handlers."""
        trigger = WebhookTrigger()

        def handler1(context):
            pass

        def handler2(context):
            pass

        trigger.register_handler(handler1)
        trigger.register_handler(handler2)

        assert len(trigger.handlers) == 2

    def test_normalize_github_deployment(self):
        """Test normalizing GitHub deployment payload."""
        trigger = WebhookTrigger()

        payload = {
            "deployment": {
                "environment": "production",
                "ref": "main",
                "sha": "abc123",
            },
            "repository": {
                "full_name": "owner/repo",
            },
        }

        context = trigger.normalize_github_deployment(payload)

        assert context["trigger_type"] == "github_deployment"
        assert context["environment"] == "production"
        assert context["ref"] == "main"
        assert context["sha"] == "abc123"
        assert context["repository"] == "owner/repo"
        assert "created_at" in context
        assert context["original_payload"] == payload

    def test_normalize_github_deployment_missing_fields(self):
        """Test normalizing payload with missing fields."""
        trigger = WebhookTrigger()

        payload = {}
        context = trigger.normalize_github_deployment(payload)

        assert context["trigger_type"] == "github_deployment"
        assert context["environment"] is None
        assert context["ref"] is None
        assert context["sha"] is None
        assert context["repository"] is None

    @pytest.mark.asyncio
    async def test_receive_webhook(self):
        """Test receiving a webhook."""
        trigger = WebhookTrigger()

        payload = {
            "action": "created",
            "deployment": {"environment": "production"},
        }

        await trigger.receive_webhook(payload)

        assert len(trigger.events) == 1
        assert trigger.events[0] == payload

    @pytest.mark.asyncio
    async def test_receive_webhook_calls_handlers(self):
        """Test that receiving webhook calls registered handlers."""
        trigger = WebhookTrigger()

        handler_called = []

        def handler(context):
            handler_called.append(context)

        trigger.register_handler(handler)

        payload = {"deployment": {}, "repository": {}}
        await trigger.receive_webhook(payload)

        assert len(handler_called) == 1
        assert handler_called[0]["trigger_type"] == "github_deployment"

    @pytest.mark.asyncio
    async def test_receive_webhook_multiple_handlers(self):
        """Test that all handlers are called."""
        trigger = WebhookTrigger()

        handler1_called = []
        handler2_called = []

        def handler1(context):
            handler1_called.append(context)

        def handler2(context):
            handler2_called.append(context)

        trigger.register_handler(handler1)
        trigger.register_handler(handler2)

        payload = {"deployment": {}, "repository": {}}
        await trigger.receive_webhook(payload)

        assert len(handler1_called) == 1
        assert len(handler2_called) == 1

    @pytest.mark.asyncio
    async def test_receive_webhook_handler_exception(self):
        """Test that handler exceptions are caught."""
        trigger = WebhookTrigger()

        def failing_handler(context):
            raise RuntimeError("Handler failed")

        trigger.register_handler(failing_handler)

        # Should not raise exception
        payload = {"deployment": {}, "repository": {}}
        await trigger.receive_webhook(payload)

        # Event should still be added
        assert len(trigger.events) == 1

    def test_has_pending_events_false(self):
        """Test has_pending_events when no events."""
        trigger = WebhookTrigger()

        assert trigger.has_pending_events() is False

    @pytest.mark.asyncio
    async def test_has_pending_events_true(self):
        """Test has_pending_events when events exist."""
        trigger = WebhookTrigger()

        await trigger.receive_webhook({"deployment": {}})

        assert trigger.has_pending_events() is True

    def test_pop_event_empty(self):
        """Test popping event when none exist."""
        trigger = WebhookTrigger()

        event = trigger.pop_event()

        assert event is None

    @pytest.mark.asyncio
    async def test_pop_event(self):
        """Test popping an event."""
        trigger = WebhookTrigger()

        payload = {"deployment": {}, "action": "test"}
        await trigger.receive_webhook(payload)

        event = trigger.pop_event()

        assert event == payload
        assert len(trigger.events) == 0

    @pytest.mark.asyncio
    async def test_pop_event_order(self):
        """Test that events are popped in FIFO order."""
        trigger = WebhookTrigger()

        payload1 = {"action": "first"}
        payload2 = {"action": "second"}

        await trigger.receive_webhook(payload1)
        await trigger.receive_webhook(payload2)

        event1 = trigger.pop_event()
        event2 = trigger.pop_event()

        assert event1["action"] == "first"
        assert event2["action"] == "second"


class TestTriggerManager:
    """Test TriggerManager class."""

    def test_trigger_manager_creation(self):
        """Test creating a trigger manager."""
        manager = TriggerManager(interval_minutes=5, webhook_port=8080)

        assert manager.interval.interval_minutes == 5
        assert manager.webhook.port == 8080
        assert manager.queue == []

    def test_add_interval_trigger_first_time(self):
        """Test adding interval trigger for first time."""
        manager = TriggerManager(interval_minutes=5)

        manager.add_interval_trigger()

        assert len(manager.queue) == 1
        assert manager.queue[0]["type"] == "interval"

    def test_add_interval_trigger_not_ready(self):
        """Test adding interval trigger when not ready."""
        manager = TriggerManager(interval_minutes=5)

        # First trigger
        manager.add_interval_trigger()
        assert len(manager.queue) == 1

        # Immediate second attempt should not add
        manager.add_interval_trigger()
        assert len(manager.queue) == 1

    def test_add_interval_trigger_ready_after_interval(self):
        """Test adding interval trigger after interval passes."""
        manager = TriggerManager(interval_minutes=1)

        # First trigger
        manager.add_interval_trigger()
        assert len(manager.queue) == 1

        # Manually set to past
        manager.interval.last_triggered = datetime.utcnow() - timedelta(minutes=2)

        # Should add another
        manager.add_interval_trigger()
        assert len(manager.queue) == 2

    @pytest.mark.asyncio
    async def test_add_webhook_event_no_events(self):
        """Test adding webhook event when none pending."""
        manager = TriggerManager(interval_minutes=5)

        manager.add_webhook_event()

        assert len(manager.queue) == 0

    @pytest.mark.asyncio
    async def test_add_webhook_event_with_pending(self):
        """Test adding webhook event when event pending."""
        manager = TriggerManager(interval_minutes=5)

        # Add a webhook event
        payload = {"deployment": {}, "repository": {}}
        await manager.webhook.receive_webhook(payload)

        # Add to queue
        manager.add_webhook_event()

        assert len(manager.queue) == 1
        assert manager.queue[0]["trigger_type"] == "github_deployment"

    @pytest.mark.asyncio
    async def test_wait_for_trigger_interval(self):
        """Test waiting for interval trigger."""
        manager = TriggerManager(interval_minutes=5)

        # Should get interval trigger immediately (first time)
        context = await manager.wait_for_trigger()

        assert context["type"] == "interval"

    @pytest.mark.asyncio
    async def test_wait_for_trigger_webhook_priority(self):
        """Test that webhook events are processed along with interval."""
        manager = TriggerManager(interval_minutes=5)

        # Add a webhook event
        payload = {"deployment": {"environment": "prod"}, "repository": {}}
        await manager.webhook.receive_webhook(payload)

        # First call might get either webhook or interval (both are ready)
        context1 = await manager.wait_for_trigger()

        # Check that we get a valid context
        assert "type" in context1 or "trigger_type" in context1

        # If we got interval first, webhook should be in the queue
        if context1.get("type") == "interval":
            # There should be a webhook event still pending
            assert manager.webhook.has_pending_events() or len(manager.queue) > 0

    @pytest.mark.asyncio
    async def test_wait_for_trigger_queue_empty_waits(self):
        """Test that wait_for_trigger waits when queue is empty and not ready."""
        manager = TriggerManager(interval_minutes=1)

        # Trigger once to mark as triggered
        manager.add_interval_trigger()
        manager.queue.pop(0)

        # Set last trigger to 58 seconds ago
        manager.interval.last_triggered = datetime.utcnow() - timedelta(seconds=58)

        # Should wait approximately 2 seconds
        start = asyncio.get_event_loop().time()
        context = await manager.wait_for_trigger()
        elapsed = asyncio.get_event_loop().time() - start

        # Should have waited
        assert elapsed > 1.5
        assert context["type"] == "interval"

    @pytest.mark.asyncio
    async def test_wait_for_trigger_fallback(self):
        """Test fallback trigger context."""
        manager = TriggerManager(interval_minutes=1)

        # Mark as triggered way in the past
        manager.interval.last_triggered = datetime.utcnow() - timedelta(minutes=10)

        # Manually clear queue to test fallback
        manager.queue = []

        context = await manager.wait_for_trigger()

        # Should return some context (either interval or fallback)
        assert "type" in context or "trigger_type" in context
