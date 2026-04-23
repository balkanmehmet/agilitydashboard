import logging
import os
import re
import time
from collections import defaultdict

import requests
import streamlit as st

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("standup_board")


def _log_state(label: str, state: dict):
    log.info("%s | ── STATE SNAPSHOT ──────────────────────", label)
    for k, v in state.items():
        if k == "issues":
            log.info("%s | issues (%d):", label, len(v or []))
            for i, issue in enumerate(v or []):
                log.info(
                    "%s |   [%d] key=%-8s status=%-12s assignee=%-20s summary=%r",
                    label, i,
                    issue.get("key", "?"),
                    issue.get("status", "?"),
                    issue.get("assignee", "?"),
                    str(issue.get("summary", ""))[:60],
                )
        elif k == "current_issue":
            ci = v or {}
            log.info(
                "%s | current_issue: key=%s status=%s summary=%r",
                label, ci.get("key", "—"), ci.get("status", "—"),
                str(ci.get("summary", ""))[:60],
            )
        else:
            log.info("%s | %-20s = %r", label, k, str(v)[:120])
    log.info("%s | ── END STATE ───────────────────────────", label)


# ── Config ────────────────────────────────────────────────────────────────────
FUNCTION_APP_BASE_URL = os.getenv("FUNCTION_APP_BASE_URL", "").rstrip("/")
FUNCTION_APP_CODE = os.getenv("FUNCTION_APP_CODE", "")
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "5"))

PREFERRED_STATUS_ORDER = ["New", "In Progress", "Blocked", "In Review", "Done"]

# Status badge colours: background, text
STATUS_COLORS = {
    "New":         ("#e3f2fd", "#1565c0"),
    "In Progress": ("#fff8e1", "#f57f17"),
    "Blocked":     ("#fce4ec", "#b71c1c"),
    "In Review":   ("#f3e5f5", "#6a1b9a"),
    "Done":        ("#e8f5e9", "#1b5e20"),
}
DEFAULT_STATUS_COLOR = ("#f1f3f4", "#3c4043")


def _status_badge(status: str) -> str:
    bg, fg = STATUS_COLORS.get(status, DEFAULT_STATUS_COLOR)
    return f"<span class='status-badge' style='background:{bg};color:{fg};'>{status}</span>"


# ── API ───────────────────────────────────────────────────────────────────────
def build_state_url(instance_id: str) -> str:
    if not FUNCTION_APP_BASE_URL:
        raise ValueError("FUNCTION_APP_BASE_URL is required")
    return f"{FUNCTION_APP_BASE_URL}/api/standup/state/{instance_id}"


def get_state(instance_id: str):
    params = {}
    if FUNCTION_APP_CODE:
        params["code"] = FUNCTION_APP_CODE
    response = requests.get(build_state_url(instance_id), params=params, timeout=20)
    response.raise_for_status()
    data = response.json()
    _log_state("RAW_API", data)
    return data


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def get_state_cached(instance_id: str, refresh_seconds: int):
    _ = refresh_seconds
    return get_state(instance_id)


def get_state_logged(instance_id: str, refresh_seconds: int) -> dict:
    data = get_state_cached(instance_id, refresh_seconds)
    _log_state("CACHE_RETURN", data)
    return data


# ── State management ──────────────────────────────────────────────────────────
def is_presentation_active(standup_status: str, spoken_text: str, current_issue_key) -> bool:
    status_value = (standup_status or "").strip().lower()
    active_tokens = ("speak", "present", "narrat", "wait", "listen", "advance_pending", "issue_discussion")
    has_active = any(t in status_value for t in active_tokens)
    return bool(current_issue_key) and (has_active or bool((spoken_text or "").strip()))


def choose_display_state(latest_state: dict) -> tuple[dict, bool]:
    latest_ci = latest_state.get("current_issue") or {}
    latest_key = latest_ci.get("key")
    latest_status = latest_state.get("status", "unknown")
    latest_text = latest_state.get("spoken_text", "")

    snapshot = st.session_state.get("display_state_snapshot")
    if snapshot:
        snap_key = (snapshot.get("current_issue") or {}).get("key")
        if snap_key and snap_key == latest_key and is_presentation_active(latest_status, latest_text, latest_key):
            log.info("CHOOSE_DISPLAY | returning LOCKED snapshot for key=%s", snap_key)
            _log_state("DISPLAY/snapshot", snapshot)
            return snapshot, True

    log.info("CHOOSE_DISPLAY | returning LATEST state")
    _log_state("DISPLAY/latest", latest_state)
    st.session_state["display_state_snapshot"] = latest_state
    return latest_state, False


# ── Helpers ───────────────────────────────────────────────────────────────────
def normalize_status(status: str) -> str:
    s = (status or "").strip().lower()
    mapping = {"new": "New", "in progress": "In Progress", "blocked": "Blocked",
                "in review": "In Review", "done": "Done"}
    return mapping.get(s, status or "Unknown")


def get_status_columns(issues: list[dict]) -> list[str]:
    extra, seen = [], set(PREFERRED_STATUS_ORDER)
    for issue in issues:
        sn = normalize_status(issue.get("status", ""))
        if sn not in seen:
            extra.append(sn)
            seen.add(sn)
    return list(PREFERRED_STATUS_ORDER) + extra


