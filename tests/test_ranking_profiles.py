"""Tests for Ranking Profiles per-Agent (#98)."""

import pytest
from fastapi.testclient import TestClient

from kore_memory.main import app

client = TestClient(app)
HEADERS = {"X-Agent-Id": "rank-test"}


class TestRankingProfileCRUD:
    """Test CRUD operations via API."""

    def test_list_empty(self):
        r = client.get("/ranking/profiles", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_save_profile(self):
        r = client.put("/ranking/profiles", json={
            "weights": {"similarity": 0.6, "decay_score": 0.2, "confidence": 0.1, "freshness": 0.1},
            "profile_name": "custom",
        }, headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["profile_name"] == "custom"

    def test_list_after_save(self):
        client.put("/ranking/profiles", json={
            "weights": {"similarity": 0.5, "decay_score": 0.3},
            "profile_name": "my-profile",
        }, headers=HEADERS)
        r = client.get("/ranking/profiles", headers=HEADERS)
        names = [p["profile_name"] for p in r.json()["profiles"]]
        assert "my-profile" in names

    def test_update_existing(self):
        client.put("/ranking/profiles", json={
            "weights": {"similarity": 0.5},
            "profile_name": "update-test",
        }, headers=HEADERS)
        client.put("/ranking/profiles", json={
            "weights": {"similarity": 0.9},
            "profile_name": "update-test",
        }, headers=HEADERS)
        r = client.get("/ranking/profiles", headers=HEADERS)
        profile = next(p for p in r.json()["profiles"] if p["profile_name"] == "update-test")
        assert profile["weights"]["similarity"] == 0.9

    def test_delete_profile(self):
        client.put("/ranking/profiles", json={
            "weights": {"similarity": 0.5},
            "profile_name": "to-delete",
        }, headers=HEADERS)
        r = client.delete("/ranking/profiles/to-delete", headers=HEADERS)
        assert r.status_code == 200

    def test_delete_nonexistent(self):
        r = client.delete("/ranking/profiles/nonexistent", headers=HEADERS)
        assert r.status_code == 404


class TestRankingProfileValidation:
    """Test weight validation."""

    def test_invalid_key_rejected(self):
        r = client.put("/ranking/profiles", json={
            "weights": {"invalid_key": 0.5},
        }, headers=HEADERS)
        assert r.status_code == 422

    def test_negative_weight_rejected(self):
        r = client.put("/ranking/profiles", json={
            "weights": {"similarity": -0.5},
        }, headers=HEADERS)
        assert r.status_code == 422

    def test_sum_over_one_rejected(self):
        r = client.put("/ranking/profiles", json={
            "weights": {"similarity": 0.8, "decay_score": 0.8},
        }, headers=HEADERS)
        assert r.status_code == 422


class TestRankingProfileIntegration:
    """Test that custom profiles affect search ranking."""

    def _save(self, content, category="general"):
        r = client.post("/save", json={"content": content, "category": category}, headers=HEADERS)
        return r.json()["id"]

    def test_search_uses_custom_profile(self):
        """Save a custom profile, search, verify ranking_profile in response."""
        self._save("Integration test memory for ranking profile verification")
        # Save custom profile
        client.put("/ranking/profiles", json={
            "weights": {"similarity": 0.8, "decay_score": 0.1, "confidence": 0.1},
            "profile_name": "custom",
        }, headers=HEADERS)
        # Search with custom profile
        r = client.get("/search?q=ranking+profile+verification&ranking_profile=custom", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["ranking_profile"] == "custom"

    def test_fallback_to_default(self):
        """Search with non-existent profile falls back to default."""
        self._save("Fallback test memory for default ranking profile")
        r = client.get("/search?q=fallback+default&ranking_profile=nonexistent", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["ranking_profile"] == "nonexistent"  # name preserved in response

    def test_agent_isolation(self):
        """Profiles are scoped to agent_id."""
        client.put("/ranking/profiles", json={
            "weights": {"similarity": 0.9},
            "profile_name": "isolated",
        }, headers=HEADERS)
        # Different agent should not see it
        r = client.get("/ranking/profiles", headers={"X-Agent-Id": "other-agent"})
        names = [p["profile_name"] for p in r.json()["profiles"]]
        assert "isolated" not in names
