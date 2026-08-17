"""Internal knowledge-base Flask module."""

__all__ = ["bp"]


def __getattr__(name):
    if name == "bp":
        from .routes import bp
        return bp
    raise AttributeError(name)
