"""
Kore — Pydantic models
Request/response schemas with validation.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal[
    # Categorie generali
    "general",
    "project",
    "trading",
    "finance",
    "person",
    "preference",
    "task",
    "decision",
    # Categorie coding memory mode (v2.1)
    "architectural_decision",
    "root_cause",
    "runbook",
    "regression_note",
    "tech_debt",
    "api_contract",
]

MemoryType = Literal["episodic", "semantic", "procedural", "meta"]

# Mapping canonico category → memory_type (usato per inferenza automatica)
_CATEGORY_TYPE_MAP: dict[str, MemoryType] = {
    "architectural_decision": "semantic",
    "root_cause": "episodic",
    "runbook": "procedural",
    "regression_note": "episodic",
    "tech_debt": "semantic",
    "api_contract": "semantic",
    "decision": "semantic",
    "task": "episodic",
    "general": "semantic",
    "project": "semantic",
    "preference": "semantic",
    "person": "semantic",
    "trading": "semantic",
    "finance": "semantic",
}


def infer_memory_type(category: str) -> MemoryType:
    """Inferisce memory_type dalla category se non fornito esplicitamente."""
    return _CATEGORY_TYPE_MAP.get(category, "semantic")


class ProvenanceSchema(BaseModel):
    """Provenienza di una memoria: chi l'ha creata, come, da dove."""

    source_type: Literal["agent", "file", "import", "api"] = "agent"
    source_ref: str | None = None
    author_agent: str | None = None
    session_id: str | None = None
    created_via: str | None = None
    external_id: str | None = None


class MemorySaveRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "content": "Il progetto usa FastAPI con SQLite per la persistenza",
                    "category": "project",
                    "importance": 4,
                },
                {
                    "content": "Riunione con il team alle 15:00",
                    "category": "task",
                },
            ]
        }
    }

    content: str = Field(..., min_length=3, max_length=4000)
    category: Category = Field("general")
    importance: int | None = Field(None, ge=1, le=5, description="None=auto-scored, 1-5=explicit")
    ttl_hours: int | None = Field(None, ge=1, le=8760, description="Time-to-live in ore (max 1 anno)")
    # Campi temporali v2.1
    valid_from: datetime | None = Field(None, description="Inizio validità della memoria")
    valid_to: datetime | None = Field(None, description="Fine validità: None = nessuna scadenza")
    supersedes_id: int | None = Field(None, description="ID della memoria sostituita da questa")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Confidenza nella correttezza (0.0-1.0)")
    provenance: ProvenanceSchema | None = Field(None, description="Provenienza della memoria")
    memory_type: MemoryType | None = Field(None, description="Tipo cognitivo: None = inferito dalla category")
    metadata: dict | None = Field(None, description="Campi strutturati specifici per category (coding mode)")
    title: str | None = Field(None, max_length=120, description="Titolo esplicito (auto-generato se assente)")
    facts: list[str] | None = Field(None, max_length=20, description="Fatti espliciti (override auto-extraction)")
    concepts: list[str] | None = Field(None, max_length=15, description="Concetti espliciti (override auto-extraction)")
    narrative: str | None = Field(None, max_length=500, description="Riassunto esplicito (override auto-extraction)")

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Content cannot be blank")
        return v.strip()


class MemoryUpdateRequest(BaseModel):
    content: str | None = Field(None, min_length=3, max_length=4000)
    category: Category | None = None
    importance: int | None = Field(None, ge=1, le=5)
    # Campi temporali v2.1
    valid_to: datetime | None = Field(None, description="Aggiorna la scadenza della memoria")
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    provenance: ProvenanceSchema | None = None

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Content cannot be blank")
        return v.strip() if v else v


