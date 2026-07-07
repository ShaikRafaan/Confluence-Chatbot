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
from typing import Optional
from server.ingestion import run_pipeline
load_dotenv()
#Config
NVIDIA_API_KEY=os.getenv("NVIDIA_API_KEY")
EMBED_MODEL="nvidia/nv-embedqa-e5-v5"
LLM_MODEL="meta/llama-3.1-70b-instruct"
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

def retrieve_chunks(user_id: str, query: str, top_k: int = 3):
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

def build_prompt(query: str, documents: list):
    context="\n\n".join(documents)

    prompt=f"""

    You are an AI assistant for a Customer Support Intelligence Platform.

    Answer ONLY using the context below.
    If the answer is not present, say "I don't know".

    Context:
    {context}

    Question:
    {query}

    Answer:
    """.strip()

    return prompt
def generate_answer(prompt: str, history: list = None):
    messages=[{"role":"system","content":"You answer based only on provided context."}]
    if history:
        messages.extend(history)
    messages.append({"role":"user","content":prompt})
    try:
        response=get_nvidia_client().chat.completions.create(
            model=LLM_MODEL,
            messages=messages, temperature=0.2
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"LLM Error: {e}")
        return str(e)

def rag_pipeline(user_id: str, query: str, history: list = None):
    print(f"Query: {query}")

    try:
        documents, metadatas = retrieve_chunks(user_id, query)
    except Exception as exc:
        print(f"Error retrieving chunks: {exc}")
        return "Unable to retrieve context for this query. Please ingest data first."

    if not documents:
        return "No relevant data found. Please ingest data first."

    prompt=build_prompt(query,documents)

    answer=generate_answer(prompt, history)

    print(f"Answer: \n\n {answer}")

    return answer


#API Endpoints

@app.get("/")
def root():
    return {"message":"Server is running"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat")
def chat(req: ChatRequest):
    session_id = req.session_id
    if not session_id:
        session_id = str(uuid4())

    # Passing empty history since Redis is disabled for testing
    history = []

    answer = rag_pipeline(req.user_id, req.query, history)

    return{
        "user_id":req.user_id,
        "session_id": session_id,
        "query":req.query,
        "answer": answer
    }

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


