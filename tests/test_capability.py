from orchid.capability import CAPABILITY_REGISTRY, get_capability


def test_registry_has_five_entries():
    assert len(CAPABILITY_REGISTRY) == 5
    assert set(CAPABILITY_REGISTRY.keys()) == {"developer", "tester", "researcher", "reviewer", "base"}


def test_developer_and_base_are_unrestricted():
    assert CAPABILITY_REGISTRY["developer"].allowed_tools is None
    assert CAPABILITY_REGISTRY["base"].allowed_tools is None


def test_reviewer_allowed_tools_match_agent_class():
    """CAPABILITY_REGISTRY must match ReviewerAgent.allowed_tools exactly."""
    from orchid.agents.reviewer import ReviewerAgent
    registry_tools = CAPABILITY_REGISTRY["reviewer"].allowed_tools
    assert registry_tools == ReviewerAgent.allowed_tools


def test_tester_and_researcher_allowed_tools_match_agent_classes():
    """CAPABILITY_REGISTRY must match TesterAgent and ResearcherAgent allowed_tools."""
    from orchid.agents.researcher import ResearcherAgent
    from orchid.agents.tester import TesterAgent

    assert CAPABILITY_REGISTRY["tester"].allowed_tools == TesterAgent.allowed_tools
    assert CAPABILITY_REGISTRY["researcher"].allowed_tools == ResearcherAgent.allowed_tools


def test_get_capability_returns_base_for_unknown_type():
    cap = get_capability("nonexistent")
    assert cap is CAPABILITY_REGISTRY["base"]


def test_researcher_has_network_access():
    assert CAPABILITY_REGISTRY["researcher"].network_access is True


def test_tester_has_no_network_access():
    assert CAPABILITY_REGISTRY["tester"].network_access is False