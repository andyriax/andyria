"""HTTP API tests for the Andyria FastAPI application."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def tmp_data(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def app(tmp_data: Path):
    from andyria.api import create_app
    from andyria.coordinator import Coordinator

    coord = Coordinator(
        data_dir=tmp_data,
        node_id="api-test-node",
        deployment_class="edge",
        entropy_sources=["os_urandom"],
    )
    return create_app(coordinator=coord)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_ok(self, client: AsyncClient):
        res = await client.get("/health")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] in ("ok", "degraded")
        assert "ready" in body
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_health_service_name(self, client: AsyncClient):
        body = (await client.get("/health")).json()
        assert body["service"] == "andyria"


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_returns_node(self, client: AsyncClient):
        res = await client.get("/v1/status")
        assert res.status_code == 200
        body = res.json()
        assert body["node_id"] == "api-test-node"
        assert "ready" in body
        assert "readiness_detail" in body

    @pytest.mark.asyncio
    async def test_status_deployment_class(self, client: AsyncClient):
        body = (await client.get("/v1/status")).json()
        assert body["deployment_class"] == "edge"


class TestInfer:
    @pytest.mark.asyncio
    async def test_infer_stateless(self, client: AsyncClient):
        res = await client.post("/v1/infer", json={"input": "Hello Andyria"})
        assert res.status_code == 200
        body = res.json()
        assert body["output"]
        assert body["session_id"] is None
        assert body["turn_number"] >= 1

    @pytest.mark.asyncio
    async def test_infer_with_session_increments_turn(self, client: AsyncClient):
        sid = "test-session-turn"
        r1 = await client.post("/v1/infer", json={"input": "First message", "session_id": sid})
        r2 = await client.post("/v1/infer", json={"input": "Second message", "session_id": sid})

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["turn_number"] == 1
        assert r2.json()["turn_number"] == 2

    @pytest.mark.asyncio
    async def test_infer_returns_session_id_back(self, client: AsyncClient):
        sid = "echo-session"
        body = (await client.post("/v1/infer", json={"input": "test", "session_id": sid})).json()
        assert body["session_id"] == sid

    @pytest.mark.asyncio
    async def test_infer_math(self, client: AsyncClient):
        body = (await client.post("/v1/infer", json={"input": "calculate 6 * 9"})).json()
        assert "54" in body["output"]

    @pytest.mark.asyncio
    async def test_infer_processing_ms(self, client: AsyncClient):
        body = (await client.post("/v1/infer", json={"input": "time this"})).json()
        assert body.get("processing_ms") is not None
        assert body["processing_ms"] >= 0

    @pytest.mark.asyncio
    async def test_infer_with_agent_id(self, client: AsyncClient):
        created = await client.post(
            "/v1/agents",
            json={
                "name": "Runtime Agent",
                "system_prompt": "You are a runtime-focused assistant.",
            },
        )
        agent_id = created.json()["agent_id"]

        body = (await client.post(
            "/v1/infer",
            json={"input": "hello", "agent_id": agent_id},
        )).json()
        assert body["agent_id"] == agent_id


class TestAgents:
    @pytest.mark.asyncio
    async def test_default_agent_exists(self, client: AsyncClient):
        body = (await client.get("/v1/agents")).json()
        assert any(a["agent_id"] == "default" for a in body)

    @pytest.mark.asyncio
    async def test_create_and_get_agent(self, client: AsyncClient):
        created = await client.post(
            "/v1/agents",
            json={
                "name": "ResearchNode",
                "model": "gpt-5.3",
                "tools": ["web", "fs"],
            },
        )
        assert created.status_code == 201
        agent = created.json()
        assert agent["name"] == "ResearchNode"
        assert agent["persona"] is not None
        assert agent["persona"]["codename"]
        assert agent["persona"]["seed"]

        fetched = await client.get(f"/v1/agents/{agent['agent_id']}")
        assert fetched.status_code == 200
        assert fetched.json()["agent_id"] == agent["agent_id"]

    @pytest.mark.asyncio
    async def test_agent_avatar_svg(self, client: AsyncClient):
        created = await client.post("/v1/agents", json={"name": "AvatarAgent"})
        agent_id = created.json()["agent_id"]

        avatar = await client.get(f"/v1/agents/{agent_id}/avatar.svg")
        assert avatar.status_code == 200
        assert avatar.headers["content-type"].startswith("image/svg+xml")
        assert "<svg" in avatar.text

    @pytest.mark.asyncio
    async def test_agent_skills_profile(self, client: AsyncClient):
        created = await client.post("/v1/agents", json={"name": "SkillAgent"})
        agent_id = created.json()["agent_id"]

        res = await client.get(f"/v1/agents/{agent_id}/skills")
        assert res.status_code == 200
        body = res.json()
        assert body["agent_id"] == agent_id
        assert "atm.iterative_thinking" in body["skills"]
        assert body["modes"]["auto_resume"] is True

    @pytest.mark.asyncio
    async def test_update_agent(self, client: AsyncClient):
        created = await client.post("/v1/agents", json={"name": "Mutable"})
        agent_id = created.json()["agent_id"]

        patched = await client.patch(
            f"/v1/agents/{agent_id}",
            json={"name": "Mutable v2", "active": True},
        )
        assert patched.status_code == 200
        assert patched.json()["name"] == "Mutable v2"

    @pytest.mark.asyncio
    async def test_clone_agent(self, client: AsyncClient):
        created = await client.post("/v1/agents", json={"name": "SourceAgent"})
        src_id = created.json()["agent_id"]

        cloned = await client.post(
            f"/v1/agents/{src_id}/clone",
            json={"name": "SourceAgent Clone"},
        )
        assert cloned.status_code == 201
        clone_body = cloned.json()
        assert clone_body["name"] == "SourceAgent Clone"
        assert clone_body["agent_id"] != src_id

    @pytest.mark.asyncio
    async def test_retire_agent(self, client: AsyncClient):
        created = await client.post("/v1/agents", json={"name": "RetireMe"})
        agent_id = created.json()["agent_id"]

        retired = await client.delete(f"/v1/agents/{agent_id}")
        assert retired.status_code == 200
        assert retired.json()["active"] is False

    @pytest.mark.asyncio
    async def test_agent_dev_workspace_unique_per_agent(self, client: AsyncClient):
        a1 = await client.post("/v1/agents", json={"name": "DevA"})
        a2 = await client.post("/v1/agents", json={"name": "DevB"})
        id1 = a1.json()["agent_id"]
        id2 = a2.json()["agent_id"]

        r1 = await client.get(f"/v1/agents/{id1}/dev")
        r2 = await client.get(f"/v1/agents/{id2}/dev")
        assert r1.status_code == 200
        assert r2.status_code == 200

        b1 = r1.json()
        b2 = r2.json()
        assert b1["agent_id"] == id1
        assert b2["agent_id"] == id2
        assert b1["workspace_path"] != b2["workspace_path"]
        assert b1["ide_url"] != b2["ide_url"]
        assert id1 in b1["ide_url"]
        assert id2 in b2["ide_url"]
        assert Path(b1["workspace_path"]).exists()
        assert Path(b2["workspace_path"]).exists()
        assert (Path(b1["workspace_path"]) / "README.md").exists()
        assert (Path(b1["workspace_path"]) / "agent.profile.json").exists()
        assert (Path(b1["workspace_path"]) / "avatar.svg").exists()
        assert (Path(b1["workspace_path"]) / "skills.imports.txt").exists()
        assert (Path(b1["workspace_path"]) / "cron.auto-develop").exists()
        assert (Path(b1["workspace_path"]) / "sleepmode.dreamscapes.json").exists()
        assert (Path(b1["workspace_path"]) / "workspace.manifest.json").exists()
        assert (Path(b1["workspace_path"]) / ".env.agent").exists()

        evt = await client.get("/v1/events", params={"event_type": "agent_dev_workspace_prepared"})
        assert evt.status_code == 200
        events = evt.json()
        assert any(e["event_type"] == "agent_dev_workspace_prepared" for e in events)

    @pytest.mark.asyncio
    async def test_agent_dev_workspace_missing_agent(self, client: AsyncClient):
        res = await client.get("/v1/agents/missing-agent/dev")
        assert res.status_code == 404


class TestTabs:
    @pytest.mark.asyncio
    async def test_tab_lifecycle(self, client: AsyncClient):
        created = await client.post(
            "/v1/tabs",
            json={"viewport_mode": "chat"},
        )
        assert created.status_code == 201
        tab = created.json()
        assert tab["tab_id"]
        assert tab["agent_id"] == "default"

        fetched = await client.get(f"/v1/tabs/{tab['tab_id']}")
        assert fetched.status_code == 200
        assert fetched.json()["tab_id"] == tab["tab_id"]

        updated = await client.patch(
            f"/v1/tabs/{tab['tab_id']}",
            json={"viewport_mode": "graph"},
        )
        assert updated.status_code == 200
        assert updated.json()["viewport_mode"] == "graph"

        deleted = await client.delete(f"/v1/tabs/{tab['tab_id']}")
        assert deleted.status_code == 200

        missing = await client.get(f"/v1/tabs/{tab['tab_id']}")
        assert missing.status_code == 404

    @pytest.mark.asyncio
    async def test_create_tab_rejects_unknown_agent(self, client: AsyncClient):
        res = await client.post(
            "/v1/tabs",
            json={"agent_id": "missing-agent"},
        )
        assert res.status_code == 400


class TestEventFilters:
    @pytest.mark.asyncio
    async def test_filter_events_by_type(self, client: AsyncClient):
        await client.post("/v1/agents", json={"name": "FilterTypeAgent"})
        res = await client.get("/v1/events", params={"event_type": "agent_created"})
        assert res.status_code == 200
        body = res.json()
        assert len(body) >= 1
        assert all(item["event_type"] == "agent_created" for item in body)

    @pytest.mark.asyncio
    async def test_filter_events_by_tab_id(self, client: AsyncClient):
        created = await client.post("/v1/tabs", json={})
        tab_id = created.json()["tab_id"]

        res = await client.get("/v1/events", params={"tab_id": tab_id})
        assert res.status_code == 200
        body = res.json()
        assert len(body) >= 1
        assert any(item["event_type"] == "tab_opened" for item in body)


class TestSession:
    @pytest.mark.asyncio
    async def test_get_session_after_infer(self, client: AsyncClient):
        sid = "get-session-test"
        await client.post("/v1/infer", json={"input": "store this", "session_id": sid})

        res = await client.get(f"/v1/session/{sid}")
        assert res.status_code == 200
        body = res.json()
        assert body["session_id"] == sid
        assert len(body["turns"]) >= 1

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, client: AsyncClient):
        res = await client.get("/v1/session/nonexistent-abc-123")
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_session_clears(self, client: AsyncClient):
        sid = "delete-me"
        await client.post("/v1/infer", json={"input": "remember", "session_id": sid})

        del_res = await client.delete(f"/v1/session/{sid}")
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "cleared"

        get_res = await client.get(f"/v1/session/{sid}")
        assert get_res.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_nonexistent_session_ok(self, client: AsyncClient):
        res = await client.delete("/v1/session/ghost-session")
        assert res.status_code == 200


class TestUI:
    @pytest.mark.asyncio
    async def test_root_returns_html(self, client: AsyncClient):
        res = await client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers.get("content-type", "")
        assert b"Andyria" in res.content


class TestTools:
    @pytest.mark.asyncio
    async def test_list_tools(self, client: AsyncClient):
        res = await client.get("/v1/tools")
        assert res.status_code == 200
        tools = res.json()
        assert "echo" in tools
        assert "timestamp" in tools
        assert "word_count" in tools


class TestChains:
    @pytest.mark.asyncio
    async def test_create_and_list_chain(self, client: AsyncClient):
        created = await client.post(
            "/v1/chains",
            json={"name": "Test Pipeline", "agent_ids": ["default"]},
        )
        assert created.status_code == 201
        chain = created.json()
        assert chain["name"] == "Test Pipeline"
        assert chain["agent_ids"] == ["default"]
        assert chain["active"] is True

        listed = await client.get("/v1/chains")
        assert listed.status_code == 200
        assert any(c["chain_id"] == chain["chain_id"] for c in listed.json())

    @pytest.mark.asyncio
    async def test_get_chain(self, client: AsyncClient):
        created = await client.post(
            "/v1/chains",
            json={"name": "Get Me", "agent_ids": ["default"]},
        )
        chain_id = created.json()["chain_id"]
        res = await client.get(f"/v1/chains/{chain_id}")
        assert res.status_code == 200
        assert res.json()["chain_id"] == chain_id

    @pytest.mark.asyncio
    async def test_get_chain_not_found(self, client: AsyncClient):
        res = await client.get("/v1/chains/chain-nonexistent")
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_chain(self, client: AsyncClient):
        created = await client.post(
            "/v1/chains",
            json={"name": "Deletable", "agent_ids": ["default"]},
        )
        chain_id = created.json()["chain_id"]
        deleted = await client.delete(f"/v1/chains/{chain_id}")
        assert deleted.status_code == 200
        assert deleted.json()["active"] is False

    @pytest.mark.asyncio
    async def test_run_chain(self, client: AsyncClient):
        created = await client.post(
            "/v1/chains",
            json={"name": "Runnable", "agent_ids": ["default"]},
        )
        chain_id = created.json()["chain_id"]
        run = await client.post(
            f"/v1/chains/{chain_id}/run",
            json={"input": "Hello from chain test"},
        )
        assert run.status_code == 200
        assert run.json()["output"]

    @pytest.mark.asyncio
    async def test_run_chain_invalid_agent(self, client: AsyncClient):
        res = await client.post(
            "/v1/chains",
            json={"name": "Bad Chain", "agent_ids": ["ghost-999"]},
        )
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_run_unknown_chain(self, client: AsyncClient):
        res = await client.post(
            "/v1/chains/chain-doesnotexist/run",
            json={"input": "test"},
        )
        assert res.status_code == 404


class TestDemo:
    @pytest.mark.asyncio
    async def test_demo_status_initially_inactive(self, client: AsyncClient):
        res = await client.get("/v1/demo")
        assert res.status_code == 200
        data = res.json()
        assert data["active"] is False

    @pytest.mark.asyncio
    async def test_demo_start_creates_agents(self, client: AsyncClient):
        res = await client.post("/v1/demo/start")
        assert res.status_code == 201
        data = res.json()
        assert data["active"] is True
        assert len(data["agent_ids"]) == 3
        assert len(data["session_ids"]) == 3
        assert "Demo" in data["message"]

    @pytest.mark.asyncio
    async def test_demo_status_active_after_start(self, client: AsyncClient):
        await client.post("/v1/demo/start")
        res = await client.get("/v1/demo")
        assert res.status_code == 200
        assert res.json()["active"] is True

    @pytest.mark.asyncio
    async def test_demo_start_idempotent(self, client: AsyncClient):
        r1 = await client.post("/v1/demo/start")
        r2 = await client.post("/v1/demo/start")
        assert r1.status_code == 201
        assert r2.status_code == 201
        # Second call returns same state (already active)
        assert r1.json()["active"] is True
        assert r2.json()["active"] is True

    @pytest.mark.asyncio
    async def test_demo_stop(self, client: AsyncClient):
        await client.post("/v1/demo/start")
        res = await client.post("/v1/demo/stop")
        assert res.status_code == 200
        data = res.json()
        assert data["active"] is False

    @pytest.mark.asyncio
    async def test_demo_agents_have_personas(self, client: AsyncClient):
        start = await client.post("/v1/demo/start")
        agent_ids = start.json()["agent_ids"]
        for agent_id in agent_ids:
            agent_res = await client.get(f"/v1/agents/{agent_id}")
            assert agent_res.status_code == 200
            agent = agent_res.json()
            assert agent["persona"] is not None
