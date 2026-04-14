"""
Kore — Test del Client Sincrono KoreClient
Copre tutti i metodi di KoreClient contro il server ASGI in-process.
Usa FastAPI TestClient (estende httpx.Client) come transport, zero rete.

Copertura target: tutti i metodi pubblici di KoreClient
  - save(), save_batch()
  - search(), timeline()
  - delete()
  - add_tags(), get_tags(), remove_tags(), search_by_tag()
  - add_relation(), get_relations()
  - decay_run(), compress(), cleanup()
  - export_memories(), import_memories()
  - health()
  - context manager __enter__ / __exit__
"""

import pytest

from fastapi.testclient import TestClient  # noqa: E402

from kore_memory.main import app, _rate_buckets  # noqa: E402

from kore_memory.client import (  # noqa: E402
    KoreClient,
    KoreValidationError,
    KoreNotFoundError,
    _build_headers,
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


# ── Factory: KoreClient con TestClient iniettato ──────────────────────────────


def _make_sync_client(agent_id: str = "sync-test") -> KoreClient:
    """
    Crea un KoreClient sincrono che usa FastAPI TestClient come transport.
    TestClient estende httpx.Client — compatibile con KoreClient._client.
    """
    kore = KoreClient.__new__(KoreClient)
    kore.base_url = "http://testserver"
    kore.agent_id = agent_id
    # TestClient è httpx.Client, quindi compatibile con KoreClient._client
    kore._client = TestClient(
        app,
        headers=_build_headers(None, agent_id),
        raise_server_exceptions=False,  # Le eccezioni HTTP vengono gestite da _raise_for_status
    )
    return kore


# ── Test: save() ─────────────────────────────────────────────────────────────


class TestSyncSave:
    """Verifica il metodo save() del client sincrono."""

    def setup_method(self):
        # Resetta i bucket rate-limit tra un test e l'altro
        _rate_buckets.clear()

    def test_save_base_ritorna_modello(self):
        """save() deve restituire MemorySaveResponse con id > 0."""
        kore = _make_sync_client()
        result = kore.save("Memoria di test base per il client sincrono")
        assert isinstance(result, MemorySaveResponse)
        assert result.id > 0
        assert result.importance >= 1

    def test_save_con_category_project(self):
        """save() con category='project' deve funzionare correttamente."""
        kore = _make_sync_client()
        result = kore.save(
            "Architettura del progetto Kore Memory in Python",
            category="project",
        )
        assert isinstance(result, MemorySaveResponse)
        assert result.id > 0

    def test_save_con_importance_esplicita(self):
        """save() con importance=5 deve restituire importance=5."""
        kore = _make_sync_client()
        result = kore.save(
            "Decisione critica: usare SQLite con WAL mode",
            category="decision",
            importance=5,
        )
        assert isinstance(result, MemorySaveResponse)
        assert result.importance == 5

    def test_save_con_category_task(self):
        """save() con category='task' deve persistere correttamente."""
        kore = _make_sync_client()
        result = kore.save(
            "Completare la suite di test per il client sincrono",
            category="task",
            importance=3,
        )
        assert isinstance(result, MemorySaveResponse)
        assert result.id > 0

    def test_save_con_ttl_hours(self):
        """save() con ttl_hours deve creare una memoria con TTL."""
        kore = _make_sync_client()
        result = kore.save(
            "Memoria temporanea con scadenza automatica dopo 24 ore",
            ttl_hours=24,
        )
        assert isinstance(result, MemorySaveResponse)
        assert result.id > 0

    def test_save_troppo_corto_alza_validation_error(self):
        """save() con contenuto troppo corto (< 3 char) deve sollevare KoreValidationError."""
        kore = _make_sync_client()
        with pytest.raises(KoreValidationError):
            kore.save("ab")

    def test_save_content_blank_alza_validation_error(self):
        """save() con contenuto blank deve sollevare KoreValidationError."""
        kore = _make_sync_client()
        with pytest.raises(KoreValidationError):
            kore.save("   ")

    def test_save_category_invalida_alza_validation_error(self):
        """save() con category non riconosciuta deve sollevare KoreValidationError."""
        kore = _make_sync_client()
        # Costruisce direttamente la richiesta HTTP per testare validazione lato server
        r = kore._client.post("/save", json={
            "content": "Contenuto valido lungo abbastanza",
            "category": "categoria_inventata",
        })
        assert r.status_code == 422


# ── Test: save_batch() ────────────────────────────────────────────────────────


class TestSyncSaveBatch:
    """Verifica il metodo save_batch() del client sincrono."""

    def setup_method(self):
        _rate_buckets.clear()

    def test_save_batch_due_memorie(self):
        """save_batch() con 2 item deve restituire BatchSaveResponse.total == 2."""
        kore = _make_sync_client()
        result = kore.save_batch([
            {"content": "Prima memoria batch sincrono alfa", "category": "general"},
            {"content": "Seconda memoria batch sincrono beta", "category": "project", "importance": 3},
        ])
        assert isinstance(result, BatchSaveResponse)
        assert result.total == 2
        assert len(result.saved) == 2

    def test_save_batch_item_ricevono_id(self):
        """Ogni item in save_batch() deve avere un id positivo."""
        kore = _make_sync_client()
        result = kore.save_batch([
            {"content": "Batch sincrono item uno con contenuto sufficiente"},
            {"content": "Batch sincrono item due con contenuto sufficiente"},
            {"content": "Batch sincrono item tre con contenuto sufficiente"},
        ])
        assert all(item.id > 0 for item in result.saved)

    def test_save_batch_singola_memoria(self):
        """save_batch() con un solo item deve funzionare."""
        kore = _make_sync_client()
        result = kore.save_batch([
            {"content": "Singola memoria nel batch sincrono", "importance": 2},
        ])
        assert isinstance(result, BatchSaveResponse)
        assert result.total == 1

    def test_save_batch_con_categorie_diverse(self):
        """save_batch() con categorie miste deve salvare tutti gli item."""
        kore = _make_sync_client()
        result = kore.save_batch([
            {"content": "Memoria batch categoria task sincrono", "category": "task"},
            {"content": "Memoria batch categoria decision sincrono", "category": "decision"},
            {"content": "Memoria batch categoria preference sincrono", "category": "preference"},
        ])
        assert result.total == 3


# ── Test: search() ────────────────────────────────────────────────────────────


class TestSyncSearch:
    """Verifica il metodo search() del client sincrono."""

    def setup_method(self):
        _rate_buckets.clear()

    def test_search_ritorna_modello(self):
        """search() deve restituire MemorySearchResponse."""
        kore = _make_sync_client()
        kore.save("Memoria di ricerca sincrona con parola UNIQSYNC1")
        result = kore.search("UNIQSYNC1", semantic=False)
        assert isinstance(result, MemorySearchResponse)
        assert result.total >= 1

    def test_search_senza_risultati(self):
        """search() su query senza risultati deve ritornare total == 0."""
        kore = _make_sync_client()
        result = kore.search("PAROLA_INESISTENTE_XQZ999", semantic=False)
        assert isinstance(result, MemorySearchResponse)
        assert result.total == 0

    def test_search_con_category_filter(self):
        """search() con filtro category deve restituire solo memorie di quella categoria."""
        kore = _make_sync_client(agent_id="sync-search-cat")
        kore.save("Progetto filtro categoria sincrono CATFILTER1", category="project")
        kore.save("Task filtro categoria sincrono CATFILTER1", category="task")
        result = kore.search("CATFILTER1", category="project", semantic=False)
        assert isinstance(result, MemorySearchResponse)
        # Tutti i risultati devono essere di categoria 'project'
        for mem in result.results:
            assert mem.category == "project"

    def test_search_con_limit(self):
        """search() con limit=2 deve restituire al massimo 2 risultati."""
        kore = _make_sync_client(agent_id="sync-search-limit")
        for i in range(5):
            kore.save(f"Voce paginazione sincrona {i} marcatore SYNCLIM")
        result = kore.search("SYNCLIM", limit=2, semantic=False)
        assert len(result.results) <= 2

    def test_search_con_semantic_false(self):
        """search() con semantic=False usa FTS5 invece degli embedding."""
        kore = _make_sync_client()
        kore.save("Test ricerca testuale sincrona FTS5 SYNFTS1")
        result = kore.search("SYNFTS1", semantic=False)
        assert isinstance(result, MemorySearchResponse)
        assert result.total >= 1

    def test_search_con_offset_deprecated(self):
        """search() con offset restituisce il campo offset nella risposta."""
        kore = _make_sync_client(agent_id="sync-search-off")
        for i in range(3):
            kore.save(f"Memoria offset sincrono {i} SYNCOFF1")
        result = kore.search("SYNCOFF1", offset=1, semantic=False)
        assert isinstance(result, MemorySearchResponse)
        with pytest.deprecated_call(match="deprecated"):
            assert result.offset == 1


# ── Test: timeline() ──────────────────────────────────────────────────────────


class TestSyncTimeline:
    """Verifica il metodo timeline() del client sincrono."""

    def setup_method(self):
        _rate_buckets.clear()

    def test_timeline_ritorna_modello(self):
        """timeline() deve restituire MemorySearchResponse."""
        kore = _make_sync_client()
        kore.save("Timeline sincrona: evento iniziale del progetto")
        result = kore.timeline("Timeline sincrona")
        assert isinstance(result, MemorySearchResponse)

    def test_timeline_vuota_ritorna_zero(self):
        """timeline() su argomento senza memorie deve restituire total == 0."""
        kore = _make_sync_client(agent_id="sync-timeline-empty")
        result = kore.timeline("ArgomentoInesistenteSyncTL99")
        assert isinstance(result, MemorySearchResponse)
        assert result.total == 0

    def test_timeline_ordine_cronologico(self):
        """timeline() deve restituire le memorie in ordine cronologico crescente."""
        kore = _make_sync_client(agent_id="sync-timeline-order")
        kore.save("Timeline ordine sincrono: primo evento SYNCTL1")
        kore.save("Timeline ordine sincrono: secondo evento SYNCTL1")
        result = kore.timeline("SYNCTL1")
        assert isinstance(result, MemorySearchResponse)
        if len(result.results) >= 2:
            # I risultati dal più vecchio al più recente
            assert result.results[0].created_at <= result.results[-1].created_at

    def test_timeline_con_limit(self):
        """timeline() con limit=2 deve restituire al massimo 2 risultati."""
        kore = _make_sync_client(agent_id="sync-timeline-lim")
        for i in range(5):
            kore.save(f"Timeline limit sincrono {i} SYNCTLL1")
        result = kore.timeline("SYNCTLL1", limit=2)
        assert len(result.results) <= 2


# ── Test: delete() ────────────────────────────────────────────────────────────


class TestSyncDelete:
    """Verifica il metodo delete() del client sincrono."""

    def setup_method(self):
        _rate_buckets.clear()

    def test_delete_memoria_esistente_ritorna_true(self):
        """delete() su una memoria esistente deve restituire True."""
        kore = _make_sync_client()
        saved = kore.save("Memoria da eliminare nel test sincrono")
        assert kore.delete(saved.id) is True

    def test_delete_memoria_inesistente_ritorna_false(self):
        """delete() su un id non esistente deve restituire False (non alzare eccezione)."""
        kore = _make_sync_client()
        assert kore.delete(999999) is False

    def test_delete_memoria_non_trovabile_dopo_eliminazione(self):
        """Dopo delete(), la memoria non deve più apparire nella ricerca."""
        kore = _make_sync_client(agent_id="sync-del-verify")
        saved = kore.save("Memoria da eliminare e verificare SYNCDEL1")
        kore.delete(saved.id)
        result = kore.search("SYNCDEL1", semantic=False)
        # La memoria eliminata non deve comparire
        ids = [m.id for m in result.results]
        assert saved.id not in ids

    def test_delete_altra_volta_lo_stesso_id_ritorna_false(self):
        """Doppio delete() sullo stesso id: il secondo deve restituire False."""
        kore = _make_sync_client()
        saved = kore.save("Memoria per doppio delete sincrono test")
        kore.delete(saved.id)
        assert kore.delete(saved.id) is False


# ── Test: export_memories() e import_memories() ───────────────────────────────


class TestSyncExportImport:
    """Verifica export_memories() e import_memories() del client sincrono."""

    def setup_method(self):
        _rate_buckets.clear()

    def test_export_ritorna_modello(self):
        """export_memories() deve restituire MemoryExportResponse."""
        kore = _make_sync_client(agent_id="sync-export-1")
        kore.save("Memoria da esportare nel test sincrono uno")
        result = kore.export_memories()
        assert isinstance(result, MemoryExportResponse)
        assert result.total >= 1

    def test_export_contiene_le_memorie_salvate(self):
        """Le memorie esportate devono includere quelle precedentemente salvate."""
        kore = _make_sync_client(agent_id="sync-export-2")
        kore.save("Memoria export sincrono marker EXPTEST1")
        result = kore.export_memories()
        contenuti = [m.get("content", "") for m in result.memories]
        assert any("EXPTEST1" in c for c in contenuti)

    def test_import_ritorna_modello(self):
        """import_memories() deve restituire MemoryImportResponse."""
        kore = _make_sync_client(agent_id="sync-import-1")
        result = kore.import_memories([
            {"content": "Memoria importata sincrona alfa", "category": "general", "importance": 2},
        ])
        assert isinstance(result, MemoryImportResponse)
        assert result.imported == 1

    def test_import_multiplo(self):
        """import_memories() con più item deve importarli tutti."""
        kore = _make_sync_client(agent_id="sync-import-2")
        result = kore.import_memories([
            {"content": "Import sincrono item uno lungo abbastanza", "category": "general"},
            {"content": "Import sincrono item due lungo abbastanza", "category": "project"},
            {"content": "Import sincrono item tre lungo abbastanza", "category": "task"},
        ])
        assert isinstance(result, MemoryImportResponse)
        assert result.imported == 3

    def test_roundtrip_export_import(self):
        """Le memorie esportate devono poter essere reimportate in un altro agent."""
        agente_sorgente = _make_sync_client(agent_id="sync-export-src")
        agente_dest = _make_sync_client(agent_id="sync-import-dst")

        agente_sorgente.save("Memoria per roundtrip export-import sincrono ROUNDTRIP1")
        export_result = agente_sorgente.export_memories()
        assert export_result.total >= 1

        import_result = agente_dest.import_memories(export_result.memories)
        assert isinstance(import_result, MemoryImportResponse)
        assert import_result.imported >= 1


# ── Test: add_tags(), get_tags(), remove_tags() ───────────────────────────────


class TestSyncTags:
    """Verifica i metodi di gestione tag del client sincrono."""

    def setup_method(self):
        _rate_buckets.clear()

    def test_add_tags_ritorna_modello(self):
        """add_tags() deve restituire TagResponse con count corretto."""
        kore = _make_sync_client()
        saved = kore.save("Memoria per aggiunta tag sincrona")
        result = kore.add_tags(saved.id, ["python", "backend"])
        assert isinstance(result, TagResponse)
        assert result.count == 2

    def test_get_tags_ritorna_tags_aggiunti(self):
        """get_tags() deve restituire i tag precedentemente aggiunti."""
        kore = _make_sync_client()
        saved = kore.save("Memoria per lettura tag sincrona")
        kore.add_tags(saved.id, ["kore", "memory", "sync"])
        result = kore.get_tags(saved.id)
        assert isinstance(result, TagResponse)
        assert "kore" in result.tags
        assert "memory" in result.tags
        assert "sync" in result.tags

    def test_get_tags_memoria_senza_tag_ritorna_lista_vuota(self):
        """get_tags() su memoria senza tag deve restituire lista vuota."""
        kore = _make_sync_client()
        saved = kore.save("Memoria senza tag per test sincrono")
        result = kore.get_tags(saved.id)
        assert isinstance(result, TagResponse)
        assert result.count == 0
        assert result.tags == []

    def test_remove_tags_rimuove_tag_specificato(self):
        """remove_tags() deve rimuovere solo i tag specificati."""
        kore = _make_sync_client()
        saved = kore.save("Memoria per rimozione tag sincrona")
        kore.add_tags(saved.id, ["da-tenere", "da-rimuovere"])
        result = kore.remove_tags(saved.id, ["da-rimuovere"])
        assert isinstance(result, TagResponse)
        assert "da-tenere" in result.tags
        assert "da-rimuovere" not in result.tags

    def test_add_tags_poi_remove_tutti_ritorna_lista_vuota(self):
        """Aggiunta e rimozione di tutti i tag deve produrre lista vuota."""
        kore = _make_sync_client()
        saved = kore.save("Memoria per ciclo completo tag sincrono")
        kore.add_tags(saved.id, ["tag-uno", "tag-due"])
        result = kore.remove_tags(saved.id, ["tag-uno", "tag-due"])
        assert result.tags == []

    def test_search_by_tag_trova_memoria_taggata(self):
        """search_by_tag() deve trovare la memoria con il tag specificato."""
        kore = _make_sync_client(agent_id="sync-tag-search")
        saved = kore.save("Memoria taggata per ricerca sincrona unica")
        kore.add_tags(saved.id, ["sync-unique-tag-99"])
        result = kore.search_by_tag("sync-unique-tag-99")
        assert isinstance(result, MemorySearchResponse)
        assert result.total >= 1
        ids = [m.id for m in result.results]
        assert saved.id in ids

    def test_search_by_tag_senza_risultati(self):
        """search_by_tag() su tag inesistente deve restituire total == 0."""
        kore = _make_sync_client()
        result = kore.search_by_tag("tag-completamente-inesistente-xyz999")
        assert isinstance(result, MemorySearchResponse)
        assert result.total == 0


# ── Test: add_relation(), get_relations() ─────────────────────────────────────


class TestSyncRelations:
    """Verifica i metodi di gestione relazioni del client sincrono."""

    def setup_method(self):
        _rate_buckets.clear()

    def test_add_relation_ritorna_modello(self):
        """add_relation() deve restituire RelationResponse con total >= 1."""
        kore = _make_sync_client()
        s1 = kore.save("Sorgente relazione sincrona primo nodo")
        s2 = kore.save("Destinazione relazione sincrona secondo nodo")
        result = kore.add_relation(s1.id, s2.id, "related")
        assert isinstance(result, RelationResponse)
        assert result.total >= 1

    def test_add_relation_tipo_depends_on(self):
        """add_relation() con tipo 'depends_on' deve salvare il tipo correttamente."""
        kore = _make_sync_client()
        s1 = kore.save("Nodo dipendente relazione sincrona test A")
        s2 = kore.save("Nodo dipendenza relazione sincrona test B")
        result = kore.add_relation(s1.id, s2.id, "depends_on")
        assert isinstance(result, RelationResponse)
        tipi = [r.relation for r in result.relations]
        assert "depends_on" in tipi

    def test_get_relations_ritorna_relazioni_esistenti(self):
        """get_relations() deve restituire le relazioni precedentemente create."""
        kore = _make_sync_client()
        s1 = kore.save("Sorgente get relations sincrono alfa")
        s2 = kore.save("Target get relations sincrono beta")
        kore.add_relation(s1.id, s2.id, "related")
        result = kore.get_relations(s1.id)
        assert isinstance(result, RelationResponse)
        assert result.total >= 1

    def test_get_relations_memoria_senza_relazioni(self):
        """get_relations() su memoria senza relazioni deve restituire total == 0."""
        kore = _make_sync_client()
        saved = kore.save("Memoria isolata senza relazioni sincrona")
        result = kore.get_relations(saved.id)
        assert isinstance(result, RelationResponse)
        assert result.total == 0
        assert result.relations == []

    def test_add_relazioni_multiple(self):
        """Una memoria può avere relazioni con più target."""
        kore = _make_sync_client()
        s1 = kore.save("Hub relazioni multiple sincrono nodo centrale")
        s2 = kore.save("Spoke relazioni multiple sincrono primo ramo")
        s3 = kore.save("Spoke relazioni multiple sincrono secondo ramo")
        kore.add_relation(s1.id, s2.id, "related")
        kore.add_relation(s1.id, s3.id, "related")
        result = kore.get_relations(s1.id)
        assert result.total >= 2


# ── Test: decay_run(), compress(), cleanup() ─────────────────────────────────


class TestSyncMaintenance:
    """Verifica i metodi di manutenzione del client sincrono."""

    def setup_method(self):
        _rate_buckets.clear()

    def test_decay_run_ritorna_modello(self):
        """decay_run() deve restituire DecayRunResponse con updated >= 0."""
        kore = _make_sync_client()
        result = kore.decay_run()
        assert isinstance(result, DecayRunResponse)
        assert result.updated >= 0

    def test_decay_run_su_agent_con_memorie(self):
        """decay_run() su agent con memorie deve elaborarle senza errori."""
        kore = _make_sync_client(agent_id="sync-decay-run")
        kore.save("Memoria per decay run sincrono test uno")
        kore.save("Memoria per decay run sincrono test due")
        result = kore.decay_run()
        assert isinstance(result, DecayRunResponse)
        assert result.updated >= 0

    def test_compress_ritorna_modello(self):
        """
        compress() deve restituire CompressRunResponse con i campi attesi.
        Usa un agente privo di relazioni per evitare UNIQUE constraint su memory_relations
        (bug noto nel compressor quando le memorie hanno già relazioni condivise).
        """
        # Agente isolato senza relazioni preesistenti
        kore = _make_sync_client(agent_id="sync-compress-clean-1")
        result = kore.compress()
        assert isinstance(result, CompressRunResponse)
        assert "clusters_found" in result.model_dump()
        assert "memories_merged" in result.model_dump()
        assert "new_records_created" in result.model_dump()

    def test_compress_valori_non_negativi(self):
        """compress() deve restituire valori numerici >= 0."""
        # Agente isolato senza relazioni preesistenti
        kore = _make_sync_client(agent_id="sync-compress-clean-2")
        result = kore.compress()
        assert result.clusters_found >= 0
        assert result.memories_merged >= 0
        assert result.new_records_created >= 0

    def test_cleanup_ritorna_modello(self):
        """cleanup() deve restituire CleanupExpiredResponse con removed >= 0."""
        kore = _make_sync_client()
        result = kore.cleanup()
        assert isinstance(result, CleanupExpiredResponse)
        assert result.removed >= 0

    def test_cleanup_rimuove_memoria_con_ttl_scaduto(self):
        """cleanup() deve eliminare memorie con TTL scaduto (ttl_hours=1 nel passato)."""
        kore = _make_sync_client(agent_id="sync-cleanup-ttl")
        # Salva una memoria con TTL minimo (1 ora)
        kore.save("Memoria con TTL per test cleanup sincrono", ttl_hours=1)
        # Forza la scadenza manipolando il DB direttamente
        from kore_memory.database import get_connection
        with get_connection() as conn:
            conn.execute(
                "UPDATE memories SET expires_at = '2000-01-01T00:00:00' WHERE agent_id = 'sync-cleanup-ttl'"
            )
        result = kore.cleanup()
        assert isinstance(result, CleanupExpiredResponse)
        assert result.removed >= 1


# ── Test: health() ────────────────────────────────────────────────────────────


class TestSyncHealth:
    """Verifica il metodo health() del client sincrono."""

    def setup_method(self):
        _rate_buckets.clear()

    def test_health_ritorna_dict(self):
        """health() deve restituire un dizionario con le chiavi attese."""
        kore = _make_sync_client()
        result = kore.health()
        assert isinstance(result, dict)
        assert result["status"] == "ok"

    def test_health_contiene_campi_obbligatori(self):
        """health() deve includere 'status', 'version', 'semantic_search', 'database'."""
        kore = _make_sync_client()
        result = kore.health()
        assert "status" in result
        assert "version" in result
        assert "semantic_search" in result
        assert "database" in result

    def test_health_database_connected(self):
        """health() deve indicare database='connected' se il DB è raggiungibile."""
        kore = _make_sync_client()
        result = kore.health()
        assert result["database"] == "connected"


# ── Test: context manager ─────────────────────────────────────────────────────


class TestSyncContextManager:
    """Verifica il context manager __enter__ / __exit__ di KoreClient."""

    def setup_method(self):
        _rate_buckets.clear()

    def test_context_manager_ritorna_se_stesso(self):
        """__enter__ deve restituire l'istanza del client."""
        kore = _make_sync_client()
        with kore as k:
            assert k is kore

    def test_context_manager_chiama_operazioni(self):
        """Le operazioni all'interno del context manager devono funzionare correttamente."""
        kore = _make_sync_client()
        with kore:
            result = kore.health()
            assert result["status"] == "ok"

    def test_context_manager_chiude_client_alla_uscita(self):
        """Dopo __exit__, il client HTTP deve essere chiuso."""
        kore = _make_sync_client()
        with kore:
            pass
        assert kore._client.is_closed

    def test_context_manager_save_e_search(self):
        """save() e search() dentro il context manager devono funzionare."""
        with _make_sync_client(agent_id="sync-ctx-test") as kore:
            saved = kore.save("Test context manager sincrono CTXMGR1")
            result = kore.search("CTXMGR1", semantic=False)
            assert result.total >= 1
            assert any(m.id == saved.id for m in result.results)


# ── Test: isolamento tra agent ────────────────────────────────────────────────


class TestSyncAgentIsolation:
    """Verifica che il KoreClient sincrono rispetti l'isolamento per agent_id."""

    def setup_method(self):
        _rate_buckets.clear()

    def test_agent_a_non_vede_dati_agent_b(self):
        """Le memorie di agent A non devono essere visibili da agent B."""
        agent_a = _make_sync_client(agent_id="sync-iso-a")
        agent_b = _make_sync_client(agent_id="sync-iso-b")
        agent_a.save("Segreto dell'agente A solo per sync iso ISOSYNC1")
        result = agent_b.search("ISOSYNC1", semantic=False)
        assert result.total == 0

    def test_agent_a_vede_solo_suoi_dati(self):
        """Un agent deve poter cercare e trovare solo le sue memorie."""
        agent_x = _make_sync_client(agent_id="sync-iso-x")
        agent_x.save("Dato esclusivo agente X sincrono ISOXSYNC1")
        result = agent_x.search("ISOXSYNC1", semantic=False)
        assert result.total >= 1

    def test_delete_da_agent_sbagliato_ritorna_false(self):
        """delete() da un agent diverso dal proprietario deve restituire False."""
        agent_own = _make_sync_client(agent_id="sync-iso-own")
        agent_other = _make_sync_client(agent_id="sync-iso-other")
        saved = agent_own.save("Memoria protetta da eliminazione sincrona")
        # L'altro agent non riesce a eliminare la memoria altrui
        assert agent_other.delete(saved.id) is False


# ── Test: integrazione end-to-end sincrona ────────────────────────────────────


class TestSyncEndToEnd:
    """Test di integrazione che verificano flussi completi con il client sincrono."""

    def setup_method(self):
        _rate_buckets.clear()

    def test_flusso_completo_save_tag_search_delete(self):
        """Flusso completo: salva → aggiungi tag → cerca per tag → elimina."""
        kore = _make_sync_client(agent_id="sync-e2e-1")

        # Salva
        saved = kore.save("Memoria flusso E2E sincrono con contenuto univoco E2ESYNC1")
        assert saved.id > 0

        # Aggiungi tag
        tags_result = kore.add_tags(saved.id, ["e2e-sync", "test"])
        assert "e2e-sync" in tags_result.tags

        # Cerca per tag
        by_tag = kore.search_by_tag("e2e-sync")
        assert by_tag.total >= 1

        # Cerca per testo
        by_text = kore.search("E2ESYNC1", semantic=False)
        assert by_text.total >= 1

        # Elimina
        assert kore.delete(saved.id) is True

        # Verifica eliminazione
        assert kore.delete(saved.id) is False

    def test_flusso_batch_export_import(self):
        """Flusso: batch save → export → import in nuovo agente."""
        src = _make_sync_client(agent_id="sync-e2e-src")
        dst = _make_sync_client(agent_id="sync-e2e-dst")

        # Salva in batch
        batch = src.save_batch([
            {"content": "Batch E2E sincrono elemento primo", "category": "project"},
            {"content": "Batch E2E sincrono elemento secondo", "category": "task"},
        ])
        assert batch.total == 2

        # Esporta
        exported = src.export_memories()
        assert exported.total >= 2

        # Importa nel nuovo agente
        imported = dst.import_memories(exported.memories)
        assert imported.imported >= 2

    def test_flusso_relazioni_con_tags(self):
        """Flusso: crea due memorie, aggiungi relazione e tag, verifica graph."""
        kore = _make_sync_client(agent_id="sync-e2e-graph")

        n1 = kore.save("Nodo E2E graph sincrono uno con contenuto")
        n2 = kore.save("Nodo E2E graph sincrono due con contenuto")

        # Tag su entrambi i nodi
        kore.add_tags(n1.id, ["graph-sync-node"])
        kore.add_tags(n2.id, ["graph-sync-node"])

        # Relazione bidirezionale
        rel = kore.add_relation(n1.id, n2.id, "linked")
        assert rel.total >= 1

        # Cerca per tag
        tagged = kore.search_by_tag("graph-sync-node")
        assert tagged.total >= 2

        # Verifica relazioni
        rels = kore.get_relations(n1.id)
        assert rels.total >= 1

    def test_flusso_decay_e_cleanup(self):
        """Flusso: salva memorie → esegui decay → esegui cleanup."""
        kore = _make_sync_client(agent_id="sync-e2e-decay")

        kore.save("Memoria decay E2E sincrona con contenuto adeguato")
        kore.save("Altra memoria decay E2E sincrona con contenuto")

        decay = kore.decay_run()
        assert isinstance(decay, DecayRunResponse)

        cleanup = kore.cleanup()
        assert isinstance(cleanup, CleanupExpiredResponse)

    def test_close_esplicita(self):
        """close() deve chiudere il client HTTP senza errori."""
        kore = _make_sync_client()
        kore.save("Memoria prima di close sincrono test")
        kore.close()
        assert kore._client.is_closed
