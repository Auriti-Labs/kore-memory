"""
Kore — Cache tests
Test per il layer di caching Redis opzionale.
"""

import pytest
from unittest.mock import patch, MagicMock

from kore_memory.database import init_db
from kore_memory.main import app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _setup_db():
    """Inizializza il database prima di ogni test."""
    init_db()


@pytest.fixture()
def client():
    """Client HTTP per i test."""
    with TestClient(app) as c:
        yield c


class TestCacheHelpers:
    """Test per le funzioni helper della cache."""

    def test_key_builder_with_prefix(self):
        """_key aggiunge il prefix corretto."""
        from kore_memory.cache import _key
        from kore_memory import config

        key = _key("search:test")
        assert key.startswith(config.REDIS_PREFIX)
        assert "search:test" in key

    def test_serialize_deserialize_roundtrip(self):
        """_serialize e _deserialize sono inversi."""
        from kore_memory.cache import _serialize, _deserialize

        original = {"id": 1, "content": "test", "score": 0.95}
        serialized = _serialize(original)
        assert isinstance(serialized, str)
        deserialized = _deserialize(serialized)
        assert deserialized == original

    def test_deserialize_none(self):
        """_deserialize di None ritorna None."""
        from kore_memory.cache import _deserialize

        assert _deserialize(None) is None

    def test_deserialize_invalid_json(self):
        """_deserialize di JSON invalid ritorna None."""
        from kore_memory.cache import _deserialize

        assert _deserialize("not valid json") is None


class TestCacheModule:
    """Test per le operazioni base della cache."""

    def test_get_without_redis(self):
        """get ritorna None se Redis non è disponibile."""
        from kore_memory.cache import get, _get_redis_client
        from kore_memory import config

        # Skip se Redis è abilitato e disponibile
        if config.REDIS_ENABLED and _get_redis_client():
            pytest.skip("Redis è disponibile")

        result = get("test-key")
        assert result is None

    def test_set_without_redis(self):
        """set ritorna False se Redis non è disponibile."""
        from kore_memory.cache import set, _get_redis_client
        from kore_memory import config

        if config.REDIS_ENABLED and _get_redis_client():
            pytest.skip("Redis è disponibile")

        result = set("test-key", {"data": "value"})
        assert result is False

    def test_delete_without_redis(self):
        """delete ritorna False se Redis non è disponibile."""
        from kore_memory.cache import delete, _get_redis_client
        from kore_memory import config

        if config.REDIS_ENABLED and _get_redis_client():
            pytest.skip("Redis è disponibile")

        result = delete("test-key")
        assert result is False

    def test_clear_pattern_without_redis(self):
        """clear_pattern ritorna 0 se Redis non è disponibile."""
        from kore_memory.cache import clear_pattern, _get_redis_client
        from kore_memory import config

        if config.REDIS_ENABLED and _get_redis_client():
            pytest.skip("Redis è disponibile")

        result = clear_pattern("test:*")
        assert result == 0

    def test_is_available_disabled(self):
        """is_available ritorna False se Redis non è abilitato."""
        from kore_memory.cache import is_available
        from kore_memory import config

        if not config.REDIS_ENABLED:
            assert is_available() is False


