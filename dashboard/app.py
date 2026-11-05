"""
dashboard/app.py
────────────────
FastAPI REST API for the Launch Radar Dashboard.
Provides read-only launch feed, project review, audit log, and blacklist visibility.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytz
import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config.settings import get_settings
from database.connection import get_db
from database.models import (
    Announcement,
    Assessment,
    AuditEvent,
    BlacklistEntry,
    Project,
    ProjectStatus,
)

logger = structlog.get_logger(__name__)
settings = get_settings()

app = FastAPI(
    title="Launch Radar API",
    description="Universal visibility launch radar and review dashboard",
    version="3.1.0",
)

# Enable CORS for dashboard web UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════
#  Pydantic Schemas
# ══════════════════════════════════════════════════════════════════

class HealthResponse(BaseModel):
    status: str
    env: str
    timestamp: datetime


class ProjectSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: Optional[str]
    ticker: Optional[str]
    chain_id: Optional[str]
    ca_address: Optional[str]
    status: str
    score: Optional[float]
    is_blacklisted: bool
    expected_launch_at: Optional[datetime]
    created_at: datetime


class AnnouncementDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    platform: str
    message_id: Optional[str]
    post_url: Optional[str]
    text: Optional[str]
    author_id: Optional[str]
    event_type: str
    published_at: Optional[datetime]
    received_at: datetime
    is_forwarded: bool


class AssessmentDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    buy_tax_pct: Optional[float]
    sell_tax_pct: Optional[float]
    tax_result: Optional[str]
    volume_usd: Optional[float]
    volume_result: Optional[str]
    safety_result: Optional[str]
    liquidity_result: Optional[str]
    route_result: Optional[str]
    overall_result: Optional[str]
    provider: Optional[str]
    assessed_at: datetime


class ProjectDetail(ProjectSummary):
    description: Optional[str]
    official_x_handle: Optional[str]
    official_telegram: Optional[str]
    website_url: Optional[str]
    expected_ca_source: Optional[str]
    identity_keys: Optional[Dict[str, Any]]
    latest_announcement: Optional[AnnouncementDetail] = None
    latest_assessment: Optional[AssessmentDetail] = None


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor: str
    action: str
    entity_type: Optional[str]
    entity_id: Optional[str]
    old_state: Optional[str]
    new_state: Optional[str]
    reason: Optional[str]
    detail: Optional[str]
    created_at: datetime


class BlacklistResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    project_name: Optional[str] = None
    project_ticker: Optional[str] = None
    reason: Optional[str]
    created_by: str
    created_at: datetime
    is_unblocked: bool


# ══════════════════════════════════════════════════════════════════
#  Endpoints
# ══════════════════════════════════════════════════════════════════

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse, tags=["Dashboard"])
async def dashboard_home() -> str:
    """Live web dashboard UI for monitoring the token sniper radar."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Launch Radar & Sniper Bot — Dashboard</title>
  <style>
    :root {
      --bg: #0d1117;
      --card-bg: #161b22;
      --border: #30363d;
      --text: #e6edf3;
      --text-muted: #8b949e;
      --primary: #58a6ff;
      --green: #3fb950;
      --red: #f85149;
      --yellow: #d29922;
      --purple: #bc8cff;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    body { background-color: var(--bg); color: var(--text); padding: 24px; }
    .container { max-width: 1300px; margin: 0 auto; }
    header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 20px; border-bottom: 1px solid var(--border); margin-bottom: 24px; flex-wrap: wrap; gap: 12px; }
    h1 { font-size: 1.5rem; display: flex; align-items: center; gap: 10px; font-weight: 700; }
    .pulse { width: 10px; height: 10px; background: var(--green); border-radius: 50%; box-shadow: 0 0 10px var(--green); display: inline-block; }
    .header-actions { display: flex; align-items: center; gap: 12px; }
    .btn { background: #21262d; border: 1px solid var(--border); color: var(--text); padding: 6px 14px; border-radius: 6px; cursor: pointer; text-decoration: none; font-size: 0.85rem; font-weight: 500; transition: all 0.2s; }
    .btn:hover { background: #30363d; border-color: #8b949e; }
    .btn-primary { background: #238636; border-color: #2ea043; color: #fff; }
    .btn-primary:hover { background: #2ea043; }
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
    .stat-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
    .stat-title { font-size: 0.8rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }
    .stat-value { font-size: 1.6rem; font-weight: 700; }
    .nav-tabs { display: flex; gap: 8px; border-bottom: 1px solid var(--border); margin-bottom: 18px; }
    .tab { padding: 10px 16px; cursor: pointer; font-size: 0.9rem; font-weight: 600; color: var(--text-muted); border-bottom: 2px solid transparent; }
    .tab.active { color: var(--primary); border-bottom-color: var(--primary); }
    .tab-content { display: none; }
    .tab-content.active { display: block; }
    .table-container { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; text-align: left; font-size: 0.88rem; }
    th { background: #1b2128; padding: 12px 16px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border); }
    td { padding: 12px 16px; border-bottom: 1px solid var(--border); }
    tr:last-child td { border-bottom: none; }
    tr:hover { background: rgba(255, 255, 255, 0.02); }
    .badge { padding: 3px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; display: inline-block; text-transform: uppercase; }
    .badge-pending { background: rgba(210, 153, 34, 0.2); color: var(--yellow); border: 1px solid rgba(210, 153, 34, 0.4); }
    .badge-approved { background: rgba(63, 185, 80, 0.2); color: var(--green); border: 1px solid rgba(63, 185, 80, 0.4); }
    .badge-rejected { background: rgba(248, 81, 73, 0.2); color: var(--red); border: 1px solid rgba(248, 81, 73, 0.4); }
    .badge-buying { background: rgba(88, 166, 255, 0.2); color: var(--primary); border: 1px solid rgba(88, 166, 255, 0.4); }
    .badge-default { background: rgba(139, 148, 158, 0.2); color: var(--text-muted); border: 1px solid var(--border); }
    .mono { font-family: monospace; font-size: 0.82rem; }
    .empty-state { padding: 48px; text-align: center; color: var(--text-muted); }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1><span class="pulse"></span> Launch Radar & Sniper Bot</h1>
      <div class="header-actions">
        <button class="btn" onclick="fetchData()">🔄 Refresh</button>
        <a class="btn btn-primary" href="/docs" target="_blank">📖 Swagger API Docs</a>
      </div>
    </header>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-title">System Status</div>
        <div class="stat-value" id="stat-status" style="color: var(--green);">ONLINE</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">Total Discovered</div>
        <div class="stat-value" id="stat-total">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">Pending Operator Review</div>
        <div class="stat-value" id="stat-pending" style="color: var(--yellow);">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">Approved / Monitoring CA</div>
        <div class="stat-value" id="stat-approved" style="color: var(--green);">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">Blacklisted</div>
        <div class="stat-value" id="stat-blacklisted" style="color: var(--red);">0</div>
      </div>
    </div>

    <div class="nav-tabs">
      <div class="tab active" onclick="switchTab('feed', this)">🚀 Live Radar Feed</div>
      <div class="tab" onclick="switchTab('blacklist', this)">🛡️ Blacklist</div>
      <div class="tab" onclick="switchTab('audit', this)">📜 Audit Trail</div>
    </div>

    <!-- Live Feed Tab -->
    <div id="tab-feed" class="tab-content active">
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>Project</th>
              <th>Ticker</th>
              <th>Chain</th>
              <th>Status</th>
              <th>Contract Address</th>
              <th>Discovered At</th>
            </tr>
          </thead>
          <tbody id="feed-tbody">
            <tr><td colspan="6" class="empty-state">Loading radar feed...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Blacklist Tab -->
    <div id="tab-blacklist" class="tab-content">
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>Project Name</th>
              <th>Ticker</th>
              <th>Reason</th>
              <th>Blacklisted By</th>
              <th>Date</th>
            </tr>
          </thead>
          <tbody id="blacklist-tbody">
            <tr><td colspan="5" class="empty-state">Loading blacklist entries...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Audit Tab -->
    <div id="tab-audit" class="tab-content">
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>Action</th>
              <th>Actor</th>
              <th>Detail</th>
              <th>Timestamp</th>
            </tr>
          </thead>
          <tbody id="audit-tbody">
            <tr><td colspan="4" class="empty-state">Loading audit events...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <script>
    function switchTab(tabName, el) {
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      el.classList.add('active');
      document.getElementById('tab-' + tabName).classList.add('active');
    }

    function getBadgeClass(status) {
      const s = (status || '').toLowerCase();
      if (s.includes('pending')) return 'badge-pending';
      if (s.includes('approved') || s.includes('passed') || s.includes('bought')) return 'badge-approved';
      if (s.includes('reject') || s.includes('black') || s.includes('blocked')) return 'badge-rejected';
      if (s.includes('buying') || s.includes('ca_detected')) return 'badge-buying';
      return 'badge-default';
    }

    async function fetchData() {
      try {
        const [health, projects, blacklist, audit] = await Promise.all([
          fetch('/health').then(r => r.json()).catch(() => ({ status: 'OFFLINE' })),
          fetch('/projects?limit=100').then(r => r.json()).catch(() => []),
          fetch('/blacklist').then(r => r.json()).catch(() => []),
          fetch('/audit?limit=50').then(r => r.json()).catch(() => [])
        ]);

        // Health
        const statusEl = document.getElementById('stat-status');
        statusEl.textContent = health.status ? health.status.toUpperCase() : 'ONLINE';
        statusEl.style.color = health.status === 'ok' ? 'var(--green)' : 'var(--red)';

        // Counters
        document.getElementById('stat-total').textContent = projects.length;
        document.getElementById('stat-pending').textContent = projects.filter(p => (p.status || '').includes('pending')).length;
        document.getElementById('stat-approved').textContent = projects.filter(p => (p.status || '').includes('approved')).length;
        document.getElementById('stat-blacklisted').textContent = blacklist.length;

        // Feed Table
        const feedTbody = document.getElementById('feed-tbody');
        if (projects.length === 0) {
          feedTbody.innerHTML = '<tr><td colspan="6" class="empty-state">No launches detected yet. The radar is listening on X & Telegram!</td></tr>';
        } else {
          feedTbody.innerHTML = projects.map(p => `
            <tr>
              <td><strong>${p.name || 'Unknown'}</strong></td>
              <td><span class="mono">${p.ticker || 'N/A'}</span></td>
              <td>${p.chain_id || 'robinhood'}</td>
              <td><span class="badge ${getBadgeClass(p.status)}">${p.status}</span></td>
              <td><span class="mono">${p.ca_address || 'Pending Drop'}</span></td>
              <td>${new Date(p.created_at).toLocaleTimeString()}</td>
            </tr>
          `).join('');
        }

        // Blacklist Table
        const blTbody = document.getElementById('blacklist-tbody');
        if (blacklist.length === 0) {
          blTbody.innerHTML = '<tr><td colspan="5" class="empty-state">No blacklisted projects.</td></tr>';
        } else {
          blTbody.innerHTML = blacklist.map(b => `
            <tr>
              <td><strong>${b.project_name || 'N/A'}</strong></td>
              <td><span class="mono">${b.project_ticker || 'N/A'}</span></td>
              <td>${b.reason || 'Manual rejection'}</td>
              <td>${b.created_by}</td>
              <td>${new Date(b.created_at).toLocaleString()}</td>
            </tr>
          `).join('');
        }

        // Audit Table
        const auditTbody = document.getElementById('audit-tbody');
        if (audit.length === 0) {
          auditTbody.innerHTML = '<tr><td colspan="4" class="empty-state">No audit logs recorded yet.</td></tr>';
        } else {
          auditTbody.innerHTML = audit.map(a => `
            <tr>
              <td><span class="badge badge-default">${a.action}</span></td>
              <td>${a.actor}</td>
              <td>${a.detail || ''}</td>
              <td>${new Date(a.created_at).toLocaleTimeString()}</td>
            </tr>
          `).join('');
        }
      } catch (err) {
        console.error('Failed to refresh dashboard data:', err);
      }
    }

    fetchData();
    setInterval(fetchData, 4000); // Live poll every 4 seconds
  </script>
</body>
</html>
"""

@app.api_route("/health", methods=["GET", "HEAD"], response_model=HealthResponse, tags=["System"])
async def health_check() -> HealthResponse:
    """System health and environment status."""
    return HealthResponse(
        status="ok",
        env=settings.app_env,
        timestamp=datetime.now(timezone.utc),
    )


@app.get("/projects", response_model=List[ProjectSummary], tags=["Projects"])
async def list_projects(
    status: Optional[str] = Query(None, description="Filter by status (e.g. pending_approval)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> List[ProjectSummary]:
    """List detected projects with optional status filter."""
    stmt = select(Project).order_by(desc(Project.created_at))
    if status:
        stmt = stmt.where(Project.status == status.lower())
    stmt = stmt.limit(limit).offset(offset)

    result = await db.execute(stmt)
    projects = result.scalars().all()
    return [ProjectSummary.model_validate(p) for p in projects]


@app.get("/feed", response_model=List[ProjectSummary], tags=["Feed"])
async def today_feed(
    db: AsyncSession = Depends(get_db),
) -> List[ProjectSummary]:
    """
    Today Launch Radar feed.
    Returns all launches detected today in the configured operator timezone.
    Spec v3.1 §4.3: Displays every detected launch, including low-scoring and rejected.
    """
    tz_name = settings.operator_timezone
    try:
        op_tz = pytz.timezone(tz_name)
    except Exception:
        op_tz = pytz.UTC

    now_op = datetime.now(op_tz)
    start_of_today_op = now_op.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_today_utc = start_of_today_op.astimezone(pytz.UTC)

    stmt = (
        select(Project)
        .where(Project.created_at >= start_of_today_utc)
        .order_by(desc(Project.created_at))
    )
    result = await db.execute(stmt)
    projects = result.scalars().all()
    return [ProjectSummary.model_validate(p) for p in projects]


@app.get("/projects/{project_id}", response_model=ProjectDetail, tags=["Projects"])
async def get_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> ProjectDetail:
    """Get full details for a single project including latest announcement and assessment."""
    stmt = (
        select(Project)
        .where(Project.id == project_id)
        .options(
            selectinload(Project.announcements),
            selectinload(Project.assessments),
        )
    )
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )

    # Sort related to find latest
    latest_ann = None
    if project.announcements:
        sorted_ann = sorted(project.announcements, key=lambda a: a.received_at, reverse=True)
        latest_ann = AnnouncementDetail.model_validate(sorted_ann[0])

    latest_ass = None
    if project.assessments:
        sorted_ass = sorted(project.assessments, key=lambda a: a.assessed_at, reverse=True)
        latest_ass = AssessmentDetail.model_validate(sorted_ass[0])

    detail = ProjectDetail.model_validate(project)
    detail.latest_announcement = latest_ann
    detail.latest_assessment = latest_ass
    return detail


@app.get("/projects/{project_id}/announcements", response_model=List[AnnouncementDetail], tags=["Projects"])
async def get_project_announcements(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> List[AnnouncementDetail]:
    """Get all announcements associated with a specific project."""
    stmt = (
        select(Announcement)
        .where(Announcement.project_id == project_id)
        .order_by(desc(Announcement.received_at))
    )
    result = await db.execute(stmt)
    announcements = result.scalars().all()
    return [AnnouncementDetail.model_validate(a) for a in announcements]


@app.get("/audit", response_model=List[AuditEventResponse], tags=["Audit"])
async def get_audit_trail(
    entity_id: Optional[str] = Query(None, description="Filter by entity/project ID"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> List[AuditEventResponse]:
    """Retrieve immutable audit events for transparency and compliance."""
    stmt = select(AuditEvent).order_by(desc(AuditEvent.created_at))
    if entity_id:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    stmt = stmt.limit(limit)

    result = await db.execute(stmt)
    events = result.scalars().all()
    return [AuditEventResponse.model_validate(e) for e in events]


@app.get("/blacklist", response_model=List[BlacklistResponse], tags=["Blacklist"])
async def get_blacklist(
    db: AsyncSession = Depends(get_db),
) -> List[BlacklistResponse]:
    """List all persistently blacklisted projects."""
    stmt = (
        select(BlacklistEntry, Project)
        .join(Project, BlacklistEntry.project_id == Project.id)
        .where(BlacklistEntry.is_unblocked == False)  # noqa: E712
        .order_by(desc(BlacklistEntry.created_at))
    )
    result = await db.execute(stmt)
    rows = result.all()

    items = []
    for entry, proj in rows:
        item = BlacklistResponse(
            id=entry.id,
            project_id=entry.project_id,
            project_name=proj.name,
            project_ticker=proj.ticker,
            reason=entry.reason,
            created_by=entry.created_by,
            created_at=entry.created_at,
            is_unblocked=entry.is_unblocked,
        )
        items.append(item)
    return items

