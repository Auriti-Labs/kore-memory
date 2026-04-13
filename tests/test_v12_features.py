"""
Test per le feature della v1.2.0 — Developer Experience release.
Verifica: SDK importance fix, GET /memories/{id}, cursor pagination,
integrazioni PydanticAI/OpenAI/LangChain, MCP HTTP transport.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from kore_memory.client import AsyncKoreClient, KoreClient
from kore_memory.database import get_connection
from kore_memory.main import app
from kore_memory.models import MemorySaveRequest
from kore_memory.repository import get_memory, save_memory

HEADERS = {"X-Agent-Id": "test-v12"}
client = TestClient(app)


def _save(content: str, **kwargs) -> int:
    """Salva una memoria e ritorna l'id."""
    req = MemorySaveRequest(content=content, **kwargs)
    mid, _, _conflicts = save_memory(req, agent_id="test-v12")
    return mid


def _cleanup():
    """Pulisce tutte le memorie dell'agente test-v12."""
    with get_connection() as conn:
        conn.execute("DELETE FROM memory_tags WHERE memory_id IN (SELECT id FROM memories WHERE agent_id = 'test-v12')")
        conn.execute("DELETE FROM memories WHERE agent_id = 'test-v12'")


# ── SDK importance default fix ─────────────────────────────────────────────────


class TestSDKImportanceDefault:
    def test_sync_save_default_importance_is_none(self):
        """KoreClient.save() deve avere importance=None come default (auto-scoring)."""
        import inspect
        sig = inspect.signature(KoreClient.save)
        default = sig.parameters["importance"].default
        assert default is None, f"Atteso None, ottenuto {default}"

    def test_async_save_default_importance_is_none(self):
        """AsyncKoreClient.save() deve avere importance=None come default."""
        import inspect
        sig = inspect.signature(AsyncKoreClient.save)
        default = sig.parameters["importance"].default
        assert default is None, f"Atteso None, ottenuto {default}"

    def test_sync_save_omits_importance_when_none(self):
        """Se importance=None, il payload non deve includere importance."""
        with patch.object(KoreClient, "__init__", lambda self, **kw: None):
            kc = KoreClient.__new__(KoreClient)
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.is_success = True
            mock_response.json.return_value = {"id": 1, "importance": 3, "message": "saved"}
            mock_client.post.return_value = mock_response
            kc._client = mock_client

            kc.save("test content")
            call_args = mock_client.post.call_args
            payload = call_args[1]["json"]
            assert "importance" not in payload, f"importance presente nel payload: {payload}"


# ── GET /memories/{id} endpoint ────────────────────────────────────────────────


