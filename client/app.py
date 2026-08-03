import os
import html
import json
import time
from datetime import datetime

import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")

st.set_page_config(
    page_title="Confluence RAG",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
        .block-container {
            padding-top: 2rem;
            max-width: 900px;
        }
        h1 {
            font-weight: 700;
        }
        .app-subtitle {
            color: #6b7280;
            margin-top: -0.6rem;
            margin-bottom: 1.5rem;
            font-size: 0.95rem;
        }
        .status-pill {
            display: inline-block;
            padding: 0.15rem 0.6rem;
            border-radius: 999px;
            font-size: 0.75rem;
            font-weight: 600;
            background: #ecfdf5;
            color: #047857;
            border: 1px solid #a7f3d0;
        }
        div[data-testid="stChatMessage"] {
            border-radius: 12px;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📚 Confluence RAG Assistant")
st.markdown(
    '<div class="app-subtitle">Ask questions grounded in your ingested Confluence space.</div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------
if "chat_histories" not in st.session_state:
    st.session_state.chat_histories = {}

if "last_user_id" not in st.session_state:
    st.session_state.last_user_id = "demo-user"

if "active_session_id" not in st.session_state:
    st.session_state.active_session_id = None

if "available_sessions" not in st.session_state:
    st.session_state.available_sessions = {}

if "ingestion_status" not in st.session_state:
    st.session_state.ingestion_status = {}


def get_history(user_id: str):
    return st.session_state.chat_histories.setdefault(user_id, [])


def refresh_sessions(user_id: str):
    """Fetch known sessions from backend and merge with locally-known IDs."""
    try:
        response = requests.get(f"{BACKEND_URL}/sessions/{user_id}", timeout=10)
        if response.ok:
            sessions = response.json()
            remote_ids = [
                item.get("session_id")
                for item in sessions
                if item.get("session_id")
            ]
        else:
            remote_ids = []
    except requests.RequestException:
        remote_ids = []

    # Merge: keep any locally-known active session even if not yet on the server
    existing = st.session_state.available_sessions.get(user_id, [])
    merged = list(dict.fromkeys(remote_ids + existing))  # dedupe, remote first
    st.session_state.available_sessions[user_id] = merged


def load_session_history(user_id: str, session_id: str):
    """Fetch full message history from backend for a session."""
    try:
        resp = requests.get(
            f"{BACKEND_URL}/sessions/{user_id}/{session_id}", timeout=10
        )
        if resp.ok:
            data = resp.json()
            msgs = data.get("messages", [])
            st.session_state.chat_histories[user_id] = [
                {
                    "role": m.get("role"),
                    "content": m.get("content"),
                    "sources": m.get("sources", []),
                    "ts": m.get("timestamp", ""),
                }
                for m in msgs
            ]
    except requests.RequestException:
        pass


def source_href(src, confluence_base_url=""):
    url = (src.get("url") or "").strip()
    base_url = (confluence_base_url or "").strip().rstrip("/")

    if url.startswith("http://") or url.startswith("https://"):
        return url

    if url and base_url:
        return f"{base_url}/{url.lstrip('/')}"

    page_id = str(src.get("page_id") or "").strip()
    if page_id and base_url:
        return f"{base_url}/pages/{page_id}"

    return url


def render_sources(sources, confluence_base_url=""):
    if not sources:
        return

    st.markdown("**Sources:**")
    for src in sources:
        title = html.escape(src.get("title", "Confluence Page"))
        url = html.escape(source_href(src, confluence_base_url), quote=True)
        if url:
            st.markdown(
                f'- <a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(f"- {title}")


def stream_chat_response(payload, metadata, status_slot=None):
    with requests.post(
        f"{BACKEND_URL}/chat/stream",
        json=payload,
        stream=True,
        timeout=(10, 120),
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue

            event = json.loads(line)
            event_type = event.get("type")

            if event_type == "status":
                metadata["status"] = event.get("message", "")
                if status_slot is not None:
                    status_slot.caption(metadata["status"])
            elif event_type == "session":
                metadata["session_id"] = event.get("session_id")
            elif event_type == "token":
                yield event.get("text", "")
            elif event_type == "metadata":
                metadata["session_id"] = event.get("session_id")
                metadata["sources"] = event.get("sources", [])
            elif event_type == "error":
                metadata["session_id"] = event.get("session_id")
                raise RuntimeError(
                    event.get("message", "Streaming response failed")
                )


# ---------------------------------------------------------------------------
# Sidebar — ingestion
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Ingestion Settings")

    user_id = st.text_input("User ID", value=st.session_state.last_user_id)
    
    # Refresh sessions only when user_id changes or on initial load
    if (
        st.session_state.last_user_id != user_id
        or "sessions_fetched_user_id" not in st.session_state
        or st.session_state.sessions_fetched_user_id != user_id
    ):
        st.session_state.last_user_id = user_id
        refresh_sessions(user_id)
        st.session_state.sessions_fetched_user_id = user_id

    # ---- session selector ----
    stored_sessions = list(
        st.session_state.available_sessions.get(user_id, [])
    )
    # Ensure the active session always appears in the dropdown
    if (
        st.session_state.active_session_id
        and st.session_state.active_session_id not in stored_sessions
    ):
        stored_sessions.insert(0, st.session_state.active_session_id)

    session_options = ["New session"] + stored_sessions

    # Compute default index from active_session_id
    if st.session_state.active_session_id in session_options:
        default_idx = session_options.index(st.session_state.active_session_id)
    else:
        default_idx = 0

    col_sel, col_ref = st.columns([4, 1])
    with col_sel:
        selected_session = st.selectbox(
            "Conversation session",
            options=session_options,
            index=default_idx,
            label_visibility="visible",
        )
    with col_ref:
        st.write("") # spacing alignment
        if st.button("🔄", help="Refresh available sessions from server"):
            refresh_sessions(user_id)
            st.rerun()

    # React to user *changing* the dropdown
    if selected_session == "New session":
        if st.session_state.active_session_id is not None:
            # User explicitly chose "New session"
            st.session_state.active_session_id = None
            st.session_state.chat_histories[user_id] = []
            st.rerun()
    else:
        if st.session_state.active_session_id != selected_session:
            # User switched to a different existing session
            st.session_state.active_session_id = selected_session
            load_session_history(user_id, selected_session)
            st.rerun()

    if st.button("➕ Start new session", use_container_width=True):
        st.session_state.active_session_id = None
        st.session_state.chat_histories[user_id] = []
        st.rerun()

    with st.expander("Confluence credentials", expanded=True):
        api_key = st.text_input("API Token", type="password")
        user_email = st.text_input("Email")
        confluence_url = st.text_input(
            "Confluence URL", value="https://your-instance.atlassian.net/wiki"
        )

    with st.expander("Scope filters (optional)"):
        label = st.text_input("Label")
        title = st.text_input("Title")

    current_ingestion = st.session_state.ingestion_status.get(user_id, {})
    ingestion_in_progress = current_ingestion.get("state") == "running"

    ingest_clicked = st.button(
        "⏳ Ingesting..." if ingestion_in_progress else "🚀 Start Ingestion",
        use_container_width=True,
        disabled=ingestion_in_progress,
    )

    status_placeholder = st.empty()

    if ingest_clicked:
        payload = {
            "user_id": user_id,
            "api_key": api_key,
            "user_email": user_email,
            "confluence_url": confluence_url,
            "label": label or None,
            "title": title or None,
            "force": True,
        }
        try:
            response = requests.post(
                f"{BACKEND_URL}/ingest", json=payload, timeout=15
            )
            if response.ok:
                data = response.json()
                st.session_state.ingestion_status[user_id] = {
                    "state": "running",
                    "job_id": data.get("job_id"),
                    "message": data.get("message", "Ingestion started"),
                    "ts": datetime.now().strftime("%H:%M:%S"),
                }
            else:
                st.session_state.ingestion_status[user_id] = {
                    "state": "error",
                    "message": f"Ingestion failed: {response.text}",
                    "ts": datetime.now().strftime("%H:%M:%S"),
                }
        except requests.RequestException as exc:
            st.session_state.ingestion_status[user_id] = {
                "state": "error",
                "message": f"Could not reach backend: {exc}",
                "ts": datetime.now().strftime("%H:%M:%S"),
            }
        st.rerun()

    last_status = st.session_state.ingestion_status.get(user_id)
    if last_status:
        ts = last_status.get("ts", "")
        job_id = last_status.get("job_id")
        if last_status["state"] == "running" and job_id:
            try:
                response = requests.get(
                    f"{BACKEND_URL}/ingest/status/{job_id}", timeout=30
                )
                response.raise_for_status()
                job = response.json()
                total = job.get("total_items") or 1
                processed = job.get("processed_items") or 0
                progress = min(processed / total, 1.0)
                stage = (
                    str(job.get("stage", "running")).replace("_", " ").title()
                )

                with status_placeholder.container():
                    st.progress(progress, text=f"{stage} - {processed}/{total}")
                    st.caption(f"Status: {job.get('status', 'running')}")

                    if job.get("status") == "complete":
                        st.success("Ingestion complete.")
                        st.session_state.ingestion_status[user_id] = {
                            "state": "success",
                            "message": "Ingestion complete.",
                            "ts": datetime.now().strftime("%H:%M:%S"),
                        }
                    elif job.get("status") == "failed":
                        errors = job.get("errors", [])
                        st.error("Ingestion failed.")
                        if errors:
                            with st.expander("View errors"):
                                st.write(errors)
                        st.session_state.ingestion_status[user_id] = {
                            "state": "error",
                            "message": "Ingestion failed.",
                            "ts": datetime.now().strftime("%H:%M:%S"),
                        }
                    else:
                        time.sleep(3.0)
                        st.rerun()
            except requests.RequestException as exc:
                with status_placeholder.container():
                    st.warning(f"Status check delayed (retrying...): {exc}")
                time.sleep(2.0)
                st.rerun()
        elif last_status["state"] == "success":
            status_placeholder.success(f"{last_status['message']} ({ts})")
        elif last_status["state"] == "error":
            status_placeholder.error(f"{last_status['message']} ({ts})")

    st.divider()
    history = get_history(user_id)
    st.caption(f"💬 {len(history)} messages in this user's history")
    if st.button(
        "🗑️ Clear chat history",
        use_container_width=True,
        disabled=not history,
    ):
        st.session_state.chat_histories[user_id] = []
        st.rerun()

# ---------------------------------------------------------------------------
# Main — chat
# ---------------------------------------------------------------------------
history = get_history(user_id)

chat_container = st.container()
with chat_container:
    if not history:
        st.info("No messages yet. Ask a question below to get started.")
    for msg in history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
            render_sources(msg.get("sources"), confluence_url)
            if msg.get("ts"):
                st.caption(msg["ts"])

query = st.chat_input("Ask a question about your Confluence space...")

if query:
    now = datetime.now().strftime("%H:%M:%S")
    history.append({"role": "user", "content": query, "ts": now})
    with chat_container:
        with st.chat_message("user"):
            st.write(query)
            st.caption(now)

    # Always pass the current active_session_id so the backend reuses it
    payload = {
        "user_id": user_id,
        "query": query,
        "session_id": st.session_state.active_session_id,
    }
    sources = []
    with chat_container:
        with st.chat_message("assistant"):
            metadata = {
                "sources": [],
                "session_id": None,
                "status": "Searching knowledge base...",
            }
            status_slot = st.empty()
            status_slot.caption(metadata["status"])
            try:
                answer = st.write_stream(
                    stream_chat_response(payload, metadata, status_slot)
                )
                status_slot.empty()
                sources = metadata.get("sources", [])
            except (
                requests.RequestException,
                RuntimeError,
                json.JSONDecodeError,
            ) as exc:
                status_slot.empty()
                answer = f"⚠️ Could not stream response: {exc}"
                st.write(answer)

            # Persist the session_id returned by the backend
            session_id = metadata.get("session_id")
            if session_id:
                st.session_state.active_session_id = session_id
                # Immediately register so the selector doesn't lose it on rerun
                existing = st.session_state.available_sessions.setdefault(
                    user_id, []
                )
                if session_id not in existing:
                    existing.insert(0, session_id)

            render_sources(sources, confluence_url)
            ts = datetime.now().strftime("%H:%M:%S")
            st.caption(ts)

    history.append(
        {"role": "assistant", "content": answer, "sources": sources, "ts": ts}
    )