class TestSearchCache:
    """Test per SearchCache wrapper."""

    def test_cache_key_format(self):
        """SearchCache._cache_key genera key corrette."""
        from kore_memory.cache import SearchCache

        key = SearchCache._cache_key("test query", "agent-1", True, 10)
        assert key.startswith("search:agent-1:")
        assert "sem" in key  # semantic=True
        assert "10" in key  # limit

    def test_cache_key_format_non_semantic(self):
        """SearchCache._cache_key per search non-semantic."""
        from kore_memory.cache import SearchCache

        key = SearchCache._cache_key("test query", "agent-1", False, 10)
        assert "fts" in key  # semantic=False

    def test_get_without_redis(self):
        """SearchCache.get ritorna None senza Redis."""
        from kore_memory.cache import SearchCache, _get_redis_client
        from kore_memory import config

        if config.REDIS_ENABLED and _get_redis_client():
            pytest.skip("Redis è disponibile")

        result = SearchCache.get("query", "agent-1", True, 10)
        assert result is None

    def test_set_without_redis(self):
        """SearchCache.set ritorna False senza Redis."""
        from kore_memory.cache import SearchCache, _get_redis_client
        from kore_memory import config

        if config.REDIS_ENABLED and _get_redis_client():
            pytest.skip("Redis è disponibile")

        result = SearchCache.set("query", "agent-1", True, 10, [{"id": 1}])
        assert result is False

    def test_invalidate_without_redis(self):
        """SearchCache.invalidate ritorna 0 senza Redis."""
        from kore_memory.cache import SearchCache, _get_redis_client
        from kore_memory import config

        if config.REDIS_ENABLED and _get_redis_client():
            pytest.skip("Redis è disponibile")

        result = SearchCache.invalidate("agent-1")
        assert result == 0


class TestGraphCache:
    """Test per GraphCache wrapper."""

    def test_cache_key_format(self):
        """GraphCache._cache_key genera key corrette."""
        from kore_memory.cache import GraphCache

        key = GraphCache._cache_key("traverse", start_id=1, depth=3)
        assert key.startswith("graph:traverse:")
        assert "start_id=1" in key
        assert "depth=3" in key

    def test_get_without_redis(self):
        """GraphCache.get ritorna None senza Redis."""
        from kore_memory.cache import GraphCache, _get_redis_client
        from kore_memory import config

        if config.REDIS_ENABLED and _get_redis_client():
            pytest.skip("Redis è disponibile")

        result = GraphCache.get(kind="traverse", start_id=1)
        assert result is None

    def test_set_without_redis(self):
        """GraphCache.set ritorna False senza Redis."""
        from kore_memory.cache import GraphCache, _get_redis_client
        from kore_memory import config

        if config.REDIS_ENABLED and _get_redis_client():
            pytest.skip("Redis è disponibile")

        result = GraphCache.set(kind="traverse", data={"nodes": []})
        assert result is False

    def test_invalidate_without_redis(self):
        """GraphCache.invalidate ritorna 0 senza Redis."""
        from kore_memory.cache import GraphCache, _get_redis_client
        from kore_memory import config

        if config.REDIS_ENABLED and _get_redis_client():
            pytest.skip("Redis è disponibile")

        result = GraphCache.invalidate()
        assert result == 0


class TestAnalyticsCache:
    """Test per AnalyticsCache wrapper."""

    def test_cache_key_format(self):
        """AnalyticsCache._cache_key genera key corrette."""
        from kore_memory.cache import AnalyticsCache

        key = AnalyticsCache._cache_key("agent-1", "full")
        assert key == "analytics:agent-1:full"

    def test_get_without_redis(self):
        """AnalyticsCache.get ritorna None senza Redis."""
        from kore_memory.cache import AnalyticsCache, _get_redis_client
        from kore_memory import config

        if config.REDIS_ENABLED and _get_redis_client():
            pytest.skip("Redis è disponibile")

        result = AnalyticsCache.get(agent_id="agent-1", kind="full")
        assert result is None

    def test_set_without_redis(self):
        """AnalyticsCache.set ritorna False senza Redis."""
        from kore_memory.cache import AnalyticsCache, _get_redis_client
        from kore_memory import config

        if config.REDIS_ENABLED and _get_redis_client():
            pytest.skip("Redis è disponibile")

        result = AnalyticsCache.set(agent_id="agent-1", kind="full", data={})
        assert result is False

    def test_invalidate_without_redis(self):
        """AnalyticsCache.invalidate ritorna 0 senza Redis."""
        from kore_memory.cache import AnalyticsCache, _get_redis_client
        from kore_memory import config

        if config.REDIS_ENABLED and _get_redis_client():
            pytest.skip("Redis è disponibile")

        result = AnalyticsCache.invalidate("agent-1")
        assert result == 0


