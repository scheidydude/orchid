from orchid.mailbox import drop_mailbox, get_mailbox


def test_send_and_receive():
    agent_id = "agent-A"
    mb = get_mailbox(agent_id)
    mb.send("sender-X", "hello")
    msg = mb.receive()
    assert msg is not None
    assert msg.content == "hello"
    assert msg.sender == "sender-X"
    drop_mailbox(agent_id)


def test_receive_returns_none_when_empty():
    agent_id = "agent-B"
    mb = get_mailbox(agent_id)
    msg = mb.receive(timeout_s=0.0)
    assert msg is None
    drop_mailbox(agent_id)


def test_has_messages():
    agent_id = "agent-C"
    mb = get_mailbox(agent_id)
    assert mb.has_messages() is False
    mb.send("sender-X", "hello")
    assert mb.has_messages() is True
    drop_mailbox(agent_id)


def test_drop_mailbox_removes_it():
    agent_id = "agent-D"
    mb = get_mailbox(agent_id)
    mb.send("sender-X", "hello")
    drop_mailbox(agent_id)
    mb2 = get_mailbox(agent_id)
    assert mb2 is not mb
    assert mb2.has_messages() is False