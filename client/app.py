import os
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
# Session state
# ---------------------------------------------------------------------------
if "chat_histories" not in st.session_state:
    st.session_state.chat_histories = {}  # user_id -> list[dict(role, content, ts)]

if "last_user_id" not in st.session_state:
    st.session_state.last_user_id = "demo-user"

if "active_session_id" not in st.session_state:
    st.session_state.active_session_id = None

if "available_sessions" not in st.session_state:
    st.session_state.available_sessions = {}


def get_history(user_id: str):
    return st.session_state.chat_histories.setdefault(user_id, [])


def refresh_sessions(user_id: str):
    try:
        response = requests.get(f"{BACKEND_URL}/sessions/{user_id}", timeout=10)
        if response.ok:
            sessions = response.json()
            st.session_state.available_sessions[user_id] = [item.get("session_id") for item in sessions if item.get("session_id")]
        else:
            st.session_state.available_sessions[user_id] = []
    except requests.RequestException:
        st.session_state.available_sessions[user_id] = []


def render_sources(sources):
    if not sources:
        return

    st.markdown("**Sources:**")
    for src in sources:
        title = src.get("title", "Confluence Page")
        url = src.get("url", "")
        if url:
            st.markdown(f"- <a href='{url}' target='_blank'>{title}</a>", unsafe_allow_html=True)
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
            elif event_type == "token":
                yield event.get("text", "")
            elif event_type == "metadata":
                metadata["session_id"] = event.get("session_id")
                metadata["sources"] = event.get("sources", [])
            elif event_type == "error":
                metadata["session_id"] = event.get("session_id")
                raise RuntimeError(event.get("message", "Streaming response failed"))


# ---------------------------------------------------------------------------
# Sidebar — ingestion
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Ingestion Settings")

    user_id = st.text_input("User ID", value=st.session_state.last_user_id)
    st.session_state.last_user_id = user_id

    refresh_sessions(user_id)

    stored_sessions = list(st.session_state.available_sessions.get(user_id, []))
    if st.session_state.active_session_id and st.session_state.active_session_id not in stored_sessions:
        stored_sessions.insert(0, st.session_state.active_session_id)

    session_options = ["New session"] + stored_sessions
    selected_session = st.selectbox(
        "Conversation session",
        options=session_options,
        index=0 if st.session_state.active_session_id is None else session_options.index(st.session_state.active_session_id) if st.session_state.active_session_id in session_options else 0,
    )

    if selected_session != "New session":
        if st.session_state.active_session_id != selected_session:
            st.session_state.active_session_id = selected_session
            try:
                resp = requests.get(f"{BACKEND_URL}/sessions/{user_id}/{selected_session}", timeout=10)
                if resp.ok:
                    data = resp.json()
                    msgs = data.get("messages", [])
                    st.session_state.chat_histories[user_id] = [
                        {
                            "role": m.get("role"),
                            "content": m.get("content"),
                            "sources": m.get("sources", []),
                            "ts": m.get("timestamp", "")
                        } for m in msgs
                    ]
            except requests.RequestException:
                pass
    else:
        if st.session_state.active_session_id is not None:
            st.session_state.active_session_id = None
            st.session_state.chat_histories[user_id] = []

    with st.expander("Confluence credentials", expanded=True):
        api_key = st.text_input("API Token", type="password")
        user_email = st.text_input("Email")
        confluence_url = st.text_input(
            "Confluence URL", value="https://your-instance.atlassian.net/wiki"
        )

    with st.expander("Scope filters (optional)"):
        label = st.text_input("Label")
        title = st.text_input("Title")

    if "ingestion_status" not in st.session_state:
        st.session_state.ingestion_status = {}  # user_id -> dict(state, message, ts)

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
        }
        try:
            response = requests.post(f"{BACKEND_URL}/ingest", json=payload, timeout=15)
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
                response = requests.get(f"{BACKEND_URL}/ingest/status/{job_id}", timeout=10)
                response.raise_for_status()
                job = response.json()
                total = job.get("total_items") or 1
                processed = job.get("processed_items") or 0
                progress = min(processed / total, 1.0)
                stage = str(job.get("stage", "running")).replace("_", " ").title()

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
                        time.sleep(1.5)
                        st.rerun()
            except requests.RequestException as exc:
                status_placeholder.error(f"Could not read ingestion status: {exc}")
        elif last_status["state"] == "success":
            status_placeholder.success(f"{last_status['message']} ({ts})")
        elif last_status["state"] == "error":
            status_placeholder.error(f"{last_status['message']} ({ts})")

    st.divider()
    history = get_history(user_id)
    st.caption(f"💬 {len(history)} messages in this user's history")
    if st.button("🗑️ Clear chat history", use_container_width=True, disabled=not history):
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
            render_sources(msg.get("sources"))
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

    payload = {"user_id": user_id, "query": query, "session_id": st.session_state.active_session_id}
    sources = []
    with chat_container:
        with st.chat_message("assistant"):
            metadata = {"sources": [], "session_id": None, "status": "Searching knowledge base..."}
            status_placeholder = st.empty()
            status_placeholder.caption(metadata["status"])
            try:
                answer = st.write_stream(stream_chat_response(payload, metadata, status_placeholder))
                status_placeholder.empty()
                sources = metadata.get("sources", [])
                session_id = metadata.get("session_id")
                if session_id:
                    st.session_state.active_session_id = session_id
                    existing_sessions = st.session_state.available_sessions.setdefault(user_id, [])
                    if session_id not in existing_sessions:
                        existing_sessions.append(session_id)
            except (requests.RequestException, RuntimeError, json.JSONDecodeError) as exc:
                status_placeholder.empty()
                answer = f"⚠️ Could not stream response: {exc}"
                st.write(answer)

            render_sources(sources)
            ts = datetime.now().strftime("%H:%M:%S")
            st.caption(ts)

    history.append({"role": "assistant", "content": answer, "sources": sources, "ts": ts})
