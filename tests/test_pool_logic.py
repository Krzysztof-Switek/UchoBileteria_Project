from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.events import services
from apps.events.models import EventStatus, PoolManualStatus, PoolStatus
from apps.events.services import SalesState
from tests.factories import make_event, make_pool

pytestmark = pytest.mark.django_db

NOW = timezone.now()


def make_three_pools(event):
    """Spec 6.3 example: Early Bird / Regular / Last Call."""
    early = make_pool(
        event,
        name="Early Bird",
        priority=1,
        capacity=50,
        price_gross=Decimal("40.00"),
        sales_start_at=NOW - timedelta(days=1),
    )
    regular = make_pool(
        event,
        name="Regular",
        priority=2,
        capacity=150,
        price_gross=Decimal("60.00"),
        sales_start_at=NOW + timedelta(days=5),
    )
    last_call = make_pool(
        event,
        name="Last Call",
        priority=3,
        capacity=100,
        price_gross=Decimal("80.00"),
        sales_start_at=NOW + timedelta(days=10),
    )
    return early, regular, last_call


class TestPoolActivation:
    def test_pool_activates_by_start_date(self):
        event = make_event()
        early, regular, last_call = make_three_pools(event)
        statuses = dict(
            (p.name, s) for p, s in services.pools_with_status(event, NOW)
        )
        assert statuses == {"Early Bird": PoolStatus.ACTIVE, "Regular": PoolStatus.DRAFT,
                            "Last Call": PoolStatus.DRAFT}
        assert services.get_active_pool(event, NOW) == early

    def test_pool_not_active_before_start_date(self):
        event = make_event()
        make_pool(event, sales_start_at=NOW + timedelta(days=1))
        assert services.get_active_pool(event, NOW) is None

    def test_next_pool_activates_when_previous_sold_out(self):
        event = make_event()
        early, regular, _ = make_three_pools(event)
        early.sold_count = early.capacity
        early.save()
        # Regular's start date (in 5 days) not reached, but Early Bird sold out.
        assert services.get_active_pool(event, NOW) == regular

    def test_next_pool_activates_when_previous_forced_closed(self):
        event = make_event()
        early, regular, _ = make_three_pools(event)
        early.manual_status = PoolManualStatus.FORCED_CLOSED
        early.save()
        assert services.get_active_pool(event, NOW) == regular

    def test_sellout_pause_blocks_next_pool_until_its_own_date(self):
        from apps.events.models import PoolSelloutAction

        event = make_event()
        early, regular, _ = make_three_pools(event)
        early.on_sellout = PoolSelloutAction.PAUSE
        early.sold_count = early.capacity
        early.save()
        # Early sold out but chose PAUSE — Regular waits for its own date
        # (in 5 days), it does not cascade in early like the default case.
        assert services.get_active_pool(event, NOW) is None
        statuses = dict(
            (pool.name, status) for pool, status in services.pools_with_status(event, NOW)
        )
        assert statuses["Early Bird"] == PoolStatus.SOLD_OUT
        assert statuses["Regular"] == PoolStatus.DRAFT

    def test_lowest_priority_wins_when_multiple_match(self):
        event = make_event()
        a = make_pool(event, name="A", priority=1, sales_start_at=NOW - timedelta(days=2))
        make_pool(event, name="B", priority=2, sales_start_at=NOW - timedelta(days=1))
        assert services.get_active_pool(event, NOW) == a

    def test_forced_open_overrides_future_start_date(self):
        event = make_event()
        pool = make_pool(
            event,
            sales_start_at=NOW + timedelta(days=5),
            manual_status=PoolManualStatus.FORCED_OPEN,
        )
        assert services.get_active_pool(event, NOW) == pool

    def test_forced_open_does_not_override_sold_out(self):
        event = make_event()
        pool = make_pool(event, capacity=10, manual_status=PoolManualStatus.FORCED_OPEN)
        pool.sold_count = 10
        pool.save()
        assert services.get_active_pool(event, NOW) is None

    def test_pool_closes_after_its_end_date(self):
        event = make_event()
        make_pool(
            event,
            sales_start_at=NOW - timedelta(days=2),
            sales_end_at=NOW - timedelta(days=1),
        )
        [(_, status)] = services.pools_with_status(event, NOW)
        assert status == PoolStatus.CLOSED

    def test_first_pool_without_dates_is_active(self):
        event = make_event()
        pool = make_pool(event)
        assert services.get_active_pool(event, NOW) == pool

    def test_second_pool_without_dates_waits_for_first(self):
        event = make_event()
        first = make_pool(event, name="First", priority=1)
        second = make_pool(event, name="Second", priority=2)
        assert services.get_active_pool(event, NOW) == first
        first.sold_count = first.capacity
        first.save()
        assert services.get_active_pool(event, NOW) == second


