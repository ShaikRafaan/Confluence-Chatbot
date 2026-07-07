import chromadb
from openai import OpenAI
from dotenv import load_dotenv
import os
load_dotenv()

# ✅ NVIDIA API config
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

client_nvidia = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_API_KEY
)

MODEL_NAME = "nvidia/nv-embedqa-e5-v5"

# ✅ Chroma init
client = chromadb.PersistentClient(path="./chroma_db")
collection = client.get_or_create_collection(name="confluence_rag")


def embed_query(query: str):
    response = client_nvidia.embeddings.create(
        model=MODEL_NAME,
        input=[query],
        encoding_format="float",
        extra_body={"truncate": "END", "input_type": "query"}
    )
    return response.data[0].embedding


def search(query: str, top_k: int = 3):
    print(f"\n🔍 Query: {query}")

    query_embedding = embed_query(query)

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k
    )

    for i in range(len(results["ids"][0])):
        print("\n---")
        print(f"Result {i+1}")
        print("Document:", results["documents"][0][i][:200])
        print("Metadata:", results["metadatas"][0][i])


if __name__ == "__main__":
    search("What is the architecture of the platform?")