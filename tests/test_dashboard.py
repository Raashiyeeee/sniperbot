"""
tests/test_dashboard.py
────────────────────────
Tests for the Launch Radar FastAPI Dashboard.
Uses in-memory SQLite and FastAPI TestClient.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dashboard.app import app
from database.connection import get_db
from database.models import AuditEvent, Base, BlacklistEntry, Project, ProjectStatus


@pytest.fixture(scope="module")
def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture
def client(db_engine):
    session_maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health_check(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Launch Radar" in res.text

    # HEAD request support for Uptime monitors
    res_head = client.head("/")
    assert res_head.status_code == 200

    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "env" in data

    res_health_head = client.head("/health")
    assert res_health_head.status_code == 200


def test_list_projects_and_feed(client, db_engine):
    # Insert a sample project
    session_maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def _insert():
        async with session_maker() as session:
            proj = Project(
                id="proj-test-1",
                name="TestRadarProject",
                ticker="TRP",
                chain_id="robinhood",
                status=ProjectStatus.PENDING_APPROVAL,
                created_at=datetime.now(timezone.utc),
            )
            session.add(proj)
            await session.commit()

    asyncio.run(_insert())

    res = client.get("/projects")
    assert res.status_code == 200
    projects = res.json()
    assert len(projects) >= 1
    assert any(p["id"] == "proj-test-1" for p in projects)

    feed_res = client.get("/feed")
    assert feed_res.status_code == 200
    feed_items = feed_res.json()
    assert any(p["id"] == "proj-test-1" for p in feed_items)


def test_get_project_detail(client):
    res = client.get("/projects/proj-test-1")
    assert res.status_code == 200
    detail = res.json()
    assert detail["name"] == "TestRadarProject"
    assert detail["ticker"] == "TRP"


def test_get_project_not_found(client):
    res = client.get("/projects/non-existent-uuid")
    assert res.status_code == 404


def test_audit_and_blacklist_endpoints(client, db_engine):
    session_maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def _insert():
        async with session_maker() as session:
            audit = AuditEvent(
                id="audit-1",
                actor="operator:123",
                action="test_action",
                entity_id="proj-test-1",
                detail="Manual test entry",
                created_at=datetime.now(timezone.utc),
            )
            blacklisted = Project(
                id="proj-black-1",
                name="RugProject",
                ticker="RUG",
                chain_id="solana",
                status=ProjectStatus.REJECTED,
                is_blacklisted=True,
                created_at=datetime.now(timezone.utc),
            )
            bl_entry = BlacklistEntry(
                id="bl-1",
                project_id="proj-black-1",
                reason="Honeypot code detected",
                created_by="system",
                is_unblocked=False,
                created_at=datetime.now(timezone.utc),
            )
            session.add_all([audit, blacklisted, bl_entry])
            await session.commit()

    asyncio.run(_insert())

    # Check audit log endpoint
    res_audit = client.get("/audit")
    assert res_audit.status_code == 200
    audit_data = res_audit.json()
    assert any(a["id"] == "audit-1" for a in audit_data)

    # Check blacklist endpoint
    res_bl = client.get("/blacklist")
    assert res_bl.status_code == 200
    bl_data = res_bl.json()
    assert any(b["project_id"] == "proj-black-1" for b in bl_data)