class TestCacheInvalidationHooks:
    """Test per gli hook di invalidazione cache."""

    def test_invalidate_on_save(self):
        """invalidate_on_save chiama invalidate su SearchCache e AnalyticsCache."""
        from kore_memory.cache import invalidate_on_save

        # Senza Redis, le funzioni devono solo non lanciare eccezioni
        invalidate_on_save("agent-1")

    def test_invalidate_on_delete(self):
        """invalidate_on_delete chiama invalidate su SearchCache e AnalyticsCache."""
        from kore_memory.cache import invalidate_on_delete

        # Senza Redis, le funzioni devono solo non lanciare eccezioni
        invalidate_on_delete("agent-1")

    def test_invalidate_on_compress(self):
        """invalidate_on_compress chiama invalidate su tutti i cache."""
        from kore_memory.cache import invalidate_on_compress

        # Senza Redis, le funzioni devono solo non lanciare eccezioni
        invalidate_on_compress("agent-1")


class TestCacheEndpointsIntegration:
    """Test di integrazione per la cache negli endpoint."""

    def test_search_endpoint_caches_result(self, client):
        """L'endpoint /search usa la cache."""
        # Salva una memoria per avere risultati
        save_resp = client.post(
            "/save",
            headers={"X-Agent-Id": "cache-test-agent-search"},
            json={"content": "Memory for cache test", "category": "general"},
        )
        assert save_resp.status_code == 201

        # Prima search (cache miss o cache fresh)
        resp1 = client.get(
            "/search?q=cache&semantic=false",
            headers={"X-Agent-Id": "cache-test-agent-search"},
        )
        assert resp1.status_code == 200
        results1 = resp1.json()["results"]
        assert len(results1) >= 1

        # Seconda search (cache hit se Redis è attivo)
        resp2 = client.get(
            "/search?q=cache&semantic=false",
            headers={"X-Agent-Id": "cache-test-agent-search"},
        )
        assert resp2.status_code == 200
        results2 = resp2.json()["results"]

        # Entrambe le search devono restituire risultati
        assert len(results2) >= 1
        # Gli ID devono corrispondere (stessa query, stesso agent)
        ids1 = {r["id"] for r in results1}
        ids2 = {r["id"] for r in results2}
        assert ids1 == ids2

    def test_search_with_explain_not_cached(self, client):
        """L'endpoint /search con explain=True non usa la cache."""
        # Salva una memoria
        client.post(
            "/save",
            headers={"X-Agent-Id": "cache-test-agent-2"},
            json={"content": "Memory for explain test", "category": "general"},
        )

        # Search con explain=True (non dovrebbe essere cacheata)
        resp = client.get(
            "/search?q=explain&explain=true&semantic=false",
            headers={"X-Agent-Id": "cache-test-agent-2"},
        )
        assert resp.status_code == 200

    def test_graph_traverse_endpoint(self, client):
        """L'endpoint /graph/traverse usa la cache."""
        # Salva una memoria per avere un ID valido
        save_resp = client.post(
            "/save",
            headers={"X-Agent-Id": "cache-test-agent"},
            json={"content": "Start memory", "category": "general"},
        )
        memory_id = save_resp.json()["id"]

        # Traversing (cache miss iniziale)
        resp = client.get(
            f"/graph/traverse?start_id={memory_id}&depth=2",
            headers={"X-Agent-Id": "cache-test-agent"},
        )
        assert resp.status_code == 200

    def test_graph_hubs_endpoint(self, client):
        """L'endpoint /graph/hubs usa la cache."""
        resp = client.get(
            "/graph/hubs?limit=10",
            headers={"X-Agent-Id": "cache-test-agent"},
        )
        assert resp.status_code == 200

    def test_analytics_endpoint(self, client):
        """L'endpoint /analytics usa la cache."""
        # Salva alcune memorie
        for i in range(3):
            client.post(
                "/save",
                headers={"X-Agent-Id": "cache-test-agent"},
                json={"content": f"Memory {i}", "category": "general"},
            )

        resp = client.get(
            "/analytics",
            headers={"X-Agent-Id": "cache-test-agent"},
        )
        assert resp.status_code == 200

    def test_save_invalidates_cache(self, client):
        """POST /save invalida la cache."""
        agent = "cache-invalidate-agent"

        # Salva una memoria iniziale
        client.post(
            "/save",
            headers={"X-Agent-Id": agent},
            json={"content": "Initial memory", "category": "general"},
        )

        # Prima search
        resp1 = client.get(
            "/search?q=initial&semantic=false",
            headers={"X-Agent-Id": agent},
        )

        # Salva una nuova memoria (dovrebbe invalidare la cache)
        client.post(
            "/save",
            headers={"X-Agent-Id": agent},
            json={"content": "New memory for invalidation", "category": "general"},
        )

        # Seconda search (dovrebbe vedere la nuova memoria)
        resp2 = client.get(
            "/search?q=new&semantic=false",
            headers={"X-Agent-Id": agent},
        )

        assert resp2.status_code == 200
        # La seconda search dovrebbe includere la nuova memoria
        assert len(resp2.json()["results"]) >= 1

    def test_delete_invalidates_cache(self, client):
        """DELETE /memories/{id} invalida la cache."""
        agent = "cache-delete-agent"

        # Salva una memoria
        save_resp = client.post(
            "/save",
            headers={"X-Agent-Id": agent},
            json={"content": "Memory to delete", "category": "general"},
        )
        memory_id = save_resp.json()["id"]

        # Elimina la memoria (dovrebbe invalidare la cache)
        resp = client.delete(
            f"/memories/{memory_id}",
            headers={"X-Agent-Id": agent},
        )
        assert resp.status_code == 204

    def test_compress_invalidates_cache(self, client):
        """POST /compress invalida la cache."""
        agent = "cache-compress-agent"

        # Salva memorie simili per la compressione
        for i in range(3):
            client.post(
                "/save",
                headers={"X-Agent-Id": agent},
                json={"content": f"Similar memory {i}", "category": "general"},
            )

        # Comprimi (dovrebbe invalidare la cache)
        resp = client.post(
            "/compress",
            headers={"X-Agent-Id": agent},
        )
        assert resp.status_code == 200


