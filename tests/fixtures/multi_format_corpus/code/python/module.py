"""Trivial Python module fixture."""


def hello() -> str:
    """Return a greeting."""
    return "hello, world"


class Greeter:
    """A trivial class."""

    def greet(self, name: str) -> str:
        return f"hello, {name}"
