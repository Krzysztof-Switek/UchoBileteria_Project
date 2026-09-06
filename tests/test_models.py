import pytest
from django.db import IntegrityError, transaction

from apps.auditlog.models import AuditLog
from apps.auditlog.services import log_action
from apps.orders.models import OrderStatus, PaymentConfig, PaymentMode
from apps.statemachine import InvalidTransition
from apps.tickets.models import TicketStatus
from tests.factories import make_event, make_order, make_pool, make_ticket

pytestmark = pytest.mark.django_db


class TestDbConstraints:
    def test_event_cannot_oversell(self):
        event = make_event(capacity_total=10)
        event.sold_total = 11
        with pytest.raises(IntegrityError), transaction.atomic():
            event.save()

    def test_pool_cannot_oversell(self):
        event = make_event()
        pool = make_pool(event, capacity=5)
        pool.sold_count = 6
        with pytest.raises(IntegrityError), transaction.atomic():
            pool.save()

    def test_order_quantity_must_be_positive(self):
        event = make_event()
        pool = make_pool(event)
        with pytest.raises(IntegrityError), transaction.atomic():
            make_order(event, pool, quantity=0)


class TestOrderTransitions:
    @pytest.mark.parametrize(
        ("old", "new"),
        [
            (OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING),
            (OrderStatus.CREATED, OrderStatus.EXPIRED),
            (OrderStatus.CREATED, OrderStatus.CANCELLED),
            (OrderStatus.PAYMENT_PENDING, OrderStatus.PAID),
            (OrderStatus.PAYMENT_PENDING, OrderStatus.FAILED),
            (OrderStatus.PAYMENT_PENDING, OrderStatus.EXPIRED),
            (OrderStatus.PAID, OrderStatus.REFUNDED),
            (OrderStatus.FAILED, OrderStatus.PAYMENT_PENDING),
            (OrderStatus.EXPIRED, OrderStatus.PAID),
        ],
    )
    def test_allowed(self, old, new):
        event = make_event()
        order = make_order(event, make_pool(event), status=old)
        order.transition_to(new)
        assert order.status == new

    @pytest.mark.parametrize(
        ("old", "new"),
        [
            (OrderStatus.CREATED, OrderStatus.PAID),  # must go through PAYMENT_PENDING
            (OrderStatus.CREATED, OrderStatus.REFUNDED),
            (OrderStatus.PAID, OrderStatus.CANCELLED),
            (OrderStatus.PAID, OrderStatus.EXPIRED),
            (OrderStatus.REFUNDED, OrderStatus.PAID),
            (OrderStatus.CANCELLED, OrderStatus.PAYMENT_PENDING),
            (OrderStatus.EXPIRED, OrderStatus.PAYMENT_PENDING),
        ],
    )
    def test_forbidden(self, old, new):
        event = make_event()
        order = make_order(event, make_pool(event), status=old)
        with pytest.raises(InvalidTransition):
            order.transition_to(new)
        assert order.status == old


class TestTicketTransitions:
    @pytest.mark.parametrize(
        ("old", "new"),
        [
            (TicketStatus.ISSUED, TicketStatus.CHECKED_IN),
            (TicketStatus.ISSUED, TicketStatus.REFUNDED),
            (TicketStatus.ISSUED, TicketStatus.CANCELLED),
            (TicketStatus.ISSUED, TicketStatus.INVALIDATED),
            (TicketStatus.CHECKED_IN, TicketStatus.INVALIDATED),
            # OBS-06: door staff can undo a mistaken scan.
            (TicketStatus.CHECKED_IN, TicketStatus.ISSUED),
        ],
    )
    def test_allowed(self, old, new):
        event = make_event()
        ticket = make_ticket(make_order(event, make_pool(event)), status=old)
        ticket.transition_to(new)
        assert ticket.status == new

    @pytest.mark.parametrize(
        ("old", "new"),
        [
            (TicketStatus.CHECKED_IN, TicketStatus.CHECKED_IN),  # double scan
            (TicketStatus.REFUNDED, TicketStatus.CHECKED_IN),
            (TicketStatus.CANCELLED, TicketStatus.CHECKED_IN),
            (TicketStatus.INVALIDATED, TicketStatus.CHECKED_IN),
            (TicketStatus.REFUNDED, TicketStatus.ISSUED),
        ],
    )
    def test_forbidden(self, old, new):
        event = make_event()
        ticket = make_ticket(make_order(event, make_pool(event)), status=old)
        with pytest.raises(InvalidTransition):
            ticket.transition_to(new)
        assert ticket.status == old


class TestPaymentConfig:
    def test_singleton_defaults_to_demo_mode(self):
        config = PaymentConfig.load()
        assert config.mode == PaymentMode.DEMO
        assert PaymentConfig.is_demo_mode() is True

    def test_always_single_row(self):
        PaymentConfig.load()
        another = PaymentConfig(mode=PaymentMode.LIVE)
        another.save()
        assert PaymentConfig.objects.count() == 1
        assert PaymentConfig.load().mode == PaymentMode.LIVE


class TestAuditLog:
    def test_log_action_records_entity_and_metadata(self):
        event = make_event()
        log_action("event.published", event, metadata={"capacity": event.capacity_total})
        entry = AuditLog.objects.get()
        assert entry.entity_type == "Event"
        assert entry.entity_id == str(event.pk)
        assert entry.actor_label == "system"
        assert entry.metadata == {"capacity": 300}

    def test_log_action_with_actor(self, django_user_model):
        user = django_user_model.objects.create_superuser("boss", password="x")
        event = make_event()
        entry = log_action("event.created", event, actor=user)
        assert entry.actor == user
        assert entry.actor_label == "boss"
        assert entry.actor_role == "ADMIN"
