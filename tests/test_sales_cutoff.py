"""Online sales stop at midnight before the concert day — enforced by the
models, so no path (admin, seed data, scripts) can sell into the concert day."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.events.models import Event, online_sales_cutoff
from tests.factories import make_event, make_pool

pytestmark = pytest.mark.django_db


def concert(days=10, hour=21):
    day = timezone.localdate() + timedelta(days=days)
    return timezone.make_aware(timezone.datetime(day.year, day.month, day.day, hour))


class TestCutoff:
    def test_cutoff_is_local_midnight_of_concert_day(self):
        cutoff = timezone.localtime(online_sales_cutoff(concert()))
        assert cutoff.date() == concert().date()
        assert (cutoff.hour, cutoff.minute) == (0, 0)

    def test_event_sales_end_clamped_to_cutoff(self):
        event = make_event(start_at=concert(), end_at=concert() + timedelta(hours=4),
                           sales_end_at=concert() + timedelta(days=1))
        assert event.sales_end_at == online_sales_cutoff(event.start_at)

    def test_earlier_sales_end_kept(self):
        early = concert() - timedelta(days=3)
        event = make_event(start_at=concert(), sales_end_at=early)
        assert event.sales_end_at == early

    def test_pool_sales_end_clamped_to_cutoff(self):
        event = make_event(start_at=concert(), end_at=concert() + timedelta(hours=4))
        pool = make_pool(event, sales_end_at=concert())
        assert pool.sales_end_at == online_sales_cutoff(event.start_at)


class TestEventList:
    def test_list_shows_short_dates_and_pools(self, admin_client):
        event = make_event(title="Lista test", start_at=concert(hour=21),
                           end_at=concert(hour=23), sales_end_at=concert(hour=21))
        make_pool(event, name="Early", capacity=50)
        html = admin_client.get("/admin/events/event/").content.decode()
        assert timezone.localtime(event.start_at).strftime("%d.%m.%y 21:00") in html
        assert "pule biletów" in html.lower()
        assert "Early: 0/50" in html
        # online cutoff shown as the event's sales end
        assert online_sales_cutoff(event.start_at).strftime("%d.%m.%y 00:00") in html


class TestSeedDemo:
    def test_seed_demo_on_fresh_db_respects_cutoff(self):
        call_command("seed_demo")
        event = Event.objects.get(slug="test-koncert")
        cutoff = online_sales_cutoff(event.start_at)
        assert event.sales_end_at == cutoff
        assert event.pools.count() == 3
        assert all(p.sales_end_at is None or p.sales_end_at <= cutoff for p in event.pools.all())