def clean(value) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return re.sub(r"\s+", " ", text)


def clean_multi(value) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def esc(value) -> str:
    """HTML-escape a plain string for safe insertion into st.html()."""
    return (str(value or "")
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ── Board renderer — pure HTML grid so all pillars are equal height ───────────
def render_board(issues: list, current_issue_key):
    grouped = defaultdict(list)
    for issue in issues:
        assignee = issue.get("assignee") or "Unassigned"
        grouped[assignee].append(issue)

    if not grouped:
        st.info("No issues available.")
        return

    status_columns = get_status_columns(issues)
    col_pct = 100 / len(status_columns)
    assignees = sorted(grouped.keys(), key=lambda x: x.lower())
    tabs = st.tabs([f"{clean(name)} ({len(grouped[name])})" for name in assignees])

    for tab, assignee in zip(tabs, assignees):
        assignee_issues = grouped[assignee]
        buckets: dict[str, list] = {s: [] for s in status_columns}
        for issue in assignee_issues:
            buckets[normalize_status(issue.get("status", ""))].append(issue)

        with tab:
            # Build the entire board as one HTML block so CSS grid enforces equal heights
            cols_html = ""
            for status_name in status_columns:
                bucket = buckets[status_name]
                cards_html = ""
                for issue in bucket:
                    key = esc(clean(issue.get("key", "-")))
                    summary = esc(clean(issue.get("summary", ""))) or "-"
                    priority = esc(clean(issue.get("priority", "-"))) or "-"
                    is_cur = issue.get("key") == current_issue_key
                    arrow = "▶ " if is_cur else ""
                    cur_cls = " card-current" if is_cur else ""
                    cards_html += f"""
                        <div class='jira-card{cur_cls}'>
                            <div class='card-key'>{arrow}{key}</div>
                            <div class='card-summary'>{summary}</div>
                            <div class='card-meta'>Priority: {priority}</div>
                        </div>"""
                if not cards_html:
                    cards_html = "<div class='empty-pillar'>—</div>"

                sn_esc = esc(status_name)
                cols_html += f"""
                    <div class='pillar' style='width:{col_pct:.2f}%'>
                        <div class='pillar-header'>{sn_esc} <span class='pillar-count'>({len(bucket)})</span></div>
                        <div class='pillar-cards'>{cards_html}</div>
                    </div>"""

            st.html(f"<div class='board-grid'>{cols_html}</div>")


# ── Current issue panel ───────────────────────────────────────────────────────
def render_bottom_current_issue(current_issue: dict, spoken_text: str, standup_status: str):
    st.divider()

    if not current_issue:
        st.html("<div class='current-empty'>No current issue selected.</div>")
        return

    key     = esc(clean(current_issue.get("key", "-")))
    summary = esc(clean(current_issue.get("summary", ""))) or "-"
    status  = clean(current_issue.get("status", "-")) or "-"
    assignee = esc(clean(current_issue.get("assignee", "-"))) or "-"
    priority = esc(clean(current_issue.get("priority", "-"))) or "-"
    description = clean_multi(current_issue.get("description") or "")
    narration   = clean_multi(spoken_text or "")

    status_badge = _status_badge(status)
    desc_block = (
        f"<div class='cur-label'>DESCRIPTION</div>"
        f"<div class='cur-body'>{esc(description).replace(chr(10), '<br>')}</div>"
        if description else ""
    )
    narr_block = (
        f"<div class='cur-label'>BOT NARRATION</div>"
        f"<div class='cur-body'>{esc(narration).replace(chr(10), '<br>')}</div>"
        if narration else ""
    )

    log.info("RENDER | key=%s status=%s", key, status)

    st.html(f"""
    <div class='current-panel'>
        <div class='cur-top'>
            <div class='cur-title'><span class='cur-key'>{key}</span><span class='cur-summary'>{summary}</span></div>
            {status_badge}
        </div>
        <div class='cur-meta-row'>
            <span class='meta-chip'>Assignee: <strong>{assignee}</strong></span>
            <span class='meta-chip'>Priority: <strong>{priority}</strong></span>
        </div>
        {desc_block}
        {narr_block}
    </div>
    """)


# ── Page setup ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Agility Standup Board", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
/* ── Layout ──────────────────────────────────────────────── */
.block-container { padding-top:0.6rem; padding-bottom:0.1rem; max-width:99%; }
h1,h2,h3 { margin-top:0.02rem; margin-bottom:0.02rem; }
hr { margin-top:0.2rem; margin-bottom:0.2rem; }
p { margin-bottom:0.04rem; }

/* ── Header ──────────────────────────────────────────────── */
.board-title {
    font-size:1.4rem; font-weight:800; color:#172b4d;
    letter-spacing:-0.01em; margin:0 0 0.5rem 0;
    border-bottom:3px solid #0052cc; padding-bottom:0.3rem;
    line-height:1.2;
}

/* ── Metrics ─────────────────────────────────────────────── */
div[data-testid="stMetric"] { padding:0.05rem 0.12rem; }
div[data-testid="stMetric"] label,
div[data-testid="stMetric"] [data-testid="stMetricLabel"] { font-size:0.6rem; }
div[data-testid="stMetricValue"] { font-size:0.78rem; }

/* ── Tabs ────────────────────────────────────────────────── */
div[data-testid="stTabs"] button { font-size:0.7rem; padding:0.06rem 0.4rem; }

/* ── Board grid — all pillars same height via flexbox ─────── */
.board-grid {
    display:flex;
    flex-direction:row;
    align-items:stretch;   /* ← makes every pillar the same height */
    gap:8px;
    width:100%;
    box-sizing:border-box;
}
.pillar {
    box-sizing:border-box;
    background:#e8f4fd;     /* very light blue */
    border:1px solid #bee3f8;
    border-radius:10px;
    padding:8px;
    display:flex;
    flex-direction:column;
    min-height:300px;
}
.pillar-header {
    font-size:0.72rem; font-weight:700; color:#172b4d;
    margin-bottom:6px; padding-bottom:4px;
    border-bottom:1px solid #bee3f8;
}
.pillar-count { font-weight:400; color:#5e86a1; font-size:0.65rem; }
.pillar-cards { display:flex; flex-direction:column; gap:5px; flex:1; }

/* ── Jira cards ──────────────────────────────────────────── */
.jira-card {
    background:#fff; border:1px solid #dde8f0;
    border-radius:7px; padding:6px 8px;
}
.jira-card.card-current { border:2px solid #0052cc; background:#f0f7ff; }
.card-key   { font-size:0.65rem; font-weight:700; color:#0052cc; line-height:1.1; }
.card-current .card-key { color:#003d99; }
.card-summary { font-size:0.68rem; font-weight:600; color:#172b4d; margin-top:2px; line-height:1.2; }
.card-meta    { font-size:0.6rem;  color:#5e6c84; margin-top:2px; }
.empty-pillar { font-size:0.65rem; color:#9db8ca; padding-top:4px; }

/* ── Current issue panel ─────────────────────────────────── */
.current-panel {
    background:#fff; border:2px solid #0052cc;
    border-radius:12px; padding:1rem 1.3rem; margin-top:0.2rem;
}
.cur-top {
    display:flex; justify-content:space-between;
    align-items:flex-start; gap:1rem; margin-bottom:0.55rem;
}
.cur-title { display:flex; align-items:baseline; gap:0.6rem; flex-wrap:wrap; }
.cur-key {
    font-size:1.1rem; font-weight:800; color:#0052cc; white-space:nowrap;
}
.cur-summary { font-size:1.1rem; font-weight:700; color:#172b4d; }
.cur-meta-row { display:flex; flex-wrap:wrap; gap:0.5rem; margin-bottom:0.6rem; }
.meta-chip {
    font-size:0.83rem; background:#f4f5f7;
    border:1px solid #dfe1e6; border-radius:5px;
    padding:0.18rem 0.55rem; color:#42526e;
}
.meta-chip strong { color:#172b4d; }

/* ── Status badge (replaces lock badge) ──────────────────── */
.status-badge {
    font-size:0.8rem; font-weight:700;
    border-radius:999px; padding:0.22rem 0.75rem;
    white-space:nowrap; border:1px solid rgba(0,0,0,0.08);
}

/* ── Description / narration blocks ─────────────────────── */
.cur-label {
    font-size:0.72rem; font-weight:700; color:#42526e;
    text-transform:uppercase; letter-spacing:0.05em;
    margin:0.55rem 0 0.18rem 0;
}
.cur-body {
    font-size:0.9rem; color:#172b4d; line-height:1.55;
    background:#f7f8f9; border-radius:7px; padding:0.5rem 0.75rem;
}
.current-empty { font-size:0.9rem; color:#6b778c; padding:0.4rem 0; }
</style>
""", unsafe_allow_html=True)

st.html("<div class='board-title'>Agility Standup Board</div>")

instance_id = st.query_params.get("instance_id", "")
if not instance_id:
    st.warning("Missing instance_id in URL. Use ?instance_id=YOUR_ID")
    st.stop()

try:
    latest_state = get_state_logged(instance_id, REFRESH_SECONDS)
    state, snapshot_locked = choose_display_state(latest_state)
except Exception as e:
    st.error(f"Failed to load standup state: {e}")
    st.stop()

issues          = state.get("issues", []) or []
current_issue   = state.get("current_issue") or {}
current_issue_key = current_issue.get("key")
standup_status  = state.get("status", "unknown")
spoken_text     = state.get("spoken_text", "")
project_key     = state.get("project_key", "")

log.info("RENDER | project=%s issues=%d bot_state=%s current=%s",
         project_key, len(issues), standup_status, current_issue_key or "—")

m1, m2, m3 = st.columns(3)
m1.metric("Project",   project_key or "-")
m2.metric("Issues",    len(issues))
m3.metric("Bot State", standup_status)   # renamed from "State"

render_board(issues, current_issue_key)
render_bottom_current_issue(current_issue, spoken_text, standup_status)

time.sleep(REFRESH_SECONDS)
st.rerun()