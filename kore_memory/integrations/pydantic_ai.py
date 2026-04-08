"""
Kore Memory — PydanticAI integration.
Provides ready-to-use tools for PydanticAI agents.

Usage:
    from kore_memory.integrations.pydantic_ai import kore_toolset

    agent = Agent('openai:gpt-4o', toolsets=[kore_toolset(base_url="http://localhost:8765")])
    result = agent.run_sync("Save that the project uses FastAPI")

Requires: pip install 'kore-memory[pydantic-ai]' (or pydantic-ai separately)
"""

from __future__ import annotations

from typing import Any

try:
    from pydantic_ai import FunctionToolset

    _HAS_PYDANTIC_AI = True
except ImportError:
    _HAS_PYDANTIC_AI = False

from kore_memory.client import KoreClient


def kore_toolset(
    base_url: str = "http://localhost:8765",
    api_key: str | None = None,
    agent_id: str = "default",
    timeout: float = 10.0,
) -> Any:
    """
    Create a PydanticAI FunctionToolset with Kore Memory tools.

    Args:
        base_url: Kore server URL.
        api_key: API key for authentication (optional on localhost).
        agent_id: Agent namespace for memory isolation.
        timeout: Request timeout in seconds.

    Returns:
        FunctionToolset with tools: kore_save, kore_search, kore_timeline, kore_delete.
    """
    if not _HAS_PYDANTIC_AI:
        raise ImportError("PydanticAI not installed. Install with: pip install pydantic-ai")

    client = KoreClient(
        base_url=base_url,
        api_key=api_key,
        agent_id=agent_id,
        timeout=timeout,
    )

    toolset = FunctionToolset()

    @toolset.tool
    def kore_save(
        content: str,
        category: str = "general",
        importance: int = 0,
    ) -> str:
        """Save a memory to Kore persistent storage.

        Args:
            content: The text content to memorize.
            category: Category (general, project, task, decision, person, preference, trading, finance).
            importance: Importance 1-5, 0 for auto-scoring.
        """
        imp = importance if importance > 0 else None
        result = client.save(content=content, category=category, importance=imp)
        return f"Memory saved with id={result.id}, importance={result.importance}"

    @toolset.tool
    def kore_search(
        query: str,
        limit: int = 5,
        category: str = "",
    ) -> str:
        """Search Kore persistent memory using semantic search.

        Args:
            query: The search query (any language).
            limit: Maximum number of results (1-20).
            category: Filter by category (optional, empty string = all).
        """
        cat = category if category else None
        response = client.search(q=query, limit=limit, category=cat, semantic=True)
        if not response.results:
            return "No memories found."
        lines = []
        for r in response.results:
            lines.append(f"[id={r.id}] ({r.category}, imp={r.importance}) {r.content}")
        return "\n".join(lines)

    @toolset.tool
    def kore_timeline(
        subject: str,
        limit: int = 10,
    ) -> str:
        """Show the chronological timeline of memories on a subject.

        Args:
            subject: The subject to search in the timeline.
            limit: Maximum number of results (1-50).
        """
        response = client.timeline(subject=subject, limit=limit)
        if not response.results:
            return "No memories found for this subject."
        lines = []
        for r in response.results:
            lines.append(f"[{r.created_at}] {r.content}")
        return "\n".join(lines)

    @toolset.tool
    def kore_delete(memory_id: int) -> str:
        """Delete a memory from persistent storage.

        Args:
            memory_id: The ID of the memory to delete.
        """
        deleted = client.delete(memory_id)
        if deleted:
            return f"Memory {memory_id} deleted."
        return f"Memory {memory_id} not found."

    return toolset


# ── Standalone tools (for agents using @agent.tool_plain) ────────────────────


def create_kore_tools(
    base_url: str = "http://localhost:8765",
    api_key: str | None = None,
    agent_id: str = "default",
) -> dict[str, Any]:
    """
    Create a dictionary of standalone tool functions for use with @agent.tool_plain.

    Returns:
        Dict with keys: save, search, timeline, delete — functions ready for PydanticAI.
    """
    client = KoreClient(base_url=base_url, api_key=api_key, agent_id=agent_id)

    def save(content: str, category: str = "general", importance: int = 0) -> str:
        """Save a memory."""
        imp = importance if importance > 0 else None
        result = client.save(content=content, category=category, importance=imp)
        return f"Saved id={result.id}, importance={result.importance}"

    def search(query: str, limit: int = 5) -> list[dict]:
        """Search memories."""
        response = client.search(q=query, limit=limit, semantic=True)
        return [
            {"id": r.id, "content": r.content, "category": r.category, "importance": r.importance}
            for r in response.results
        ]

    def timeline(subject: str, limit: int = 10) -> list[dict]:
        """Timeline of a subject."""
        response = client.timeline(subject=subject, limit=limit)
        return [{"id": r.id, "content": r.content, "created_at": str(r.created_at)} for r in response.results]

    def delete(memory_id: int) -> bool:
        """Delete a memory."""
        return client.delete(memory_id)

    return {"save": save, "search": search, "timeline": timeline, "delete": delete}
