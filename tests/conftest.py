"""Global pytest fixtures shared across all test modules."""

import pytest


@pytest.fixture(autouse=True)
def reset_ledger_singleton():
    """Reset the cost ledger singleton between tests."""
    import orchid.cost.ledger as ledger_mod
    original = ledger_mod._ledger_instance
    yield
    ledger_mod._ledger_instance = original


@pytest.fixture(autouse=True)
def reset_store_singleton():
    """Reset the auth store singleton between tests.

    get_store() caches the first-created instance for the process lifetime.
    Without this reset, the first test to trigger get_store() poisons all
    subsequent tests with its store path.
    """
    import orchid.auth.store as store_mod
    original = store_mod._store_instance
    yield
    store_mod._store_instance = original


@pytest.fixture(autouse=True)
def reset_shutdown_event():
    """Ensure the global shutdown event is cleared between tests."""
    from orchid.shutdown import clear
    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
def reset_hook_registry_singleton():
    """Reset the HookRegistry singleton between tests.

    HookRegistry() is a process-wide singleton; hooks registered by one test
    file otherwise leak into every later Session(), firing stale handlers.
    """
    from orchid.hooks.registry import HookRegistry
    yield
    HookRegistry._instance = None


@pytest.fixture(autouse=True)
def reset_agent_registry():
    """Clear the agent registry between tests."""
    import orchid.agent_registry as ar
    ar._registry.clear()
    yield
    ar._registry.clear()