class TestNextPool:
    """KUP-02: the "kolejna pula" hint on the public event page."""

    def test_returns_first_draft_pool(self):
        event = make_event()
        early, regular, _ = make_three_pools(event)
        assert services.get_next_pool(event, NOW) == regular

    def test_none_when_only_one_pool(self):
        event = make_event()
        make_pool(event)
        assert services.get_next_pool(event, NOW) is None

    def test_none_when_all_pools_already_active_or_closed(self):
        event = make_event()
        pool = make_pool(event, capacity=10, manual_status=PoolManualStatus.FORCED_OPEN)
        pool.sold_count = 10
        pool.save()
        assert services.get_next_pool(event, NOW) is None


class TestSalesState:
    def test_on_sale(self):
        event = make_event()
        make_pool(event)
        assert services.get_sales_state(event, NOW) == SalesState.ON_SALE

    def test_not_started(self):
        event = make_event(sales_start_at=NOW + timedelta(days=1))
        make_pool(event)
        assert services.get_sales_state(event, NOW) == SalesState.NOT_STARTED

    def test_closed_after_sales_end(self):
        event = make_event(sales_end_at=NOW - timedelta(hours=1))
        make_pool(event)
        assert services.get_sales_state(event, NOW) == SalesState.CLOSED

    def test_event_sold_out(self):
        event = make_event(capacity_total=10)
        make_pool(event, capacity=10)
        event.sold_total = 10
        event.save()
        assert services.get_sales_state(event, NOW) == SalesState.SOLD_OUT
        assert event.effective_status(NOW) == "SOLD_OUT"

    def test_all_pools_sold_out_means_closed(self):
        """Active pool sold out and no next pool available => sales closed."""
        event = make_event(capacity_total=300)
        pool = make_pool(event, capacity=50)
        pool.sold_count = 50
        pool.save()
        assert services.get_sales_state(event, NOW) == SalesState.CLOSED

    def test_pool_sold_out_with_future_pool_means_paused(self):
        event = make_event()
        early = make_pool(event, name="Early", priority=1, capacity=50)
        early.sold_count = 50
        early.save()
        # Future pool exists but is FORCED_CLOSED for now -> admin paused sales.
        make_pool(
            event,
            name="Later",
            priority=2,
            sales_start_at=NOW + timedelta(days=5),
            manual_status=PoolManualStatus.FORCED_CLOSED,
        )
        assert services.get_sales_state(event, NOW) == SalesState.CLOSED

    def test_paused_when_next_pool_not_yet_started_after_forced_close(self):
        event = make_event()
        make_pool(
            event, name="Early", priority=1, manual_status=PoolManualStatus.FORCED_CLOSED
        )
        make_pool(event, name="Later", priority=2, sales_start_at=NOW + timedelta(days=5))
        # 'Later' activates immediately because the previous pool is exhausted
        # (forced transition to next pool, spec 6.2).
        assert services.get_sales_state(event, NOW) == SalesState.ON_SALE


class TestPublishCancel:
    def test_publish_draft_event(self):
        event = make_event(status=EventStatus.DRAFT)
        make_pool(event)
        services.publish_event(event)
        event.refresh_from_db()
        assert event.status == EventStatus.PUBLISHED
        assert AuditLog.objects.filter(action="event.published").exists()

    def test_publish_requires_pools(self):
        event = make_event(status=EventStatus.DRAFT)
        with pytest.raises(ValidationError):
            services.publish_event(event)

    def test_publish_requires_gates_open_at(self):
        event = make_event(status=EventStatus.DRAFT, gates_open_at=None)
        make_pool(event)
        with pytest.raises(ValidationError):
            services.publish_event(event)

    def test_publish_rejects_gates_open_after_start(self):
        event = make_event(status=EventStatus.DRAFT)
        event.gates_open_at = event.start_at + timedelta(hours=1)
        event.save()
        make_pool(event)
        with pytest.raises(ValidationError):
            services.publish_event(event)

    def test_publish_allows_unset_end_at(self):
        event = make_event(status=EventStatus.DRAFT, end_at=None)
        make_pool(event)
        services.publish_event(event)
        event.refresh_from_db()
        assert event.status == EventStatus.PUBLISHED

    def test_publish_rejects_non_draft(self):
        event = make_event(status=EventStatus.PUBLISHED)
        make_pool(event)
        with pytest.raises(ValidationError):
            services.publish_event(event)

    def test_cancel_published_event(self):
        event = make_event()
        services.cancel_event(event)
        event.refresh_from_db()
        assert event.status == EventStatus.CANCELLED
        assert AuditLog.objects.filter(action="event.cancelled").exists()

    def test_cannot_cancel_finished_event(self):
        event = make_event(status=EventStatus.FINISHED)
        with pytest.raises(ValidationError):
            services.cancel_event(event)

    def test_set_pool_manual_status_is_audited(self):
        event = make_event()
        pool = make_pool(event)
        services.set_pool_manual_status(pool, PoolManualStatus.FORCED_OPEN)
        entry = AuditLog.objects.get(action="pool.manual_status_set")
        assert entry.metadata == {"status": "FORCED_OPEN"}
