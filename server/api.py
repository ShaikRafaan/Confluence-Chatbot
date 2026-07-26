from pydantic import BaseModel
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import chromadb
from openai import OpenAI
from dotenv import load_dotenv
import os
import json
import random
import time
from uuid import uuid4
from urllib.parse import urlparse
from typing import Optional, List
from server.ingestion import run_pipeline
from server.redis_service import (
    create_new_session,
    save_message,
    get_session_history,
    get_user_sessions,
    clear_session_history,
    delete_session,
    update_session_accessed
)
from server.models import SessionMetadata, ClearHistoryRequest
load_dotenv()
#Config
NVIDIA_API_KEY=os.getenv("NVIDIA_API_KEY")
EMBED_MODEL=os.getenv("EMBEDDING_MODEL")
LLM_MODEL=os.getenv("LLM_MODEL")
MAX_EMBED_RETRIES = 4

if not NVIDIA_API_KEY:
    raise RuntimeError("NVIDIA_API_KEY is not set")

client_nvidia = None


def get_nvidia_client():
    global client_nvidia
    if not NVIDIA_API_KEY:
        raise RuntimeError("NVIDIA_API_KEY is not set")

    if client_nvidia is None:
        client_nvidia=OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=NVIDIA_API_KEY
        )
    return client_nvidia


client_db = chromadb.PersistentClient(path="./chroma_db")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

#Models

class ChatRequest(BaseModel):
    user_id: str
    query: str
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    user_id: str
    session_id: str
    query: str
    answer: str

class IngestRequest(BaseModel):
    user_id: str
    api_key: str
    user_email: str
    confluence_url: str
    label: Optional[str] = None
    title: Optional[str] = None

#Helper Functions

def embedding_collection_suffix() -> str:
    return "nvidia_e5_v5"

def _first_non_latin1(value: str):
    for char in value:
        if ord(char) > 255:
            return char
    return None


def validate_ingest_request(req: IngestRequest):
    required_fields = {
        "user_id": req.user_id,
        "api_key": req.api_key,
        "user_email": req.user_email,
        "confluence_url": req.confluence_url,
    }

    for field, value in required_fields.items():
        if not value or not value.strip():
            raise HTTPException(status_code=400, detail=f"{field} is required")

    # requests.HTTPBasicAuth encodes username/password as latin-1.
    for field, value in {"user_email": req.user_email, "api_key": req.api_key}.items():
        invalid_char = _first_non_latin1(value)
        if invalid_char:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{field} contains a character that cannot be used in Confluence "
                    f"Basic Auth: U+{ord(invalid_char):04X}. Check that the email and API "
                    "token fields were not accidentally filled with page text or UI text."
                ),
            )

    parsed_url = urlparse(req.confluence_url.strip())
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise HTTPException(
            status_code=400,
            detail="confluence_url must look like https://your-instance.atlassian.net/wiki",
        )

def embed_query(query: str):
    last_error = None

    for attempt in range(1, MAX_EMBED_RETRIES + 1):
        try:
            response=get_nvidia_client().embeddings.create(
                model=EMBED_MODEL,
                input=[query],
                encoding_format="float",
                extra_body={"truncate": "END", "input_type": "query"}
            )
            return response.data[0].embedding
        except Exception as exc:
            last_error = exc
            if attempt == MAX_EMBED_RETRIES:
                break

            wait_seconds = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.5)
            print(
                f"Query embedding failed on attempt {attempt}/{MAX_EMBED_RETRIES}; "
                f"retrying in {wait_seconds:.1f}s ({exc})"
            )
            time.sleep(wait_seconds)

    raise last_error

def retrieve_chunks(user_id: str, query: str, top_k: int = 10):
    collection_name = f"user_{user_id}_{embedding_collection_suffix()}"
    collection = client_db.get_or_create_collection(collection_name)

    query_embedding = embed_query(query)

    results = collection.query(query_embeddings=[query_embedding], n_results=top_k)

    documents = results.get("documents", [[]])
    metadatas = results.get("metadatas", [[]])

    # Ensure the query result shape is always a list of lists.
    if not isinstance(documents, list) or not documents:
        documents = [[]]
    if not isinstance(metadatas, list) or not metadatas:
        metadatas = [[]]

    return documents[0] or [], metadatas[0] or []

