"""
Kore — Repository: Graph operations.
Tags and relations between memories.
"""

from __future__ import annotations

from ..database import get_connection


def add_tags(memory_id: int, tags: list[str], agent_id: str = "default") -> int:
    """Add tags to a memory. Returns the number of tags added."""
    # Verify that the memory belongs to the agent
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM memories WHERE id = ? AND agent_id = ?",
            (memory_id, agent_id),
        ).fetchone()
        if not row:
            return 0
        added = 0
        for tag in tags:
            tag = tag.strip().lower()[:100]
            if not tag:
                continue
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                    (memory_id, tag),
                )
                added += 1
            except Exception:
                continue
    return added


def remove_tags(memory_id: int, tags: list[str], agent_id: str = "default") -> int:
    """Remove tags from a memory. Returns the number of tags removed."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM memories WHERE id = ? AND agent_id = ?",
            (memory_id, agent_id),
        ).fetchone()
        if not row:
            return 0
        removed = 0
        for tag in tags:
            tag = tag.strip().lower()
            cursor = conn.execute(
                "DELETE FROM memory_tags WHERE memory_id = ? AND tag = ?",
                (memory_id, tag),
            )
            removed += cursor.rowcount
    return removed


def get_tags(memory_id: int, agent_id: str = "default") -> list[str]:
    """
    Return the tags of a memory.
    Verifies that the memory belongs to the specified agent_id.
    """
    with get_connection() as conn:
        # JOIN with memories to verify ownership
        rows = conn.execute(
            """
            SELECT mt.tag
            FROM memory_tags mt
            JOIN memories m ON mt.memory_id = m.id
            WHERE mt.memory_id = ? AND m.agent_id = ?
            ORDER BY mt.tag
            """,
            (memory_id, agent_id),
        ).fetchall()
    return [r["tag"] for r in rows]


def add_relation(
    source_id: int,
    target_id: int,
    relation: str = "related",
    agent_id: str = "default",
    strength: float = 1.0,
    confidence: float = 1.0,
) -> bool:
    """
    Crea una relazione tipizzata tra due memorie con peso e confidence.
    Entrambe le memorie devono appartenere all'agente.
    Se la relazione esiste già, aggiorna strength e confidence.
    """
    strength = max(0.0, min(1.0, strength))
    confidence = max(0.0, min(1.0, confidence))
    rel_key = relation.strip().lower()[:100]

    with get_connection() as conn:
        # Verifica che entrambe le memorie appartengano all'agente
        count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE id IN (?, ?) AND agent_id = ?",
            (source_id, target_id, agent_id),
        ).fetchone()[0]
        if count < 2:
            return False
        try:
            conn.execute(
                """INSERT INTO memory_relations (source_id, target_id, relation, strength, confidence)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(source_id, target_id, relation)
                   DO UPDATE SET strength = excluded.strength,
                                 confidence = excluded.confidence,
                                 updated_at = datetime('now')""",
                (source_id, target_id, rel_key, strength, confidence),
            )
            return True
        except Exception:
            return False


def get_relations(memory_id: int, agent_id: str = "default") -> list[dict]:
    """Restituisce tutte le relazioni di una memoria (in entrambe le direzioni)."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT r.source_id, r.target_id, r.relation,
                   r.strength, r.confidence, r.created_at, r.updated_at,
                   m.content AS related_content
            FROM memory_relations r
            JOIN memories m ON m.id = CASE
                WHEN r.source_id = ? THEN r.target_id
                ELSE r.source_id
            END
            WHERE (r.source_id = ? OR r.target_id = ?) AND m.agent_id = ?
            ORDER BY r.strength DESC, r.created_at DESC
            """,
            (memory_id, memory_id, memory_id, agent_id),
        ).fetchall()
    return [dict(r) for r in rows]


def traverse_graph(
    start_id: int,
    agent_id: str = "default",
    depth: int = 3,
    relation_type: str | None = None,
) -> dict:
    """
    Multi-hop graph traversal using SQLite recursive CTE.
    Returns the start node and all reachable nodes within `depth` hops.
    """
    depth = min(depth, 10)  # cap to prevent excessive recursion

    relation_filter = ""

    with get_connection() as conn:
        # Verify start memory belongs to agent
        start = conn.execute(
            "SELECT id, content, category, importance, decay_score, created_at "
            "FROM memories WHERE id = ? AND agent_id = ? AND archived_at IS NULL",
            (start_id, agent_id),
        ).fetchone()
        if not start:
            return {"start": None, "nodes": [], "edges": [], "depth": depth}

        # Build CTE params: anchor(start_id), [relation_type], agent_id, depth, agent_id, start_id
        cte_params: list = [start_id]
        if relation_type:
            relation_filter = "AND r.relation = ?"
            cte_params.append(relation_type)
        cte_params.extend([agent_id, depth])
        # Outer query params
        outer_params = [agent_id, start_id]

        # Recursive CTE — traverse both directions
        rows = conn.execute(
            f"""
            WITH RECURSIVE graph_walk(node_id, hop) AS (
                -- Anchor: start node
                SELECT ? AS node_id, 0 AS hop
                UNION
                -- Recursive step: follow relations in both directions
                SELECT
                    CASE WHEN r.source_id = gw.node_id THEN r.target_id ELSE r.source_id END,
                    gw.hop + 1
                FROM graph_walk gw
                JOIN memory_relations r
                    ON (r.source_id = gw.node_id OR r.target_id = gw.node_id)
                    {relation_filter}
                JOIN memories m
                    ON m.id = CASE WHEN r.source_id = gw.node_id THEN r.target_id ELSE r.source_id END
                    AND m.agent_id = ?
                    AND m.archived_at IS NULL
                WHERE gw.hop < ?
            )
            SELECT DISTINCT gw.node_id, gw.hop,
                   m.content, m.category, m.importance, m.decay_score, m.created_at
            FROM graph_walk gw
            JOIN memories m ON m.id = gw.node_id AND m.agent_id = ?
            WHERE gw.node_id != ?
            ORDER BY gw.hop, m.importance DESC
            """,
            (*cte_params, *outer_params),
        ).fetchall()

        nodes = [
            {
                "id": r["node_id"],
                "content": r["content"],
                "category": r["category"],
                "importance": r["importance"],
                "decay_score": r["decay_score"],
                "created_at": r["created_at"],
                "hop": r["hop"],
            }
            for r in rows
        ]

        # Fetch edges between all discovered nodes
        node_ids = [start_id] + [n["id"] for n in nodes]
        if len(node_ids) > 1:
            placeholders = ",".join("?" * len(node_ids))
            edge_params: list = list(node_ids) + list(node_ids)
            if relation_type:
                edge_params.append(relation_type)
            edges_rows = conn.execute(
                f"""
                SELECT source_id, target_id, relation, strength, confidence, created_at
                FROM memory_relations
                WHERE source_id IN ({placeholders}) AND target_id IN ({placeholders})
                {"AND relation = ?" if relation_type else ""}
                ORDER BY strength DESC
                """,
                edge_params,
            ).fetchall()
            edges = [dict(e) for e in edges_rows]
        else:
            edges = []

    return {
        "start": dict(start),
        "nodes": nodes,
        "edges": edges,
        "depth": depth,
    }


def extract_subgraph(
    memory_ids: list[int],
    agent_id: str = "default",
    expand_depth: int = 0,
) -> dict:
    """
    Estrae il sottografo indotto dai nodi specificati.
    Con expand_depth > 0 espande anche i vicini entro `expand_depth` hop.
    Restituisce nodi e archi tra tutti i nodi nel sottoinsieme.
    """
    if not memory_ids:
        return {"nodes": [], "edges": [], "total_nodes": 0, "total_edges": 0}

    # Cap per sicurezza
    memory_ids = memory_ids[:200]
    expand_depth = min(expand_depth, 5)

    with get_connection() as conn:
        # Se expand_depth > 0, usa CTE ricorsiva per espandere il set
        if expand_depth > 0:
            placeholders_anchor = ",".join("?" * len(memory_ids))
            # Binding: ids (anchor IN), agent_id x2 (anchor + recursive JOIN),
            # expand_depth (hop limit), agent_id (outer JOIN)
            cte_params: list = list(memory_ids) + [agent_id, agent_id, expand_depth, agent_id]
            rows = conn.execute(
                f"""
                WITH RECURSIVE subgraph_walk(node_id, hop) AS (
                    SELECT id AS node_id, 0 AS hop
                    FROM memories
                    WHERE id IN ({placeholders_anchor}) AND agent_id = ? AND archived_at IS NULL
                    UNION
                    SELECT
                        CASE WHEN r.source_id = sw.node_id THEN r.target_id ELSE r.source_id END,
                        sw.hop + 1
                    FROM subgraph_walk sw
                    JOIN memory_relations r ON (r.source_id = sw.node_id OR r.target_id = sw.node_id)
                    JOIN memories m ON m.id = CASE
                        WHEN r.source_id = sw.node_id THEN r.target_id ELSE r.source_id END
                        AND m.agent_id = ? AND m.archived_at IS NULL
                    WHERE sw.hop < ?
                )
                SELECT DISTINCT sw.node_id,
                       m.content, m.category, m.importance, m.decay_score,
                       m.confidence, m.created_at, sw.hop
                FROM subgraph_walk sw
                JOIN memories m ON m.id = sw.node_id AND m.agent_id = ?
                ORDER BY sw.hop, m.importance DESC
                """,
                cte_params,
            ).fetchall()
        else:
            # Solo i nodi richiesti esplicitamente
            placeholders = ",".join("?" * len(memory_ids))
            rows = conn.execute(
                f"""
                SELECT id AS node_id, content, category, importance,
                       decay_score, confidence, created_at, 0 AS hop
                FROM memories
                WHERE id IN ({placeholders}) AND agent_id = ? AND archived_at IS NULL
                ORDER BY importance DESC
                """,
                (*memory_ids, agent_id),
            ).fetchall()

        nodes = [
            {
                "id": r["node_id"],
                "content": r["content"],
                "category": r["category"],
                "importance": r["importance"],
                "decay_score": r["decay_score"],
                "confidence": r["confidence"],
                "created_at": r["created_at"],
                "hop": r["hop"],
            }
            for r in rows
        ]

        # Recupera tutti gli archi tra i nodi trovati
        node_ids = [n["id"] for n in nodes]
        edges: list[dict] = []
        if len(node_ids) > 1:
            placeholders_e = ",".join("?" * len(node_ids))
            edge_rows = conn.execute(
                f"""
                SELECT source_id, target_id, relation, strength, confidence, created_at
                FROM memory_relations
                WHERE source_id IN ({placeholders_e}) AND target_id IN ({placeholders_e})
                ORDER BY strength DESC
                """,
                (*node_ids, *node_ids),
            ).fetchall()
            edges = [dict(e) for e in edge_rows]

    return {
        "nodes": nodes,
        "edges": edges,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
    }


def get_degree_centrality(
    agent_id: str = "default",
    limit: int = 20,
    min_degree: int = 1,
) -> list[dict]:
    """
    Calcola il grado di centralità per ogni nodo del grafo dell'agente.
    degree = in_degree + out_degree (non normalizzato, più semplice e utile)
    Restituisce i nodi ordinati per degree DESC.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                m.id,
                m.content,
                m.category,
                m.importance,
                m.decay_score,
                m.created_at,
                COALESCE(out_deg.cnt, 0) AS out_degree,
                COALESCE(in_deg.cnt, 0)  AS in_degree,
                COALESCE(out_deg.cnt, 0) + COALESCE(in_deg.cnt, 0) AS degree,
                COALESCE(AVG(r_all.strength), 0.0) AS avg_strength
            FROM memories m
            LEFT JOIN (
                SELECT source_id AS id, COUNT(*) AS cnt
                FROM memory_relations
                GROUP BY source_id
            ) out_deg ON out_deg.id = m.id
            LEFT JOIN (
                SELECT target_id AS id, COUNT(*) AS cnt
                FROM memory_relations
                GROUP BY target_id
            ) in_deg ON in_deg.id = m.id
            LEFT JOIN memory_relations r_all
                ON (r_all.source_id = m.id OR r_all.target_id = m.id)
            WHERE m.agent_id = ? AND m.archived_at IS NULL
                  AND m.compressed_into IS NULL
            GROUP BY m.id
            HAVING degree >= ?
            ORDER BY degree DESC, m.importance DESC
            LIMIT ?
            """,
            (agent_id, min_degree, limit),
        ).fetchall()

    total_nodes = _count_active_nodes(conn if False else None, agent_id)

    return [
        {
            "id": r["id"],
            "content": r["content"],
            "category": r["category"],
            "importance": r["importance"],
            "decay_score": r["decay_score"],
            "created_at": r["created_at"],
            "in_degree": r["in_degree"],
            "out_degree": r["out_degree"],
            "degree": r["degree"],
            "avg_strength": round(r["avg_strength"], 4),
            "degree_centrality": _normalized_centrality(r["degree"], total_nodes),
        }
        for r in rows
    ]


def _count_active_nodes(conn, agent_id: str) -> int:
    """Conta i nodi attivi (non archiviati, non compressi) per un agente."""
    with get_connection() as c:
        row = c.execute(
            "SELECT COUNT(*) FROM memories WHERE agent_id = ? AND archived_at IS NULL AND compressed_into IS NULL",
            (agent_id,),
        ).fetchone()
    return row[0] if row else 1


def _normalized_centrality(degree: int, total_nodes: int) -> float:
    """Centralità normalizzata: degree / (N-1). Se N<=1 ritorna 0."""
    if total_nodes <= 1:
        return 0.0
    return round(degree / (total_nodes - 1), 4)
