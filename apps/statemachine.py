"""Minimal status-transition guard shared by Order and Ticket models."""


class InvalidTransition(Exception):
    def __init__(self, entity: str, old: str, new: str):
        self.old = old
        self.new = new
        super().__init__(f"{entity}: invalid status transition {old} -> {new}")


def assert_transition(entity: str, transitions: dict, old: str, new: str) -> None:
    """Raise InvalidTransition unless `old -> new` is an allowed transition."""
    if new not in transitions.get(old, ()):
        raise InvalidTransition(entity, old, new)