def build_prompt(query: str, documents: list, history: list = None):
    context="\n\n".join(documents)
    
    # Build conversation history string if available
    history_str = ""
    if history and len(history) > 0:
        history_str = "\n\nPrevious conversation:\n"
        for msg in history[-3:]:  # Include last 3 messages for context
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")
            history_str += f"{role}: {content}\n"

    prompt=f"""
    You are an AI assistant for a Confluence.

    Answer ONLY using the context below.
    If the answer is not present, say "I don't know".

    Context:
    {context}
    {history_str}
    Current Question:
    {query}

    Answer:
    """.strip()

    return prompt
def generate_answer(prompt: str, history: list = None):
    messages = [{"role": "system", "content": "You are a helpful assistant. Answer based on the provided context and conversation history."}]
    
    # Add previous conversation messages to provide context
    if history and len(history) > 0:
        # Include last 3 messages for context (to avoid token limits)
        for msg in history[-3:]:
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            })
    
    # Add the current prompt
    messages.append({"role": "user", "content": prompt})
    
    try:
        response = get_nvidia_client().chat.completions.create(
            model=LLM_MODEL,
            messages=messages, 
            temperature=0.2
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"LLM Error: {e}")
        return str(e)

def rag_pipeline(user_id: str, query: str, history: list = None):
    print(f"Query: {query}")
    print(f"History length: {len(history) if history else 0}")

    try:
        documents, metadatas = retrieve_chunks(user_id, query)
    except Exception as exc:
        print(f"Error retrieving chunks: {exc}")
        return "Unable to retrieve context for this query. Please ingest data first."

    if not documents:
        return "No relevant data found. Please ingest data first."

    prompt = build_prompt(query, documents, history)
    print(f"Prompt: {prompt}")

    answer = generate_answer(prompt, history)

    print(f"Answer: \n\n {answer}")

    return answer


#API Endpoints

@app.get("/")
def root():
    return {"message":"Server is running"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Chat endpoint with Redis-backed history."""
    # Create or use existing session
    if req.session_id:
        session_id = req.session_id
        await update_session_accessed(session_id)
    else:
        session_id = await create_new_session(req.user_id)

    print(f"Session ID: {session_id}")
    
    # Retrieve chat history from Redis
    messages = await get_session_history(session_id)
    print(f"Retrieved {len(messages)} messages from Redis")
    
    # Convert to format expected by LLM
    history = [{"role": msg.role, "content": msg.content} for msg in messages]
    print(f"History for LLM: {len(history)} messages")

    # Run RAG pipeline with context
    answer = rag_pipeline(req.user_id, req.query, history)

    # Save both user query and assistant response to Redis
    print(f"Saving user message and response to session {session_id}")
    await save_message(session_id, req.user_id, "user", req.query)
    await save_message(session_id, req.user_id, "assistant", answer)

    return ChatResponse(
        user_id=req.user_id,
        session_id=session_id,
        query=req.query,
        answer=answer
    )


@app.get("/sessions/{user_id}", response_model=List[SessionMetadata])
async def get_sessions(user_id: str):
    """Get all sessions for a user."""
    sessions = await get_user_sessions(user_id)
    return sessions


@app.get("/sessions/{user_id}/{session_id}")
async def get_session(user_id: str, session_id: str):
    """Get history for a specific session."""
    messages = await get_session_history(session_id)
    return {
        "user_id": user_id,
        "session_id": session_id,
        "messages": [msg.model_dump() for msg in messages]
    }


@app.delete("/sessions/{user_id}/{session_id}")
async def delete_chat_session(user_id: str, session_id: str):
    """Delete a chat session."""
    success = await delete_session(user_id, session_id)
    if success:
        return {"message": f"Session {session_id} deleted"}
    else:
        raise HTTPException(status_code=400, detail="Failed to delete session")


@app.post("/sessions/{session_id}/clear")
async def clear_chat_history(session_id: str):
    """Clear all messages from a session."""
    success = await clear_session_history(session_id)
    if success:
        return {"message": f"Session {session_id} history cleared"}
    else:
        raise HTTPException(status_code=400, detail="Failed to clear history")

@app.post("/ingest")
def ingest(req: IngestRequest, background_tasks: BackgroundTasks):
    validate_ingest_request(req)
    
    background_tasks.add_task(
            run_pipeline,
            user_id=req.user_id.strip(),
            api_key=req.api_key.strip(),
            user_email=req.user_email.strip(),
            confluence_url=req.confluence_url.strip(),
            label=req.label.strip() if req.label else None,
            title=req.title.strip() if req.title else None
        )
    return{"message":"Ingestion Started","user_id":req.user_id}