class MemoryRecord(BaseModel):
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "id": 42,
                    "content": "Il progetto usa FastAPI con SQLite",
                    "category": "project",
                    "importance": 4,
                    "decay_score": 0.95,
                    "memory_type": "semantic",
                    "confidence": 1.0,
                    "status": "active",
                    "conditions": [],
                    "created_at": "2026-01-15T10:30:00",
                    "updated_at": "2026-01-15T10:30:00",
                    "score": None,
                    "explain": None,
                }
            ]
        }
    }

    id: int
    content: str
    category: str
    importance: int
    decay_score: float = 1.0
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    # score è un campo runtime — calcolato durante il retrieval, mai persistito in DB
    score: float | None = None
    # Campi temporali v2.1
    memory_type: str = "semantic"
    confidence: float = 1.0
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    supersedes_id: int | None = None
    provenance: dict | None = None
    # M2: structured fields
    facts: list[str] | None = None
    concepts: list[str] | None = None
    narrative: str | None = None
    metadata: dict | None = None
    # Stato derivato (non persistito) — calcolato da _compute_memory_status()
    status: str = "active"
    conditions: list[str] = Field(default_factory=list)
    # Explain breakdown — calcolato solo con explain=true, mai persistito (Wave 2, issue #015)
    explain: dict | None = None


class MemorySaveResponse(BaseModel):
    id: int
    importance: int
    message: str = "Memory saved"
    title: str | None = None
    # Lista di conflict ID rilevati al save (lista vuota = nessun conflitto)
    conflicts_detected: list[str] = Field(default_factory=list)
    # ID della memoria superseded (se supersedes_id era presente nella request)
    superseded_id: int | None = None


class MemorySearchResponse(BaseModel):
    results: list[MemoryRecord]
    total: int
    cursor: str | None = Field(None, description="Opaque cursor for next page (base64)")
    has_more: bool = False
    # Profilo di ranking usato per ordinare i risultati
    ranking_profile: str = "default_v1"
    # Memorie escluse dal retrieval (popolate solo con explain=true, issue #015)
    excluded: list[dict] = Field(default_factory=list)
    # Deprecated fields kept for backwards compatibility
    offset: int = Field(0, deprecated=True, description="Deprecated: use cursor instead")


class MemoryImportRequest(BaseModel):
    memories: list[dict] = Field(..., min_length=1, max_length=500)


class MemoryImportResponse(BaseModel):
    imported: int
    message: str = "Import complete"


class MemoryExportResponse(BaseModel):
    memories: list[dict]
    total: int


class BatchSaveRequest(BaseModel):
    memories: list[MemorySaveRequest] = Field(..., min_length=1, max_length=100)


class BatchSaveResponse(BaseModel):
    saved: list[MemorySaveResponse]
    total: int


class TagRequest(BaseModel):
    tags: list[str] = Field(..., min_length=1, max_length=20)


class TagResponse(BaseModel):
    count: int
    tags: list[str] = []


class RelationRequest(BaseModel):
    target_id: int
    relation: str = Field("related", max_length=100)
    strength: float = Field(1.0, ge=0.0, le=1.0, description="Peso della relazione (0.0-1.0)")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Confidence sulla relazione (0.0-1.0)")


class RelationRecord(BaseModel):
    source_id: int
    target_id: int
    relation: str
    strength: float = 1.0
    confidence: float = 1.0
    created_at: str
    updated_at: str | None = None
    related_content: str | None = None


class RelationResponse(BaseModel):
    relations: list[RelationRecord]
    total: int


class DecayRunResponse(BaseModel):
    updated: int
    message: str = "Decay pass complete"


class CleanupExpiredResponse(BaseModel):
    removed: int
    message: str = "Expired memories cleaned up"


class CompressRunResponse(BaseModel):
    clusters_found: int
    memories_merged: int
    new_records_created: int
    message: str = "Compression complete"


class ArchiveResponse(BaseModel):
    success: bool
    message: str = ""


class AutoTuneResponse(BaseModel):
    boosted: int
    reduced: int
    message: str = "Auto-tune complete"


class ScoringStatsResponse(BaseModel):
    total: int
    distribution: dict[str, int]  # importance level -> count
    avg_importance: float
    avg_access_count: float
    never_accessed_30d: int
    frequently_accessed: int


class SessionCreateRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    title: str | None = Field(None, max_length=500)


class SessionResponse(BaseModel):
    id: str
    agent_id: str
    title: str | None = None
    created_at: datetime
    ended_at: datetime | None = None
    memory_count: int = 0


class SessionSummaryResponse(BaseModel):
    session_id: str
    agent_id: str
    title: str | None = None
    created_at: str
    ended_at: str | None = None
    memory_count: int = 0
    categories: list[str] = []
    avg_importance: float = 0.0
    first_memory: str | None = None
    last_memory: str | None = None


