"""
Tests for the utils module.
"""
from src.utils import add, format_message, greet, multiply


class TestGreet:
    """Tests for the greet function."""

    def test_greet_simple(self):
        """Test basic greeting."""
        assert greet("World") == "Hello, World!"

    def test_greet_empty(self):
        """Test greeting with empty string."""
        assert greet("") == "Hello, !"

    def test_greet_with_special_chars(self):
        """Test greeting with special characters."""
        assert greet("Test-User_123") == "Hello, Test-User_123!"


class TestAdd:
    """Tests for the add function."""

    def test_add_positive(self):
        """Test adding positive numbers."""
        assert add(2, 3) == 5

    def test_add_negative(self):
        """Test adding negative numbers."""
        assert add(-1, -1) == -2

    def test_add_mixed(self):
        """Test adding positive and negative numbers."""
        assert add(-1, 1) == 0

    def test_add_zero(self):
        """Test adding zero."""
        assert add(0, 5) == 5


class TestMultiply:
    """Tests for the multiply function."""

    def test_multiply_positive(self):
        """Test multiplying positive numbers."""
        assert multiply(4, 5) == 20

    def test_multiply_by_zero(self):
        """Test multiplying by zero."""
        assert multiply(5, 0) == 0

    def test_multiply_negative(self):
        """Test multiplying negative numbers."""
        assert multiply(-2, -3) == 6


class TestFormatMessage:
    """Tests for the format_message function."""

    def test_format_simple(self):
        """Test simple message formatting."""
        assert format_message("Hello, {name}!", name="Alice") == "Hello, Alice!"

    def test_format_multiple(self):
        """Test message with multiple placeholders."""
        assert format_message("{greeting}, {name}!", greeting="Hi", name="Bob") == "Hi, Bob!"

    def test_format_no_args(self):
        """Test message with no placeholders."""
        assert format_message("Static message") == "Static message"
