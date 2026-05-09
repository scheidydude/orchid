import queue
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MailboxMessage:
    sender: str
    content: Any
    timestamp: float = field(default_factory=time.monotonic)


class AgentMailbox:
    """Thread-safe message queue for a single agent instance."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self._queue: queue.Queue[MailboxMessage] = queue.Queue()

    def send(self, sender: str, content: Any) -> None:
        """Put a message onto the queue."""
        self._queue.put(MailboxMessage(sender=sender, content=content))

    def receive(self, timeout_s: float = 0.0) -> MailboxMessage | None:
        """Get a message from the queue. Returns None on timeout."""
        try:
            return self._queue.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def has_messages(self) -> bool:
        """Return True if there are messages waiting."""
        return not self._queue.empty()

    def drain(self) -> list[MailboxMessage]:
        """Collect all current messages without blocking."""
        messages: list[MailboxMessage] = []
        while self.has_messages():
            msg = self.receive()
            if msg is not None:
                messages.append(msg)
        return messages


_mailboxes: dict[str, AgentMailbox] = {}
_lock = threading.Lock()


def get_mailbox(agent_id: str) -> AgentMailbox:
    """Get or create a mailbox for the given agent_id (thread-safe)."""
    with _lock:
        if agent_id not in _mailboxes:
            _mailboxes[agent_id] = AgentMailbox(agent_id)
        return _mailboxes[agent_id]


def drop_mailbox(agent_id: str) -> None:
    """Remove a mailbox from the registry (thread-safe)."""
    with _lock:
        _mailboxes.pop(agent_id, None)