class SessionDeleteResponse(BaseModel):
    success: bool
    unlinked_memories: int


class EntityRecord(BaseModel):
    type: str
    value: str
    memory_id: int
    tag: str


class EntityListResponse(BaseModel):
    entities: list[EntityRecord]
    total: int


class AgentRecord(BaseModel):
    agent_id: str
    memory_count: int
    last_active: str | None = None


class AgentListResponse(BaseModel):
    agents: list[AgentRecord]
    total: int


class AuditEventRecord(BaseModel):
    id: int
    event: str
    agent_id: str
    memory_id: int | None = None
    data: dict | str | None = None
    created_at: str


class AuditResponse(BaseModel):
    events: list[AuditEventRecord]
    total: int


# ── Graph RAG ────────────────────────────────────────────────────────────────


class GraphNodeRecord(BaseModel):
    id: int
    content: str
    category: str
    importance: int
    decay_score: float
    created_at: str
    hop: int


class GraphEdgeRecord(BaseModel):
    source_id: int
    target_id: int
    relation: str
    strength: float = 1.0
    confidence: float = 1.0
    created_at: str


class GraphTraverseResponse(BaseModel):
    start: dict | None = None
    nodes: list[GraphNodeRecord] = []
    edges: list[GraphEdgeRecord] = []
    depth: int


class SubgraphResponse(BaseModel):
    nodes: list[GraphNodeRecord] = []
    edges: list[GraphEdgeRecord] = []
    total_nodes: int
    total_edges: int


class HubNodeRecord(BaseModel):
    id: int
    content: str
    category: str
    importance: int
    decay_score: float
    created_at: str
    in_degree: int
    out_degree: int
    degree: int
    avg_strength: float
    degree_centrality: float


class HubDetectionResponse(BaseModel):
    hubs: list[HubNodeRecord] = []
    total: int


# ── Filesystem Overlay (issue #024) ──────────────────────────────────────────


class OverlayIndexRequest(BaseModel):
    base_path: str = Field(..., min_length=1, max_length=1024, description="Directory radice da scansionare")
    patterns: list[str] = Field(
        default_factory=list,
        description="Pattern filename da includere (vuoto = DEFAULT_PATTERNS)",
        max_length=50,
    )
    include_extra_md: bool = Field(True, description="Includi file .md extra in docs/")
    replace_existing: bool = Field(True, description="Sostituisce memories esistenti per lo stesso file")
    max_depth: int = Field(2, ge=1, le=5, description="Profondità massima di scansione")


class OverlayFileRecord(BaseModel):
    path: str
    filename: str
    exists: bool
    memory_ids: list[int]
    chunk_count: int
    category: str
    last_indexed: str


class OverlayIndexResponse(BaseModel):
    indexed: int
    updated: int
    skipped: int
    errors: int
    file_results: list[dict] = []
    files_scanned: int


class OverlayFilesResponse(BaseModel):
    files: list[OverlayFileRecord]
    total: int


# ── Filesystem Watcher (issue #025) ──────────────────────────────────────────


class OverlayWatchRequest(BaseModel):
    base_path: str = Field(..., min_length=1, max_length=1024, description="Directory da monitorare")
    patterns: list[str] = Field(
        default_factory=list,
        description="Pattern filename da monitorare (vuoto = DEFAULT_PATTERNS)",
        max_length=50,
    )
    include_extra_md: bool = Field(True, description="Monitora anche file .md extra")
    max_depth: int = Field(2, ge=1, le=5, description="Profondità massima di scansione al re-index")


class OverlayWatcherRecord(BaseModel):
    base_path: str
    agent_id: str
    started_at: str
    events_processed: int
    active: bool


class OverlayWatchResponse(BaseModel):
    watching: bool
    already_running: bool = False
    base_path: str
    agent_id: str
    started_at: str = ""
    message: str


class OverlayWatchersResponse(BaseModel):
    watchers: list[OverlayWatcherRecord]
    total: int
    watcher_available: bool


# ── Summarization ────────────────────────────────────────────────────────────


class KeywordRecord(BaseModel):
    word: str
    score: float