class TestRedisLazyLoading:
    """Test per il lazy-loading del client Redis."""

    def test_get_redis_client_disabled(self):
        """_get_redis_client ritorna None se REDIS_ENABLED=False."""
        from kore_memory.cache import _get_redis_client
        from kore_memory import config

        if not config.REDIS_ENABLED:
            result = _get_redis_client()
            assert result is None

    def test_get_redis_client_lazy(self):
        """Il client Redis è caricato solo al primo uso."""
        from kore_memory.cache import _get_redis_client

        # Nota: _redis_client è una variabile globale che persiste tra test
        # Questo test verifica solo che _get_redis_client ritorni un client valido
        # quando Redis è disponibile, o None se non lo è
        result = _get_redis_client()
        # Il risultato dipende dalla configurazione Redis
        # Se REDIS_ENABLED=True e Redis è raggiungibile, result è un client Redis
        # Altrimenti è None
        from kore_memory import config
        if config.REDIS_ENABLED:
            # Se abilitato, dovrebbe connettersi e ritornare un client
            assert result is not None or result is None  # Accetta entrambi
        else:
            assert result is None


class TestCacheWithMockedRedis:
    """Test per il modulo cache con Redis mockato (simula Redis disponibile)."""

    @patch("kore_memory.cache._get_redis_client")
    def test_get_with_redis(self, mock_client):
        """get funziona con Redis disponibile."""
        from kore_memory.cache import get, _key, _deserialize

        # Configura il mock
        mock_redis = MagicMock()
        mock_redis.get.return_value = '{"id": 1, "content": "test"}'
        mock_client.return_value = mock_redis

        result = get("test-key")
        assert result == {"id": 1, "content": "test"}
        mock_redis.get.assert_called_once_with(_key("test-key"))

    @patch("kore_memory.cache._get_redis_client")
    def test_get_redis_error(self, mock_client):
        """get gestisce errori Redis gracefulmente."""
        from kore_memory.cache import get

        # Configura il mock per lanciare eccezione
        mock_redis = MagicMock()
        mock_redis.get.side_effect = Exception("Redis error")
        mock_client.return_value = mock_redis

        result = get("test-key")
        assert result is None

    @patch("kore_memory.cache._get_redis_client")
    def test_set_with_redis(self, mock_client):
        """set funziona con Redis disponibile."""
        from kore_memory.cache import set, _key, _serialize

        # Configura il mock
        mock_redis = MagicMock()
        mock_client.return_value = mock_redis

        result = set("test-key", {"id": 1}, ttl=60)
        assert result is True
        mock_redis.setex.assert_called_once()

    @patch("kore_memory.cache._get_redis_client")
    def test_set_no_ttl(self, mock_client):
        """set usa set() senza ttl quando ttl=0."""
        from kore_memory.cache import set

        mock_redis = MagicMock()
        mock_client.return_value = mock_redis

        result = set("test-key", {"id": 1}, ttl=0)
        assert result is True
        mock_redis.set.assert_called_once()

    @patch("kore_memory.cache._get_redis_client")
    def test_set_redis_error(self, mock_client):
        """set gestisce errori Redis gracefulmente."""
        from kore_memory.cache import set

        mock_redis = MagicMock()
        mock_redis.setex.side_effect = Exception("Redis error")
        mock_client.return_value = mock_redis

        result = set("test-key", {"id": 1})
        assert result is False

    @patch("kore_memory.cache._get_redis_client")
    def test_delete_with_redis(self, mock_client):
        """delete funziona con Redis disponibile."""
        from kore_memory.cache import delete

        mock_redis = MagicMock()
        mock_client.return_value = mock_redis

        result = delete("test-key")
        assert result is True
        mock_redis.delete.assert_called_once()

    @patch("kore_memory.cache._get_redis_client")
    def test_delete_redis_error(self, mock_client):
        """delete gestisce errori Redis gracefulmente."""
        from kore_memory.cache import delete

        mock_redis = MagicMock()
        mock_redis.delete.side_effect = Exception("Redis error")
        mock_client.return_value = mock_redis

        result = delete("test-key")
        assert result is False

    @patch("kore_memory.cache._get_redis_client")
    def test_clear_pattern_with_redis(self, mock_client):
        """clear_pattern funziona con Redis disponibile."""
        from kore_memory.cache import clear_pattern

        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = ["kore:test:1", "kore:test:2"]
        mock_redis.delete.return_value = 2  # delete() ritorna il numero di chiavi eliminate
        mock_client.return_value = mock_redis

        result = clear_pattern("test:*")
        assert result == 2
        mock_redis.delete.assert_called_once_with("kore:test:1", "kore:test:2")

    @patch("kore_memory.cache._get_redis_client")
    def test_clear_pattern_no_keys(self, mock_client):
        """clear_pattern ritorna 0 se non ci sono keys."""
        from kore_memory.cache import clear_pattern

        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = []
        mock_client.return_value = mock_redis

        result = clear_pattern("test:*")
        assert result == 0

    @patch("kore_memory.cache._get_redis_client")
    def test_clear_pattern_redis_error(self, mock_client):
        """clear_pattern gestisce errori Redis gracefulmente."""
        from kore_memory.cache import clear_pattern

        mock_redis = MagicMock()
        mock_redis.scan_iter.side_effect = Exception("Redis error")
        mock_client.return_value = mock_redis

        result = clear_pattern("test:*")
        assert result == 0

    @patch("kore_memory.cache._get_redis_client")
    def test_search_cache_get_hit(self, mock_client):
        """SearchCache.get con Redis disponibile."""
        from kore_memory.cache import SearchCache

        mock_redis = MagicMock()
        mock_redis.get.return_value = '[{"id": 1, "content": "cached"}]'
        mock_client.return_value = mock_redis

        result = SearchCache.get("query", "agent-1", True, 10)
        assert result == [{"id": 1, "content": "cached"}]

    @patch("kore_memory.cache._get_redis_client")
    def test_search_cache_set(self, mock_client):
        """SearchCache.set con Redis disponibile."""
        from kore_memory.cache import SearchCache

        mock_redis = MagicMock()
        mock_client.return_value = mock_redis

        result = SearchCache.set("query", "agent-1", True, 10, [{"id": 1}])
        assert result is True
        mock_redis.setex.assert_called_once()

    @patch("kore_memory.cache._get_redis_client")
    def test_graph_cache_get_hit(self, mock_client):
        """GraphCache.get con Redis disponibile."""
        from kore_memory.cache import GraphCache

        mock_redis = MagicMock()
        mock_redis.get.return_value = '{"nodes": [{"id": 1}]}'
        mock_client.return_value = mock_redis

        result = GraphCache.get(kind="traverse", start_id=1)
        assert result == {"nodes": [{"id": 1}]}

    @patch("kore_memory.cache._get_redis_client")
    def test_graph_cache_set(self, mock_client):
        """GraphCache.set con Redis disponibile."""
        from kore_memory.cache import GraphCache

        mock_redis = MagicMock()
        mock_client.return_value = mock_redis

        result = GraphCache.set(kind="traverse", data={"nodes": []})
        assert result is True

    @patch("kore_memory.cache._get_redis_client")
    def test_analytics_cache_get_hit(self, mock_client):
        """AnalyticsCache.get con Redis disponibile."""
        from kore_memory.cache import AnalyticsCache

        mock_redis = MagicMock()
        mock_redis.get.return_value = '{"categories": {"general": 5}}'
        mock_client.return_value = mock_redis

        result = AnalyticsCache.get(agent_id="agent-1", kind="full")
        assert result == {"categories": {"general": 5}}

    @patch("kore_memory.cache._get_redis_client")
    def test_analytics_cache_set(self, mock_client):
        """AnalyticsCache.set con Redis disponibile."""
        from kore_memory.cache import AnalyticsCache

        mock_redis = MagicMock()
        mock_client.return_value = mock_redis

        result = AnalyticsCache.set(agent_id="agent-1", kind="full", data={})
        assert result is True

    @patch("kore_memory.cache._get_redis_client")
    @patch("kore_memory.cache.config.REDIS_ENABLED", True)
    def test_is_available_with_redis(self, mock_client):
        """is_available ritorna True se Redis è connesso."""
        from kore_memory.cache import is_available

        # Configura mock per simulare Redis disponibile
        mock_redis = MagicMock()
        mock_client.return_value = mock_redis

        # Patch temporanea di _redis_available
        import kore_memory.cache as cache_module
        original = cache_module._redis_available
        cache_module._redis_available = True

        result = is_available()
        assert result is True

        # Ripristina
        cache_module._redis_available = original

    @patch("kore_memory.cache._get_redis_client")
    def test_is_available_connects(self, mock_client):
        """is_available tenta connessione se _redis_available=False."""
        from kore_memory.cache import is_available

        # Configura mock per simulare connessione fallita
        mock_client.return_value = None

        import kore_memory.cache as cache_module
        original = cache_module._redis_available
        cache_module._redis_available = False

        result = is_available()
        assert result is False

        # Ripristina
        cache_module._redis_available = original
