"""
Kore — Test del Client SDK
Verifica KoreClient e AsyncKoreClient contro il server ASGI.

I test di integrazione usano AsyncKoreClient (ASGITransport è solo async).
I test unit verificano helpers, eccezioni, e headers senza rete.
"""

import pytest

from kore_memory.main import app, _rate_buckets  # noqa: E402

import httpx  # noqa: E402

from kore_memory.client import (  # noqa: E402
    AsyncKoreClient,
    KoreAuthError,
    KoreClient,
    KoreError,
    KoreNotFoundError,
    KoreRateLimitError,
    KoreServerError,
    KoreValidationError,
    _build_headers,
    _raise_for_status,
)
from kore_memory.models import (  # noqa: E402
    BatchSaveResponse,
    CleanupExpiredResponse,
    CompressRunResponse,
    DecayRunResponse,
    MemoryExportResponse,
    MemoryImportResponse,
    MemorySaveResponse,
    MemorySearchResponse,
    RelationResponse,
    TagResponse,
)


# ── Helper: client async che usa il transport ASGI (zero rete) ───────────────


def _make_async_client(agent_id: str = "sdk-test") -> AsyncKoreClient:
    """Crea un AsyncKoreClient che punta al server ASGI in-process."""
    kc = AsyncKoreClient.__new__(AsyncKoreClient)
    kc.base_url = "http://testserver"
    kc.agent_id = agent_id
    kc._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers=_build_headers(None, agent_id),
        timeout=10.0,
    )
    return kc


# ── Test unit: _build_headers ────────────────────────────────────────────────


class TestBuildHeaders:
    def test_headers_senza_api_key(self):
        h = _build_headers(None, "my-agent")
        assert h == {"X-Agent-Id": "my-agent"}
        assert "X-Kore-Key" not in h

    def test_headers_con_api_key(self):
        h = _build_headers("secret-key", "my-agent")
        assert h["X-Agent-Id"] == "my-agent"
        assert h["X-Kore-Key"] == "secret-key"


# ── Test unit: _raise_for_status ─────────────────────────────────────────────


class TestRaiseForStatus:
    def test_successo_non_alza_eccezione(self):
        r = httpx.Response(200, json={"ok": True})
        _raise_for_status(r)  # non deve alzare eccezione

    def test_401_alza_auth_error(self):
        r = httpx.Response(401, json={"detail": "No auth"})
        with pytest.raises(KoreAuthError) as exc_info:
            _raise_for_status(r)
        assert exc_info.value.status_code == 401

    def test_403_alza_auth_error(self):
        r = httpx.Response(403, json={"detail": "Forbidden"})
        with pytest.raises(KoreAuthError) as exc_info:
            _raise_for_status(r)
        assert exc_info.value.status_code == 403

    def test_404_alza_not_found_error(self):
        r = httpx.Response(404, json={"detail": "Not found"})
        with pytest.raises(KoreNotFoundError) as exc_info:
            _raise_for_status(r)
        assert exc_info.value.status_code == 404

    def test_422_alza_validation_error(self):
        r = httpx.Response(422, json={"detail": "Invalid"})
        with pytest.raises(KoreValidationError) as exc_info:
            _raise_for_status(r)
        assert exc_info.value.status_code == 422

    def test_429_alza_rate_limit_error(self):
        r = httpx.Response(429, json={"detail": "Rate limit"})
        with pytest.raises(KoreRateLimitError) as exc_info:
            _raise_for_status(r)
        assert exc_info.value.status_code == 429

    def test_500_alza_server_error(self):
        r = httpx.Response(500, json={"detail": "Server error"})
        with pytest.raises(KoreServerError) as exc_info:
            _raise_for_status(r)
        assert exc_info.value.status_code == 500

    def test_status_generico_alza_kore_error(self):
        r = httpx.Response(418, json={"detail": "I'm a teapot"})
        with pytest.raises(KoreError) as exc_info:
            _raise_for_status(r)
        assert exc_info.value.status_code == 418

    def test_body_non_json(self):
        """Se il body non è JSON, usa il testo grezzo come detail."""
        r = httpx.Response(500, text="Internal error")
        with pytest.raises(KoreServerError) as exc_info:
            _raise_for_status(r)
        assert exc_info.value.detail == "Internal error"


# ── Test unit: KoreClient init e struttura ───────────────────────────────────