class SummarizeResponse(BaseModel):
    topic: str
    memory_count: int
    keywords: list[KeywordRecord] = []
    categories: dict[str, int] = {}
    avg_importance: float = 0.0
    time_span: dict[str, str] | None = None


# ── ACL ──────────────────────────────────────────────────────────────────────


class ACLGrantRequest(BaseModel):
    target_agent: str = Field(..., min_length=1, max_length=64)
    permission: str = Field("read", pattern=r"^(read|write|admin)$")


class ACLRecord(BaseModel):
    agent_id: str
    permission: str
    granted_by: str
    created_at: str


class ACLResponse(BaseModel):
    success: bool
    permissions: list[ACLRecord] = []


class SharedMemoryRecord(BaseModel):
    id: int
    content: str
    category: str
    importance: int
    decay_score: float
    created_at: str
    updated_at: str
    owner_agent: str
    permission: str


class SharedMemoriesResponse(BaseModel):
    memories: list[SharedMemoryRecord]
    total: int


# ── Analytics ────────────────────────────────────────────────────────────────


class AnalyticsResponse(BaseModel):
    total_memories: int
    categories: dict[str, int]
    importance_distribution: dict[str, int]
    decay_analysis: dict[str, float | int]
    top_tags: list[dict]
    access_patterns: dict[str, float | int]
    growth_last_30d: list[dict]
    compressed_memories: int
    archived_memories: int
    total_relations: int


# ── GDPR ─────────────────────────────────────────────────────────────────────


class GDPRDeleteResponse(BaseModel):
    deleted_memories: int
    deleted_tags: int
    deleted_relations: int
    deleted_sessions: int
    deleted_events: int
    message: str = "All agent data permanently deleted"


# ── Plugins ──────────────────────────────────────────────────────────────────


class PluginListResponse(BaseModel):
    plugins: list[str]
    total: int


# ── Explain (issue #016) ──────────────────────────────────────────────────────


class ConflictInfo(BaseModel):
    conflict_id: str
    conflict_type: str
    resolved: bool
    detected_at: str
    resolved_at: str | None = None


class MemoryExplainResponse(BaseModel):
    """Analisi completa di una singola memoria — GET /explain/memory/{id}."""

    id: int
    status: str
    conditions: list[str]
    current_score: float | None = None
    score_breakdown: dict = Field(default_factory=dict)
    access_count: int = 0
    last_accessed: str | None = None
    conflicts: list[ConflictInfo] = Field(default_factory=list)
    supersession_chain: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    provenance: dict | None = None
    memory_type: str = "semantic"
    confidence: float = 1.0
    valid_from: datetime | None = None
    valid_to: datetime | None = None


# ── Context Assembly (issue #017) ─────────────────────────────────────────────


class ContextAssembleRequest(BaseModel):
    """Request per POST /context/assemble."""

    task: str = Field(..., min_length=1, max_length=2000, description="Descrizione del task corrente")
    budget_tokens: int = Field(2000, ge=1, le=32000, description="Budget massimo in token")
    categories: list[str] = Field(
        default_factory=list,
        description="Categorie da includere (vuoto = tutte)",
        max_length=20,
    )
    ranking_profile: str = Field("default", description="Profilo di ranking: default | coding")
    include_low_confidence: bool = Field(False, description="Includi memorie con confidence < 0.5")
    explain: bool = Field(False, description="Includi score breakdown per ogni memoria")


class ContextMemoryItem(BaseModel):
    """Singola memoria nel context package."""

    id: int
    content: str
    category: str
    importance: int
    decay_score: float
    confidence: float
    score: float
    tokens_estimated: int
    status: str = "active"
    conditions: list[str] = Field(default_factory=list)
    explain: dict | None = None


class ContextAssembleResponse(BaseModel):
    """Response di POST /context/assemble — context package strutturato."""

    task: str
    budget_tokens_requested: int
    budget_tokens_used: int
    total_memories: int
    ranking_profile: str
    degraded: bool = Field(False, description="True se embedder non disponibile → fallback FTS5")
    memories: list[ContextMemoryItem] = Field(default_factory=list)
    conflicts: list[dict] = Field(
        default_factory=list,
        description="Conflitti critici irrisolti tra le memorie selezionate",
    )
