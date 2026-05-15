"""
Test per il lazy-loading delle integrazioni in kore_memory/integrations/__init__.py.
"""

import pytest


def test_lazy_load_kore_crewai_memory():
    """KoreCrewAIMemory viene caricato on-demand."""
    from kore_memory.integrations import KoreCrewAIMemory
    assert KoreCrewAIMemory is not None


def test_lazy_load_kore_langchain_memory():
    """KoreLangChainMemory viene caricato on-demand."""
    from kore_memory.integrations import KoreLangChainMemory
    assert KoreLangChainMemory is not None


def test_lazy_load_kore_chat_message_history():
    """KoreChatMessageHistory viene caricato on-demand."""
    from kore_memory.integrations import KoreChatMessageHistory
    assert KoreChatMessageHistory is not None


def test_lazy_load_kore_toolset():
    """kore_toolset viene caricato on-demand."""
    from kore_memory.integrations import kore_toolset
    assert kore_toolset is not None


def test_lazy_load_create_kore_tools():
    """create_kore_tools viene caricato on-demand."""
    from kore_memory.integrations import create_kore_tools
    assert create_kore_tools is not None


def test_lazy_load_kore_agent_tools():
    """kore_agent_tools viene caricato on-demand."""
    from kore_memory.integrations import kore_agent_tools
    assert kore_agent_tools is not None


def test_lazy_load_extract_entities():
    """extract_entities viene caricato on-demand."""
    from kore_memory.integrations import extract_entities
    assert extract_entities is not None


def test_lazy_load_auto_tag_entities():
    """auto_tag_entities viene caricato on-demand."""
    from kore_memory.integrations import auto_tag_entities
    assert auto_tag_entities is not None


def test_lazy_load_search_entities():
    """search_entities viene caricato on-demand."""
    from kore_memory.integrations import search_entities
    assert search_entities is not None


def test_getattr_raises_on_invalid_name():
    """AttributeError viene sollevato per nomi non validi."""
    from kore_memory import integrations
    with pytest.raises(AttributeError, match="has no attribute"):
        integrations.NonExistentAttribute
