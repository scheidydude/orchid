"""
Main module for the test project.
"""

from src.utils import add, format_message, greet, multiply


def main():
    """Entry point for the application."""
    print("Hello from the test project!")
    print(greet("World"))
    print(f"2 + 3 = {add(2, 3)}")
    print(f"4 * 5 = {multiply(4, 5)}")
    print(format_message("Hello, {name}! You are {age} years old.", name="Alice", age=30))
    return 0


if __name__ == "__main__":
    main()
