import os
import random
import time
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "nvidia/nv-embedqa-e5-v5")
LLM_MODEL = os.getenv("LLM_MODEL", "meta/llama-3.1-8b-instruct")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
MAX_EMBED_RETRIES = 4

_client_nvidia = None


def embedding_collection_suffix() -> str:
    return "nvidia_e5_v5"


def get_nvidia_client():
    global _client_nvidia
    if not NVIDIA_API_KEY:
        raise RuntimeError("NVIDIA_API_KEY is not set")

    if _client_nvidia is None:
        _client_nvidia = OpenAI(
            base_url=NVIDIA_BASE_URL,
            api_key=NVIDIA_API_KEY,
        )
    return _client_nvidia


def embed_with_retries(query: str, *, input_type: str = "query", max_retries: int = MAX_EMBED_RETRIES):
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = get_nvidia_client().embeddings.create(
                model=EMBED_MODEL,
                input=[query],
                encoding_format="float",
                extra_body={"truncate": "END", "input_type": input_type},
            )
            return response.data[0].embedding
        except Exception as exc:
            last_error = exc
            if attempt == max_retries:
                break

            wait_seconds = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.5)
            print(
                f"Embedding request failed on attempt {attempt}/{max_retries}; "
                f"retrying in {wait_seconds:.1f}s ({exc})"
            )
            time.sleep(wait_seconds)

    raise last_error