class TestKoreClientInit:
    def test_init_default(self):
        kc = KoreClient.__new__(KoreClient)
        # Verifica che la classe abbia i metodi attesi
        metodi = [
            "save", "save_batch", "search", "timeline", "delete",
            "add_tags", "get_tags", "remove_tags", "search_by_tag",
            "add_relation", "get_relations",
            "decay_run", "compress", "cleanup",
            "export_memories", "import_memories", "health",
            "close", "__enter__", "__exit__",
        ]
        for m in metodi:
            assert hasattr(kc, m), f"Metodo mancante: {m}"

    def test_async_client_ha_tutti_i_metodi(self):
        akc = AsyncKoreClient.__new__(AsyncKoreClient)
        metodi = [
            "save", "save_batch", "search", "timeline", "delete",
            "add_tags", "get_tags", "remove_tags", "search_by_tag",
            "add_relation", "get_relations",
            "decay_run", "compress", "cleanup",
            "export_memories", "import_memories", "health",
            "close", "__aenter__", "__aexit__",
        ]
        for m in metodi:
            assert hasattr(akc, m), f"Metodo mancante: {m}"


# ── Test unit: gerarchia eccezioni ───────────────────────────────────────────


class TestExceptionHierarchy:
    def test_tutte_ereditano_kore_error(self):
        assert issubclass(KoreAuthError, KoreError)
        assert issubclass(KoreNotFoundError, KoreError)
        assert issubclass(KoreValidationError, KoreError)
        assert issubclass(KoreRateLimitError, KoreError)
        assert issubclass(KoreServerError, KoreError)

    def test_kore_error_attributi(self):
        e = KoreError("test", status_code=500, detail={"key": "value"})
        assert str(e) == "test"
        assert e.status_code == 500
        assert e.detail == {"key": "value"}


# ── Test integrazione: AsyncKoreClient ───────────────────────────────────────


class TestAsyncKoreClientCore:
    """Test core: save, search, timeline, delete, batch."""

    def setup_method(self):
        _rate_buckets.clear()

    @pytest.mark.anyio
    async def test_save_ritorna_modello(self):
        async with _make_async_client() as kore:
            result = await kore.save("SDK test memory content", category="project")
            assert isinstance(result, MemorySaveResponse)
            assert result.id > 0
            assert result.importance >= 1

    @pytest.mark.anyio
    async def test_save_con_ttl(self):
        async with _make_async_client() as kore:
            result = await kore.save("SDK TTL memory test data", ttl_hours=48)
            assert isinstance(result, MemorySaveResponse)
            assert result.id > 0

    @pytest.mark.anyio
    async def test_save_validation_error(self):
        async with _make_async_client() as kore:
            with pytest.raises(KoreValidationError):
                await kore.save("ab")  # troppo corto

    @pytest.mark.anyio
    async def test_search_ritorna_modello(self):
        async with _make_async_client() as kore:
            await kore.save("SDK search target XYZQ unique", category="project")
            result = await kore.search("XYZQ", semantic=False)
            assert isinstance(result, MemorySearchResponse)
            assert result.total >= 1

    @pytest.mark.anyio
    async def test_search_con_offset(self):
        async with _make_async_client() as kore:
            for i in range(4):
                await kore.save(f"SDK async pagination item {i} marker ASDKPG")
            result = await kore.search("ASDKPG", limit=2, offset=1, semantic=False)
            assert isinstance(result, MemorySearchResponse)
            with pytest.deprecated_call(match="deprecated"):
                assert result.offset == 1

    @pytest.mark.anyio
    async def test_timeline_ritorna_modello(self):
        async with _make_async_client() as kore:
            await kore.save("SDK timeline async subject test")
            result = await kore.timeline("SDK timeline async")
            assert isinstance(result, MemorySearchResponse)

    @pytest.mark.anyio
    async def test_delete_esistente(self):
        async with _make_async_client() as kore:
            saved = await kore.save("SDK async memory to delete now")
            assert await kore.delete(saved.id) is True

    @pytest.mark.anyio
    async def test_delete_inesistente(self):
        async with _make_async_client() as kore:
            assert await kore.delete(999999) is False

    @pytest.mark.anyio
    async def test_save_batch_ritorna_modello(self):
        async with _make_async_client() as kore:
            result = await kore.save_batch([
                {"content": "SDK batch alpha item", "category": "general"},
                {"content": "SDK batch beta item", "category": "project", "importance": 3},
            ])
            assert isinstance(result, BatchSaveResponse)
            assert result.total == 2
            assert len(result.saved) == 2


