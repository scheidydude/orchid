from orchid.locks import FileLockRegistry


def test_acquire_and_release_no_exception():
    """Create FileLockRegistry(), call acquire("test.py"), call release("test.py"), assert no exception."""
    registry = FileLockRegistry()
    registry.acquire("test.py")
    registry.release("test.py")


def test_lock_context_manager_no_exception():
    """Use `with registry.lock("file.txt"):` block, assert no exception."""
    registry = FileLockRegistry()
    with registry.lock("file.txt"):
        pass


def test_different_paths_have_different_locks():
    """Acquire lock for "a.py", acquire lock for "b.py", assert they are different threading.Lock objects."""
    registry = FileLockRegistry()
    lock_a = registry._get_lock("a.py")
    lock_b = registry._get_lock("b.py")
    assert lock_a is not lock_b
    # threading.Lock() returns a _thread.lock instance
    assert type(lock_a).__name__ == "lock"
    assert type(lock_b).__name__ == "lock"


def test_same_path_returns_same_lock():
    """Call _get_lock("same.py") twice, assert they are the same object."""
    registry = FileLockRegistry()
    lock1 = registry._get_lock("same.py")
    lock2 = registry._get_lock("same.py")
    assert lock1 is lock2


def test_release_unlocked_does_not_raise():
    """Call release("never_acquired.py") without acquiring first, assert no exception."""
    registry = FileLockRegistry()
    registry.release("never_acquired.py")