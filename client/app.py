import os
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


# ---------------------------------------------------------------------------
# Sidebar — ingestion
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Ingestion Settings")

    user_id = st.text_input("User ID", value=st.session_state.last_user_id)
    st.session_state.last_user_id = user_id

    refresh_sessions(user_id)

    session_options = ["New session"] + list(st.session_state.available_sessions.get(user_id, []))
    selected_session = st.selectbox(
        "Conversation session",
        options=session_options,
        index=0 if st.session_state.active_session_id is None else session_options.index(st.session_state.active_session_id) if st.session_state.active_session_id in session_options else 0,
    )

    if selected_session != "New session":
        st.session_state.active_session_id = selected_session
    else:
        st.session_state.active_session_id = None

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

    ingestion_in_progress = (
        st.session_state.ingestion_status.get(user_id, {}).get("state") == "running"
    )

    ingest_clicked = st.button(
        "⏳ Ingesting..." if ingestion_in_progress else "🚀 Start Ingestion",
        use_container_width=True,
        disabled=ingestion_in_progress,
    )

    status_placeholder = st.empty()

    if ingest_clicked:
        st.session_state.ingestion_status[user_id] = {"state": "running"}
        st.rerun()

    if ingestion_in_progress:
        payload = {
            "user_id": user_id,
            "api_key": api_key,
            "user_email": user_email,
            "confluence_url": confluence_url,
            "label": label or None,
            "title": title or None,
        }
        with status_placeholder.container():
            with st.status("Ingesting Confluence content...", expanded=True) as status:
                st.write("📡 Connecting to Confluence...")
                try:
                    st.write("📥 Fetching and indexing pages... this can take a while.")
                    response = requests.post(
                        f"{BACKEND_URL}/ingest", json=payload, timeout=120
                    )
                    if response.ok:
                        status.update(
                            label="Ingestion started",
                            state="complete",
                            expanded=False,
                        )
                        st.session_state.ingestion_status[user_id] = {
                            "state": "success",
                            "message": "Ingestion started. Check backend logs for completion.",
                            "ts": datetime.now().strftime("%H:%M:%S"),
                        }
                    else:
                        status.update(
                            label="Ingestion failed", state="error", expanded=True
                        )
                        st.error(response.text)
                        st.session_state.ingestion_status[user_id] = {
                            "state": "error",
                            "message": f"Ingestion failed: {response.text}",
                            "ts": datetime.now().strftime("%H:%M:%S"),
                        }
                except requests.RequestException as exc:
                    status.update(
                        label="Could not reach backend", state="error", expanded=True
                    )
                    st.session_state.ingestion_status[user_id] = {
                        "state": "error",
                        "message": f"Could not reach backend: {exc}",
                        "ts": datetime.now().strftime("%H:%M:%S"),
                    }
        st.rerun()
    else:
        last_status = st.session_state.ingestion_status.get(user_id)
        if last_status:
            ts = last_status.get("ts", "")
            if last_status["state"] == "success":
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
    with chat_container:
        with st.chat_message("assistant"):
            with st.spinner("Searching knowledge base..."):
                try:
                    response = requests.post(f"{BACKEND_URL}/chat", json=payload, timeout=120)
                    if response.ok:
                        payload_response = response.json()
                        answer = payload_response.get("answer", "")
                        session_id = payload_response.get("session_id")
                        if session_id:
                            st.session_state.active_session_id = session_id
                            existing_sessions = st.session_state.available_sessions.setdefault(user_id, [])
                            if session_id not in existing_sessions:
                                existing_sessions.append(session_id)
                    else:
                        answer = f"⚠️ Request failed: {response.text}"
                except requests.RequestException as exc:
                    answer = f"⚠️ Could not reach backend: {exc}"
            st.write(answer)
            ts = datetime.now().strftime("%H:%M:%S")
            st.caption(ts)

    history.append({"role": "assistant", "content": answer, "ts": ts})