class TestAsyncKoreClientTags:
    """Test tags: add, get, remove, search by tag."""

    def setup_method(self):
        _rate_buckets.clear()

    @pytest.mark.anyio
    async def test_add_e_get_tags(self):
        async with _make_async_client() as kore:
            saved = await kore.save("SDK async memory for tag test")
            tag_r = await kore.add_tags(saved.id, ["python", "sdk"])
            assert isinstance(tag_r, TagResponse)
            assert tag_r.count == 2
            get_r = await kore.get_tags(saved.id)
            assert "python" in get_r.tags
            assert "sdk" in get_r.tags

    @pytest.mark.anyio
    async def test_remove_tags(self):
        async with _make_async_client() as kore:
            saved = await kore.save("SDK async memory for tag removal")
            await kore.add_tags(saved.id, ["keep", "remove"])
            result = await kore.remove_tags(saved.id, ["remove"])
            assert isinstance(result, TagResponse)
            assert "keep" in result.tags
            assert "remove" not in result.tags

    @pytest.mark.anyio
    async def test_search_by_tag(self):
        async with _make_async_client() as kore:
            saved = await kore.save("SDK async memory tagged unique")
            await kore.add_tags(saved.id, ["sdk-async-unique"])
            result = await kore.search_by_tag("sdk-async-unique")
            assert isinstance(result, MemorySearchResponse)
            assert result.total >= 1
            ids = [m.id for m in result.results]
            assert saved.id in ids


class TestAsyncKoreClientRelations:
    """Test relazioni: add, get."""

    def setup_method(self):
        _rate_buckets.clear()

    @pytest.mark.anyio
    async def test_add_e_get_relations(self):
        async with _make_async_client() as kore:
            s1 = await kore.save("SDK async relation source memory")
            s2 = await kore.save("SDK async relation target memory")
            rel_r = await kore.add_relation(s1.id, s2.id, "depends_on")
            assert isinstance(rel_r, RelationResponse)
            assert rel_r.total >= 1
            get_r = await kore.get_relations(s1.id)
            assert get_r.total >= 1
            assert get_r.relations[0]["relation"] == "depends_on"


class TestAsyncKoreClientMaintenance:
    """Test manutenzione: decay, compress, cleanup."""

    def setup_method(self):
        _rate_buckets.clear()

    @pytest.mark.anyio
    async def test_decay_run(self):
        async with _make_async_client() as kore:
            result = await kore.decay_run()
            assert isinstance(result, DecayRunResponse)
            assert result.updated >= 0

    @pytest.mark.anyio
    async def test_compress(self):
        async with _make_async_client() as kore:
            result = await kore.compress()
            assert isinstance(result, CompressRunResponse)
            assert "clusters_found" in result.model_dump()

    @pytest.mark.anyio
    async def test_cleanup(self):
        async with _make_async_client() as kore:
            result = await kore.cleanup()
            assert isinstance(result, CleanupExpiredResponse)
            assert result.removed >= 0


class TestAsyncKoreClientBackup:
    """Test export/import."""

    def setup_method(self):
        _rate_buckets.clear()

    @pytest.mark.anyio
    async def test_export_ritorna_memorie(self):
        async with _make_async_client() as kore:
            await kore.save("SDK async export test memory data")
            result = await kore.export_memories()
            assert isinstance(result, MemoryExportResponse)
            assert result.total >= 1

    @pytest.mark.anyio
    async def test_import_memorie(self):
        async with _make_async_client(agent_id="sdk-import-async") as kore:
            result = await kore.import_memories([
                {"content": "Imported SDK async alpha", "category": "general", "importance": 2},
            ])
            assert isinstance(result, MemoryImportResponse)
            assert result.imported == 1


class TestAsyncKoreClientUtility:
    """Test utility e context manager."""

    def setup_method(self):
        _rate_buckets.clear()

    @pytest.mark.anyio
    async def test_health(self):
        async with _make_async_client() as kore:
            result = await kore.health()
            assert isinstance(result, dict)
            assert result["status"] == "ok"
            assert "semantic_search" in result

    @pytest.mark.anyio
    async def test_context_manager_chiude_client(self):
        kore = _make_async_client()
        async with kore:
            result = await kore.health()
            assert result["status"] == "ok"
        assert kore._client.is_closed
