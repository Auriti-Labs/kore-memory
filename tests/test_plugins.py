"""
Kore — Plugins system tests
Test plugin hooks: pre/post save, search, delete, compress.
"""

import pytest

from kore_memory.plugins import (
    KorePlugin,
    register_plugin,
    unregister_plugin,
    run_pre_save,
    run_post_save,
    run_pre_search,
    run_post_search,
    run_pre_delete,
    run_post_delete,
    run_pre_compress,
    run_post_compress,
    list_plugins,
    clear_plugins,
)


class TestPluginRegistration:
    def test_register_plugin(self):
        """Plugin can be registered."""
        class TestPlugin(KorePlugin):
            name = "test-plugin"

        register_plugin(TestPlugin())
        plugins = list_plugins()
        assert "test-plugin" in plugins

    def test_unregister_plugin(self):
        """Plugin can be unregistered."""
        class TestPlugin2(KorePlugin):
            name = "test-plugin-2"

        plugin = TestPlugin2()
        register_plugin(plugin)
        unregister_plugin("test-plugin-2")
        plugins = list_plugins()
        assert "test-plugin-2" not in plugins


class TestPluginHooks:
    def setup_method(self):
        """Clear plugins before each test."""
        clear_plugins()

    def test_pre_save_hook_called(self):
        """pre_save hook is called with memory data."""
        hook_called = []

        class HookPlugin(KorePlugin):
            name = "hook-plugin"

            def pre_save(self, content, category, importance, agent_id):
                hook_called.append((content, category, importance, agent_id))
                return {"content": "modified"}

        plugin = HookPlugin()
        register_plugin(plugin)

        result = run_pre_save("test", "general", None, "hook-test-agent")
        assert len(hook_called) >= 1
        assert hook_called[-1][3] == "hook-test-agent"
        assert result.get("content") == "modified"

    def test_post_save_hook_called(self):
        """post_save hook is called with memory result."""
        hook_called = []

        class HookPlugin2(KorePlugin):
            name = "hook-plugin-2"

            def post_save(self, memory_id, content, category, importance, agent_id):
                hook_called.append((memory_id, content, category, importance, agent_id))

        plugin = HookPlugin2()
        register_plugin(plugin)

        run_post_save(1, "test", "general", 3, "hook-test-agent")
        assert len(hook_called) >= 1
        assert hook_called[-1][0] == 1
        assert hook_called[-1][4] == "hook-test-agent"

    def test_pre_search_hook_called(self):
        """pre_search hook is called with query params."""
        hook_called = []

        class HookPlugin3(KorePlugin):
            name = "hook-plugin-3"

            def pre_search(self, query, agent_id, semantic):
                hook_called.append((query, agent_id, semantic))
                return {"query": "modified"}

        plugin = HookPlugin3()
        register_plugin(plugin)

        result = run_pre_search("test query", "hook-test-agent", True)
        assert len(hook_called) >= 1
        assert hook_called[-1][1] == "hook-test-agent"

    def test_post_search_hook_called(self):
        """post_search hook is called with results."""
        hook_called = []

        class HookPlugin4(KorePlugin):
            name = "hook-plugin-4"

            def post_search(self, query, results, agent_id):
                hook_called.append((query, results, agent_id))
                return results + [{"id": 999}]

        plugin = HookPlugin4()
        register_plugin(plugin)

        result = run_post_search("test query", [{"id": 1}], "hook-test-agent")
        assert len(hook_called) >= 1
        assert len(result) == 2  # Added one more

    def test_pre_delete_hook_called(self):
        """pre_delete hook is called before deletion."""
        hook_called = []

        class HookPlugin5(KorePlugin):
            name = "hook-plugin-5"

            def pre_delete(self, memory_id, agent_id):
                hook_called.append((memory_id, agent_id))
                return True

        plugin = HookPlugin5()
        register_plugin(plugin)

        result = run_pre_delete(123, "hook-test-agent")
        assert len(hook_called) >= 1
        assert hook_called[-1] == (123, "hook-test-agent")
        assert result is True

    def test_post_delete_hook_called(self):
        """post_delete hook is called after deletion."""
        hook_called = []

        class HookPlugin6(KorePlugin):
            name = "hook-plugin-6"

            def post_delete(self, memory_id, agent_id):
                hook_called.append((memory_id, agent_id))

        plugin = HookPlugin6()
        register_plugin(plugin)

        run_post_delete(123, "hook-test-agent")
        assert len(hook_called) >= 1
        assert hook_called[-1][0] == 123

    def test_pre_compress_hook_called(self):
        """pre_compress hook is called before compression."""
        hook_called = []

        class HookPlugin7(KorePlugin):
            name = "hook-plugin-7"

            def pre_compress(self, agent_id):
                hook_called.append(agent_id)
                return True

        plugin = HookPlugin7()
        register_plugin(plugin)

        result = run_pre_compress("hook-test-agent")
        assert len(hook_called) >= 1
        assert hook_called[-1] == "hook-test-agent"
        assert result is True

    def test_post_compress_hook_called(self):
        """post_compress hook is called after compression."""
        hook_called = []

        class HookPlugin8(KorePlugin):
            name = "hook-plugin-8"

            def post_compress(self, clusters_found, merged, agent_id):
                hook_called.append((clusters_found, merged, agent_id))

        plugin = HookPlugin8()
        register_plugin(plugin)

        run_post_compress(2, 5, "hook-test-agent")
        assert len(hook_called) >= 1
        assert hook_called[-1][0] == 2
        assert hook_called[-1][1] == 5


class TestPluginBaseClass:
    def test_default_hook_implementations(self):
        """Base class hooks return defaults without error."""
        # KorePlugin is abstract, need concrete subclass
        class ConcretePlugin(KorePlugin):
            name = "concrete-plugin"

        plugin = ConcretePlugin()

        # All hooks should have default implementations
        assert plugin.pre_save("test", "general", None, "agent") is None
        plugin.post_save(1, "test", "general", 3, "agent")  # Returns None
        assert plugin.pre_search("q", "agent", True) is None
        assert plugin.post_search("q", [], "agent") == []
        assert plugin.pre_delete(1, "agent") is True
        plugin.post_delete(1, "agent")  # Returns None
        assert plugin.pre_compress("agent") is True
        plugin.post_compress(0, 0, "agent")  # Returns None
