"""
Utility module for the test project.
"""


def greet(name: str) -> str:
    """Return a greeting message for the given name."""
    return f"Hello, {name}!"


def add(a: int, b: int) -> int:
    """Add two numbers and return the result."""
    return a + b


def multiply(a: int, b: int) -> int:
    """Multiply two numbers and return the result."""
    return a * b


def format_message(template: str, **kwargs) -> str:
    """Format a message template with the given keyword arguments."""
    return template.format(**kwargs)
