"""Shared coloured status-badge rendering for Django admin list_display
columns (OBS-02). Colour is always paired with the label text, never colour
alone — see docs/Hendouts TODOs/21.08_audyt_ux_PLAN_TO_DO.md.

Colours are solid, medium-saturation + white text on purpose: that combination
reads clearly against both the light and dark admin themes (static/admin_theme.css),
unlike near-black or near-white chips which only work against one of them.
"""

from django.utils.html import format_html

TONE_COLORS = {
    "success": "#059669",
    "warning": "#d97706",
    "danger": "#dc2626",
    "info": "#2563eb",
    "neutral": "#52525b",
}


def badge(label: str, tone: str = "neutral"):
    color = TONE_COLORS.get(tone, TONE_COLORS["neutral"])
    return format_html(
        '<span style="background:{}; color:#fff; padding:2px 9px; '
        'border-radius:999px; font-size:0.75rem; font-weight:600; '
        'white-space:nowrap;">{}</span>',
        color,
        label,
    )


def choice_badge(value: str, tones: dict[str, str], labels: dict[str, str] | None = None):
    """Badge for a TextChoices-style value. `tones` maps value -> tone name;
    `labels` optionally overrides the displayed text (defaults to `value`)."""
    label = (labels or {}).get(value, value)
    return badge(label, tones.get(value, "neutral"))


def demo_badge(is_demo: bool):
    """DEMO/LIVE badge — same amber-for-demo convention used in the staff nav
    and the public DEMO banner."""
    return badge("DEMO", "warning") if is_demo else badge("LIVE", "success")
