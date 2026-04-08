"""
Test per la dashboard web di Kore.
Verifica che /dashboard risponda correttamente e che l'HTML contenga le sezioni attese.
"""

import httpx
import pytest

from kore_memory.database import init_db
from kore_memory.main import app


@pytest.fixture(autouse=True)
def _setup_db():
    """Inizializza il database prima di ogni test."""
    init_db()


@pytest.fixture()
async def client():
    """Client HTTP async per i test (ASGITransport richiede AsyncClient)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ── Test route dashboard ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_dashboard_returns_html(client):
    """GET /dashboard deve ritornare 200 con content-type text/html."""
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.anyio
async def test_dashboard_contains_all_sections(client):
    """L'HTML deve contenere tutte le 9 pagine della dashboard."""
    resp = await client.get("/dashboard")
    html = resp.text
    # Ogni pagina è identificata da data-page="..." nel nuovo layout
    sections = [
        'data-page="overview"',
        'data-page="memories"',
        'data-page="tags"',
        'data-page="graph"',
        'data-page="sessions"',
        'data-page="timeline"',
        'data-page="analytics"',
        'data-page="maintenance"',
        'data-page="settings"',
    ]
    for section in sections:
        assert section in html, f"Sezione {section} mancante dall'HTML"


@pytest.mark.anyio
async def test_dashboard_no_auth_required(client):
    """La dashboard non deve richiedere autenticazione (no X-Kore-Key)."""
    resp = await client.get("/dashboard")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_dashboard_has_relaxed_csp(client):
    """La dashboard deve avere CSP allargato (unsafe-inline) non quello restrittivo delle API."""
    resp = await client.get("/dashboard")
    csp = resp.headers.get("content-security-policy", "")
    assert "'unsafe-inline'" in csp
    assert "default-src 'none'" not in csp


@pytest.mark.anyio
async def test_api_keeps_strict_csp(client):
    """Le API devono mantenere il CSP restrittivo (default-src 'none')."""
    resp = await client.get("/health")
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp


@pytest.mark.anyio
async def test_dashboard_contains_kore_branding(client):
    """L'HTML deve contenere il branding Kore (titolo, logo)."""
    resp = await client.get("/dashboard")
    html = resp.text
    assert "Kore" in html
    assert "Memory Dashboard" in html


@pytest.mark.anyio
async def test_dashboard_contains_js_api_helpers(client):
    """L'HTML deve contenere le funzioni JS per chiamare le API."""
    resp = await client.get("/dashboard")
    html = resp.text
    # Nuovo layout: API client come oggetto, funzioni per ogni pagina
    assert "var api =" in html
    assert "function loadMemories(" in html
    assert "function loadOverview(" in html
    assert "function loadGraph(" in html
    assert "function loadAnalytics(" in html