class TestGetMemoryEndpoint:
    def setup_method(self):
        _cleanup()

    def test_get_memory_success(self):
        """GET /memories/{id} ritorna la memoria corretta."""
        mid = _save("Memoria per test get endpoint")
        r = client.get(f"/memories/{mid}", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == mid
        assert "Memoria per test get endpoint" in data["content"]

    def test_get_memory_not_found(self):
        """GET /memories/{id} ritorna 404 per ID inesistente."""
        r = client.get("/memories/999999", headers=HEADERS)
        assert r.status_code == 404

    def test_get_memory_agent_isolation(self):
        """GET /memories/{id} non accede a memorie di altri agent."""
        mid = _save("Memoria isolata per agent test")
        r = client.get(f"/memories/{mid}", headers={"X-Agent-Id": "altro-agente"})
        assert r.status_code == 404

    def test_get_memory_repository(self):
        """get_memory() ritorna MemoryRecord o None."""
        mid = _save("Test repository get_memory")
        mem = get_memory(mid, agent_id="test-v12")
        assert mem is not None
        assert mem.id == mid
        assert "Test repository get_memory" in mem.content

        none_mem = get_memory(999999, agent_id="test-v12")
        assert none_mem is None


# ── SDK cursor pagination ──────────────────────────────────────────────────────


class TestSDKCursorPagination:
    def test_sync_search_has_cursor_param(self):
        """KoreClient.search() deve accettare il parametro cursor."""
        import inspect
        sig = inspect.signature(KoreClient.search)
        assert "cursor" in sig.parameters

    def test_sync_timeline_has_cursor_param(self):
        """KoreClient.timeline() deve accettare il parametro cursor."""
        import inspect
        sig = inspect.signature(KoreClient.timeline)
        assert "cursor" in sig.parameters

    def test_async_search_has_cursor_param(self):
        """AsyncKoreClient.search() deve accettare il parametro cursor."""
        import inspect
        sig = inspect.signature(AsyncKoreClient.search)
        assert "cursor" in sig.parameters

    def test_async_timeline_has_cursor_param(self):
        """AsyncKoreClient.timeline() deve accettare il parametro cursor."""
        import inspect
        sig = inspect.signature(AsyncKoreClient.timeline)
        assert "cursor" in sig.parameters


# ── SDK get() method ───────────────────────────────────────────────────────────


class TestSDKGetMethod:
    def test_sync_client_has_get(self):
        """KoreClient deve avere il metodo get()."""
        assert hasattr(KoreClient, "get")

    def test_async_client_has_get(self):
        """AsyncKoreClient deve avere il metodo get()."""
        assert hasattr(AsyncKoreClient, "get")


# ── API docs examples (openapi_examples) ──────────────────────────────────────


class TestOpenAPIExamples:
    def test_save_request_has_examples(self):
        """MemorySaveRequest deve avere examples nel json_schema."""
        schema = MemorySaveRequest.model_json_schema()
        assert "examples" in schema, "MemorySaveRequest manca examples in json_schema"

    def test_openapi_schema_accessible(self):
        """L'endpoint /openapi.json deve essere accessibile."""
        r = client.get("/openapi.json")
        assert r.status_code == 200
        schema = r.json()
        assert "paths" in schema
        # Verifica che GET /memories/{memory_id} sia presente
        assert "/memories/{memory_id}" in schema["paths"]
        assert "get" in schema["paths"]["/memories/{memory_id}"]


# ── Integrazioni: import e struttura ──────────────────────────────────────────


class TestIntegrationImports:
    def test_pydantic_ai_module_exists(self):
        """Il modulo pydantic_ai deve esistere e avere le funzioni attese."""
        from kore_memory.integrations import pydantic_ai
        assert hasattr(pydantic_ai, "kore_toolset")
        assert hasattr(pydantic_ai, "create_kore_tools")

    def test_openai_agents_module_exists(self):
        """Il modulo openai_agents deve esistere e avere la funzione attesa."""
        from kore_memory.integrations import openai_agents
        assert hasattr(openai_agents, "kore_agent_tools")

    def test_langchain_chat_history_exists(self):
        """Il modulo langchain deve avere KoreChatMessageHistory."""
        from kore_memory.integrations import langchain
        assert hasattr(langchain, "KoreChatMessageHistory")

    def test_lazy_imports_from_init(self):
        """Le nuove classi devono essere accessibili via lazy-loading."""
        from kore_memory.integrations import __all__
        assert "kore_toolset" in __all__
        assert "kore_agent_tools" in __all__
        assert "KoreChatMessageHistory" in __all__

    def test_create_kore_tools_returns_dict(self):
        """create_kore_tools() deve ritornare un dict con save/search/timeline/delete."""
        from kore_memory.integrations.pydantic_ai import create_kore_tools
        tools = create_kore_tools(base_url="http://localhost:8765")
        assert "save" in tools
        assert "search" in tools
        assert "timeline" in tools
        assert "delete" in tools
        assert callable(tools["save"])


# ── MCP transport args ─────────────────────────────────────────────────────────


class TestMCPTransportArgs:
    def test_main_accepts_transport_arg(self):
        """mcp_server.main() deve accettare --transport."""
        pytest.importorskip("mcp", reason="mcp package not installed (optional dependency)")
        from kore_memory.mcp_server import main
        # Verifica che main sia definita (non possiamo eseguirla senza bloccare)
        assert callable(main)

    def test_mcp_server_has_argparse(self):
        """Il modulo mcp_server deve usare argparse per il parsing degli argomenti."""
        pytest.importorskip("mcp", reason="mcp package not installed (optional dependency)")
        import inspect

        import kore_memory.mcp_server as mcp_mod
        source = inspect.getsource(mcp_mod.main)
        assert "argparse" in source
        assert "streamable-http" in source
        assert "--transport" in source


# ── Pyproject.toml dependencies ──────────────────────────────────────────────


class TestOptionalDependencies:
    def test_pydantic_ai_in_pyproject(self):
        """pyproject.toml deve avere la dipendenza opzionale pydantic-ai."""
        import tomllib

        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)
        optional = data["project"]["optional-dependencies"]
        assert "pydantic-ai" in optional

    def test_openai_agents_in_pyproject(self):
        """pyproject.toml deve avere la dipendenza opzionale openai-agents."""
        import tomllib

        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)
        optional = data["project"]["optional-dependencies"]
        assert "openai-agents" in optional
