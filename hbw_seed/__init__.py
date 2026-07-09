"""Deterministic hotel booking seed data package."""

__all__ = ["FIXTURE_DATES", "reset_and_seed", "ui_contracts"]


def reset_and_seed(database_path):
    """Reset and load the deterministic seed dataset."""
    from .deterministic import reset_and_seed as _reset_and_seed

    return _reset_and_seed(database_path)


def __getattr__(name):
    if name == "FIXTURE_DATES":
        from .deterministic import FIXTURE_DATES

        return FIXTURE_DATES
    raise AttributeError(name)
