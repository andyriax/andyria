"""Integration tests for the Andyria coordinator."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest


def _make_coordinator(tmp_path: Path):
    from andyria.coordinator import Coordinator
    return Coordinator(
        data_dir=tmp_path,
        node_id="test-node",
        deployment_class="edge",
        entropy_sources=["os_urandom", "clock_jitter"],
    )


class TestCoordinator:
    def test_process_returns_response(self, tmp_path):
        from andyria.coordinator import Coordinator
        from andyria.models import AndyriaRequest

        coord = _make_coordinator(tmp_path)
        request = AndyriaRequest(input="Hello, Andyria!")
        response = asyncio.run(coord.process(request))

        assert response.request_id == request.id
        assert response.output
        assert response.entropy_beacon_id
        assert response.tasks_completed >= 1

    def test_entropy_beacon_stored(self, tmp_path):
        from andyria.coordinator import Coordinator
        from andyria.models import AndyriaRequest

        coord = _make_coordinator(tmp_path)
        request = AndyriaRequest(input="What is 2 + 2?")
        response = asyncio.run(coord.process(request))

        beacon = coord.get_beacon(response.entropy_beacon_id)
        assert beacon is not None
        assert beacon.id == response.entropy_beacon_id

    def test_math_symbolic_solve(self, tmp_path):
        from andyria.coordinator import Coordinator
        from andyria.models import AndyriaRequest

        coord = _make_coordinator(tmp_path)
        request = AndyriaRequest(input="calculate 6 * 7")
        response = asyncio.run(coord.process(request))

        assert "42" in response.output

    def test_event_log_grows(self, tmp_path):
        from andyria.coordinator import Coordinator
        from andyria.models import AndyriaRequest

        coord = _make_coordinator(tmp_path)
        for prompt in ["Hello", "What is 3 + 5?", "Summarize AI"]:
            asyncio.run(coord.process(AndyriaRequest(input=prompt)))

        events = coord.get_event_log()
        assert len(events) >= 1

    def test_status_fields(self, tmp_path):
        from andyria.coordinator import Coordinator
        from andyria.models import AndyriaRequest

        coord = _make_coordinator(tmp_path)
        asyncio.run(coord.process(AndyriaRequest(input="ping")))
        status = coord.status()

        assert status.node_id == "test-node"
        assert status.requests_processed == 1
        assert status.entropy_beacons_generated == 1
        assert status.uptime_s >= 0

    def test_session_context_persisted(self, tmp_path):
        from andyria.coordinator import Coordinator
        from andyria.models import AndyriaRequest

        coord = _make_coordinator(tmp_path)
        sid = "test-session-001"

        r1 = asyncio.run(coord.process(AndyriaRequest(input="My name is Alice.", session_id=sid)))
        assert r1.session_id == sid
        assert r1.turn_number == 1

        r2 = asyncio.run(coord.process(AndyriaRequest(input="What did I just tell you?", session_id=sid)))
        assert r2.turn_number == 2

        session = coord.get_session(sid)
        assert session is not None
        assert len(session.turns) == 4  # 2 user + 2 assistant

    def test_session_clear(self, tmp_path):
        from andyria.coordinator import Coordinator
        from andyria.models import AndyriaRequest

        coord = _make_coordinator(tmp_path)
        sid = "test-session-002"
        asyncio.run(coord.process(AndyriaRequest(input="Remember this.", session_id=sid)))
        coord.clear_session(sid)
        assert coord.get_session(sid) is None

    def test_stateless_request_no_session(self, tmp_path):
        from andyria.coordinator import Coordinator
        from andyria.models import AndyriaRequest

        coord = _make_coordinator(tmp_path)
        resp = asyncio.run(coord.process(AndyriaRequest(input="Stateless query")))
        assert resp.session_id is None
        assert resp.turn_number == 1

    def test_processing_ms_present(self, tmp_path):
        from andyria.coordinator import Coordinator
        from andyria.models import AndyriaRequest

        coord = _make_coordinator(tmp_path)
        resp = asyncio.run(coord.process(AndyriaRequest(input="time this")))
        assert resp.processing_ms is not None
        assert resp.processing_ms >= 0

    def test_readiness_ready_field(self, tmp_path):
        coord = _make_coordinator(tmp_path)
        status = coord.status()
        assert isinstance(status.ready, bool)
        assert status.readiness_detail is not None

    def test_readiness_fails_for_missing_configured_model(self, tmp_path):
        from andyria.coordinator import Coordinator

        missing_model = tmp_path / "models" / "missing.gguf"
        coord = Coordinator(
            data_dir=tmp_path,
            node_id="test-node",
            deployment_class="edge",
            entropy_sources=["os_urandom", "clock_jitter"],
            model_path=missing_model,
        )

        status = coord.status()
        assert status.ready is False
        assert status.model_loaded is False
        assert "not found" in (status.readiness_detail or "")

    def test_each_response_has_unique_beacon(self, tmp_path):
        from andyria.coordinator import Coordinator
        from andyria.models import AndyriaRequest

        coord = _make_coordinator(tmp_path)
        r1 = asyncio.run(coord.process(AndyriaRequest(input="First")))
        r2 = asyncio.run(coord.process(AndyriaRequest(input="Second")))
        assert r1.entropy_beacon_id != r2.entropy_beacon_id

    def test_default_agent_exists(self, tmp_path):
        coord = _make_coordinator(tmp_path)
        agents = coord.list_agents()
        assert any(a.agent_id == "default" for a in agents)

    def test_create_and_clone_agent(self, tmp_path):
        from andyria.models import AgentCloneRequest, AgentCreateRequest

        coord = _make_coordinator(tmp_path)
        created = coord.create_agent(AgentCreateRequest(name="ResearchNode", model="gpt-5.3"))
        assert created.name == "ResearchNode"

        cloned = coord.clone_agent(created.agent_id, AgentCloneRequest(name="ResearchNode Copy"))
        assert cloned is not None
        assert cloned.agent_id != created.agent_id
        assert cloned.name == "ResearchNode Copy"
        assert created.persona is not None
        assert cloned.persona is not None

    def test_spawned_agent_defaults_to_active_llm(self, tmp_path):
        from andyria.coordinator import Coordinator
        from andyria.models import AgentCreateRequest

        coord = Coordinator(
            data_dir=tmp_path,
            node_id="test-node",
            deployment_class="edge",
            entropy_sources=["os_urandom", "clock_jitter"],
            ollama_url="http://localhost:11434",
            ollama_model="dolphin-llama3:latest",
        )
        created = coord.create_agent(AgentCreateRequest(name="LLMBound"))
        assert created.model == "dolphin-llama3:latest"

    def test_infer_uses_requested_agent_id(self, tmp_path):
        from andyria.models import AgentCreateRequest, AndyriaRequest

        coord = _make_coordinator(tmp_path)
        created = coord.create_agent(
            AgentCreateRequest(
                name="PromptedAgent",
                system_prompt="You are terse.",
            )
        )
        response = asyncio.run(
            coord.process(AndyriaRequest(input="Say hi", agent_id=created.agent_id))
        )
        assert response.agent_id == created.agent_id

    def test_create_update_delete_tab(self, tmp_path):
        from andyria.models import TabCreateRequest, TabUpdateRequest, ViewportMode

        coord = _make_coordinator(tmp_path)
        created = coord.create_tab(TabCreateRequest())
        assert created.agent_id == "default"

        updated = coord.update_tab(
            created.tab_id,
            TabUpdateRequest(viewport_mode=ViewportMode.GRAPH),
        )
        assert updated is not None
        assert updated.viewport_mode == ViewportMode.GRAPH

        deleted = coord.delete_tab(created.tab_id)
        assert deleted is not None
        assert coord.get_tab(created.tab_id) is None

    def test_query_events_filters_by_event_type(self, tmp_path):
        from andyria.models import AgentCreateRequest, EventType

        coord = _make_coordinator(tmp_path)
        coord.create_agent(AgentCreateRequest(name="Filterable"))
        events = coord.query_events(event_type=EventType.AGENT_CREATED)
        assert events
        assert all(event.event_type == EventType.AGENT_CREATED for event in events)

    def test_event_subscription_receives_control_events(self, tmp_path):
        from andyria.models import AgentCreateRequest

        coord = _make_coordinator(tmp_path)
        subscriber = coord.subscribe_events()
        created = coord.create_agent(AgentCreateRequest(name="SubAgent"))

        item = subscriber.get(timeout=1.0)
        coord.unsubscribe_events(subscriber)

        event = item["event"]
        metadata = item["metadata"]
        assert event.event_type.value == "agent_created"
        assert metadata["agent_id"] == created.agent_id


class TestTools:
    def test_list_tools_returns_builtins(self, tmp_path):
        coord = _make_coordinator(tmp_path)
        tools = coord.list_tools()
        assert "echo" in tools
        assert "timestamp" in tools
        assert "word_count" in tools

    def test_echo_dispatch(self, tmp_path):
        coord = _make_coordinator(tmp_path)
        result = coord._tools.dispatch("echo", "hello world")
        assert result == "hello world"

    def test_word_count_dispatch(self, tmp_path):
        coord = _make_coordinator(tmp_path)
        result = coord._tools.dispatch("word_count", "one two three")
        assert result == "3"

    def test_unknown_tool_raises(self, tmp_path):
        coord = _make_coordinator(tmp_path)
        with pytest.raises(KeyError):
            coord._tools.dispatch("nonexistent_tool")

    def test_tool_task_emits_events(self, tmp_path):
        from andyria.models import AndyriaRequest, EventType

        coord = _make_coordinator(tmp_path)
        # A request that triggers echo tool dispatch
        request = AndyriaRequest(input="echo hello from tool")
        asyncio.run(coord.process(request))
        # Verify TOOL_CALL events were emitted if planner selected TOOL type
        tool_calls = coord.query_events(event_type=EventType.TOOL_CALL)
        tool_results = coord.query_events(event_type=EventType.TOOL_RESULT)
        # Both counts must be equal; may be 0 if planner chose a different task type
        assert len(tool_calls) == len(tool_results)


class TestChains:
    def test_create_chain(self, tmp_path):
        from andyria.models import ChainCreateRequest

        coord = _make_coordinator(tmp_path)
        chain = coord.create_chain(ChainCreateRequest(
            name="Test Pipeline",
            agent_ids=["default"],
        ))
        assert chain.chain_id.startswith("chain-")
        assert chain.name == "Test Pipeline"
        assert chain.agent_ids == ["default"]
        assert chain.active is True

    def test_list_chains(self, tmp_path):
        from andyria.models import ChainCreateRequest

        coord = _make_coordinator(tmp_path)
        coord.create_chain(ChainCreateRequest(name="A", agent_ids=["default"]))
        coord.create_chain(ChainCreateRequest(name="B", agent_ids=["default"]))
        chains = coord.list_chains()
        assert len(chains) == 2

    def test_delete_chain(self, tmp_path):
        from andyria.models import ChainCreateRequest

        coord = _make_coordinator(tmp_path)
        chain = coord.create_chain(ChainCreateRequest(name="Deletable", agent_ids=["default"]))
        deleted = coord.delete_chain(chain.chain_id)
        assert deleted is not None
        assert coord.get_chain(chain.chain_id).active is False
        assert coord.list_chains() == []

    def test_create_chain_rejects_invalid_agent(self, tmp_path):
        from andyria.models import ChainCreateRequest

        coord = _make_coordinator(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            coord.create_chain(ChainCreateRequest(name="Bad", agent_ids=["ghost-agent"]))

    def test_run_chain_returns_response(self, tmp_path):
        from andyria.models import ChainCreateRequest

        coord = _make_coordinator(tmp_path)
        chain = coord.create_chain(ChainCreateRequest(
            name="Single-step",
            agent_ids=["default"],
        ))
        response = asyncio.run(coord.run_chain(chain.chain_id, "Hello"))
        assert response.output
        assert response.agent_id == "default"

    def test_run_chain_emits_lifecycle_events(self, tmp_path):
        from andyria.models import ChainCreateRequest, EventType

        coord = _make_coordinator(tmp_path)
        chain = coord.create_chain(ChainCreateRequest(name="Lifecycle", agent_ids=["default"]))
        asyncio.run(coord.run_chain(chain.chain_id, "test input"))

        started = coord.query_events(event_type=EventType.CHAIN_STARTED)
        completed = coord.query_events(event_type=EventType.CHAIN_COMPLETED)
        steps = coord.query_events(event_type=EventType.CHAIN_STEP)

        assert len(started) >= 1
        assert len(completed) >= 1
        assert len(steps) >= 1

    def test_run_unknown_chain_raises(self, tmp_path):
        coord = _make_coordinator(tmp_path)
        with pytest.raises(ValueError):
            asyncio.run(coord.run_chain("chain-nonexistent", "input"))
