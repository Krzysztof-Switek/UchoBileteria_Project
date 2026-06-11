"""Payment provider interface (spec section 13)."""

from abc import ABC, abstractmethod

from apps.orders.models import Order


class PaymentProvider(ABC):
    @abstractmethod
    def start_checkout(self, order: Order) -> str:
        """
        Create a payment session for the order and return the URL
        the buyer should be redirected to.
        """

    @abstractmethod
    def refund(self, order: Order, amount=None) -> str:
        """
        Request a refund; returns the provider's refund reference.
        Confirmation arrives through the webhook pipeline.
        """
