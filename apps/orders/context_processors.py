from .models import PaymentConfig


def payment_mode(request):
    """Expose the global demo/live switch to every template (demo banner)."""
    return {"payment_demo_mode": PaymentConfig.is_demo_mode()}
