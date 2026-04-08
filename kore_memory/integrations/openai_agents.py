"""
Kore Memory — OpenAI Agents SDK integration.
Provides ready-to-use function tools for OpenAI Agents SDK agents.

Usage:
    from kore_memory.integrations.openai_agents import kore_agent_tools

    tools = kore_agent_tools(base_url="http://localhost:8765", agent_id="my-agent")
    agent = Agent(name="Assistant", tools=tools)

Requires: pip install 'kore-memory[openai-agents]' (or openai-agents separately)
"""

from __future__ import annotations

from typing import Any

try:
    from agents import function_tool

    _HAS_OPENAI_AGENTS = True
except ImportError:
    _HAS_OPENAI_AGENTS = False

from kore_memory.client import KoreClient


def kore_agent_tools(
    base_url: str = "http://localhost:8765",
    api_key: str | None = None,
    agent_id: str = "default",
    timeout: float = 10.0,
) -> list[Any]:
    """
    Create a list of function tools for OpenAI Agents SDK.

    Args:
        base_url: Kore server URL.
        api_key: API key for authentication (optional on localhost).
        agent_id: Agent namespace for memory isolation.
        timeout: Request timeout in seconds.

    Returns:
        List of FunctionTool ready to be passed to Agent(tools=[...]).
    """
    if not _HAS_OPENAI_AGENTS:
        raise ImportError("OpenAI Agents SDK not installed. Install with: pip install openai-agents")

    client = KoreClient(
        base_url=base_url,
        api_key=api_key,
        agent_id=agent_id,
        timeout=timeout,
    )

    @function_tool
    def kore_save(content: str, category: str = "general", importance: int = 0) -> str:
        """Save a memory to Kore persistent storage.

        Args:
            content: The text content to memorize.
            category: Category (general, project, task, decision, person, preference).
            importance: Importance 1-5, 0 for auto-scoring.
        """
        imp = importance if importance > 0 else None
        result = client.save(content=content, category=category, importance=imp)
        return f"Memory saved with id={result.id}, importance={result.importance}"

    @function_tool
    def kore_search(query: str, limit: int = 5, category: str = "") -> str:
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

    @function_tool
    def kore_timeline(subject: str, limit: int = 10) -> str:
        """Show the chronological timeline of memories on a specific subject.

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

    @function_tool
    def kore_delete(memory_id: int) -> str:
        """Delete a memory from persistent storage.

        Args:
            memory_id: The ID of the memory to delete.
        """
        deleted = client.delete(memory_id)
        if deleted:
            return f"Memory {memory_id} deleted."
        return f"Memory {memory_id} not found."

    return [kore_save, kore_search, kore_timeline, kore_delete]
