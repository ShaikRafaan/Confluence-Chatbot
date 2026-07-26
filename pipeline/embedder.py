import json
import re
from openai import OpenAI
from typing import List, Dict
import os
import random
import time
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("NVIDIA_API_KEY")
BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL_NAME = "nvidia/nv-embedqa-e5-v5"
MAX_RETRIES = 4
MAX_TOKENS = 8192


def _sanitize_text(text: str) -> str:
    """Strip control characters and zero-width Unicode that can cause NVIDIA API 500s."""
    # Remove zero-width chars and other invisible Unicode
    text = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\ufeff\u00ad]', '', text)
    # Remove ASCII control characters (keep newlines and tabs)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text.strip()

if not API_KEY:
    raise RuntimeError("NVIDIA_API_KEY is not set")

client = None


def _get_nvidia_client():
    global client
    if client is None:
        client = OpenAI(
            base_url=BASE_URL,
            api_key=API_KEY
        )
    return client


def _request_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Send text to NVIDIA BGE-M3 embedding API with retries for transient 5xx/429s.
    """
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            clean_texts = [_sanitize_text(t) for t in texts]
            # Filter out any texts that became empty after sanitization
            clean_texts = [t if t else " " for t in clean_texts]
            response = _get_nvidia_client().embeddings.create(
                model=MODEL_NAME,
                input=clean_texts,
                encoding_format="float",
                extra_body={"truncate": "END", "input_type": "passage"}
            )
            embeddings = [item.embedding for item in response.data]

            if len(embeddings) != len(texts):
                raise RuntimeError(
                    f"Embedding response count mismatch: expected {len(texts)}, got {len(embeddings)}"
                )

            return embeddings
        except Exception as exc:
            last_error = exc
            if attempt == MAX_RETRIES:
                break

            wait_seconds = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.5)
            print(
                f"Embedding request failed on attempt {attempt}/{MAX_RETRIES}; "
                f"retrying in {wait_seconds:.1f}s ({exc})"
            )
            time.sleep(wait_seconds)

    raise last_error


def embed_batch(texts: List[str]) -> List[List[float]]:
    return _request_embeddings(texts)


def _vector_from_embedding(chunk: Dict, embedding: List[float]) -> Dict:
    return {
        "id": chunk["chunk_id"],
        "values": embedding,
        "metadata": {
            **chunk["metadata"],
            "text": chunk["text"]
        }
    }


def embed_data(chunks: List[Dict], batch_size: int = 4):
    
    vectors = []
    failures = []
    chunks = [chunk for chunk in chunks if chunk.get("text", "").strip()]

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        texts = [chunk["text"] for chunk in batch]

        print(f"Processing batch {i // batch_size + 1}...")

        try:
            embeddings = embed_batch(texts)
            for j, chunk in enumerate(batch):
                vectors.append(_vector_from_embedding(chunk, embeddings[j]))
            
        except Exception as e:
            print(f"Batch failed, retrying individually... ({e})")

            # fallback to single requests
            for chunk in batch:
                try:
                    embedding = embed_batch([chunk["text"]])[0]
                    vectors.append(_vector_from_embedding(chunk, embedding))
                except Exception as inner_e:
                    failures.append((chunk["chunk_id"], str(inner_e)))
                    print(f"Failed chunk {chunk['chunk_id']}: {inner_e}")

    if failures:
        print(f"Embedding failures: {len(failures)} of {len(chunks)} chunks")
        sample = ", ".join(chunk_id for chunk_id, _ in failures[:5])
        print(f"Failed chunk sample: {sample}")

    print("Embedding complete")

    if vectors:
        print("Vector dimension:", len(vectors[0]["values"]))
        print("Total vectors created:", len(vectors))

    if not vectors and chunks:
        raise RuntimeError(
            f"All embeddings failed. First error: {failures[0][1] if failures else 'unknown'}"
        )

    return vectors


def embed_chunks(input_path: str, batch_size: int = 4):
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    chunks = data["chunks"]
    print(f"Total chunks: {len(chunks)}")

    return embed_data(chunks, batch_size=batch_size)


if __name__ == "__main__":
    vectors = embed_chunks("chunks.json")

    # Save output
    with open("vectors.json", "w", encoding="utf-8") as f:
        json.dump(vectors, f, indent=2)

    print(" Saved to vectors.json")