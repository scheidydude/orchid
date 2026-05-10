import threading

from orchid.agents.base import AgentCancelledError, BaseAgent


def test_agent_cancelled_error_is_exception():
    """AgentCancelledError is a proper exception that can be raised and caught."""
    try:
        raise AgentCancelledError("task was cancelled")
    except AgentCancelledError as exc:
        assert str(exc) == "task was cancelled"


def test_cancel_event_is_set_in_base_agent():
    """BaseAgent has a _cancel_event attribute that is a threading.Event."""
    agent = BaseAgent()
    assert isinstance(agent._cancel_event, threading.Event)
    assert not agent._cancel_event.is_set()
    agent.cancel()
    assert agent._cancel_event.is_set()


def test_cancel_event_can_be_set_from_outside():
    """Setting cancel_event from outside causes the agent to see it."""
    agent = BaseAgent()
    assert not agent._cancel_event.is_set()
    agent.cancel()
    assert agent._cancel_event.is_set()
    # Verify the event can be reset too
    agent._cancel_event.clear()
    assert not agent._cancel_event.is_set()