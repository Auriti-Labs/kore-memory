"""
Kore — ACL (Access Control List) tests
Test multi-agent ACL: grant/revoke/check permissions.
"""

import pytest

from kore_memory.acl import (
    grant_access,
    revoke_access,
    check_access,
    list_permissions,
    get_shared_memories,
)
from kore_memory.repository.memory import save_memory
from kore_memory.models import MemorySaveRequest


class TestGrantAccess:
    def test_grant_read_access(self):
        """Grant read access to another agent."""
        owner = "acl-owner-agent"
        other = "acl-other-agent"

        req = MemorySaveRequest(content="Shared memory test", category="project")
        memory_id, _, _ = save_memory(req, agent_id=owner)

        success = grant_access(memory_id, other, "read", owner)
        assert success is True

    def test_grant_write_access(self):
        """Grant write access to another agent."""
        owner = "acl-owner-agent-2"
        other = "acl-other-agent-2"

        req = MemorySaveRequest(content="Shared write test", category="project")
        memory_id, _, _ = save_memory(req, agent_id=owner)

        success = grant_access(memory_id, other, "write", owner)
        assert success is True

    def test_grant_admin_access(self):
        """Grant admin access to another agent."""
        owner = "acl-owner-agent-3"
        other = "acl-other-agent-3"

        req = MemorySaveRequest(content="Shared admin test", category="project")
        memory_id, _, _ = save_memory(req, agent_id=owner)

        success = grant_access(memory_id, other, "admin", owner)
        assert success is True

    def test_grant_nonexistent_memory_fails(self):
        """Grant access on nonexistent memory returns False."""
        success = grant_access(999999, "some-agent", "read", "owner")
        assert success is False


class TestRevokeAccess:
    def test_revoke_access(self):
        """Revoke access from another agent."""
        owner = "acl-revoke-owner"
        other = "acl-revoke-other"

        req = MemorySaveRequest(content="Revoke test", category="project")
        memory_id, _, _ = save_memory(req, agent_id=owner)

        grant_access(memory_id, other, "read", owner)
        success = revoke_access(memory_id, other, owner)
        assert success is True

    def test_revoke_nonexistent_fails(self):
        """Revoke access on nonexistent memory returns False."""
        success = revoke_access(999999, "some-agent", "owner")
        assert success is False


class TestCheckAccess:
    def test_owner_has_full_access(self):
        """Owner always has full access."""
        owner = "acl-check-owner"

        req = MemorySaveRequest(content="Owner access test", category="project")
        memory_id, _, _ = save_memory(req, agent_id=owner)

        has_read = check_access(memory_id, owner, "read")
        has_write = check_access(memory_id, owner, "write")
        has_admin = check_access(memory_id, owner, "admin")

        assert has_read is True
        assert has_write is True
        assert has_admin is True

    def test_granted_agent_has_access(self):
        """Agent with granted access can access."""
        owner = "acl-check-owner-2"
        other = "acl-check-other"

        req = MemorySaveRequest(content="Check access test", category="project")
        memory_id, _, _ = save_memory(req, agent_id=owner)

        grant_access(memory_id, other, "read", owner)

        has_read = check_access(memory_id, other, "read")
        has_write = check_access(memory_id, other, "write")

        assert has_read is True
        assert has_write is False  # Only read was granted

    def test_unauthorized_agent_no_access(self):
        """Agent without grant has no access."""
        owner = "acl-check-owner-3"
        other = "acl-unauthorized"

        req = MemorySaveRequest(content="Unauthorized test", category="project")
        memory_id, _, _ = save_memory(req, agent_id=owner)

        has_read = check_access(memory_id, other, "read")
        assert has_read is False


class TestListPermissions:
    def test_list_permissions(self):
        """List all permissions for a memory."""
        owner = "acl-list-owner"
        other1 = "acl-list-other-1"
        other2 = "acl-list-other-2"

        req = MemorySaveRequest(content="List permissions test", category="project")
        memory_id, _, _ = save_memory(req, agent_id=owner)

        grant_access(memory_id, other1, "read", owner)
        grant_access(memory_id, other2, "write", owner)

        perms = list_permissions(memory_id, owner)
        # list_permissions returns a list of permission dicts
        assert isinstance(perms, list)
        assert len(perms) >= 2


class TestGetSharedMemories:
    def test_get_shared_memories(self):
        """Get memories shared with an agent."""
        owner = "acl-shared-owner"
        other = "acl-shared-other"

        req1 = MemorySaveRequest(content="Shared memory 1", category="project")
        memory_id1, _, _ = save_memory(req1, agent_id=owner)
        grant_access(memory_id1, other, "read", owner)

        req2 = MemorySaveRequest(content="Shared memory 2", category="project")
        memory_id2, _, _ = save_memory(req2, agent_id=owner)
        grant_access(memory_id2, other, "read", owner)

        shared = get_shared_memories(other)
        # get_shared_memories returns a list of memory dicts
        assert isinstance(shared, list)
        assert len(shared) >= 2
