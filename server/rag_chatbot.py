import chromadb
from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

if not NVIDIA_API_KEY:
    raise RuntimeError("NVIDIA_API_KEY is not set")

EMBED_MODEL="nvidia/nv-embedqa-e5-v5"
LLM_MODEL="meta/llama-3.1-70b-instruct"

client_nvidia=OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_API_KEY
)

client = chromadb.PersistentClient(path="./chroma_db")

collection=client.get_or_create_collection(name="confluence_rag")

def embed_query(query: str):
    response=client_nvidia.embeddings.create(
        model=EMBED_MODEL,
        input=[query],
        encoding_format="float",
        extra_body={"truncate": "END", "input_type": "query"}
    )
    return response.data[0].embedding

def retrieve_chunk(query: str, top_k: int =3):
    query_embedding=embed_query(query)

    results=collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k
    )

    documents = results["documents"][0]
    metadatas= results["metadatas"][0]

    return documents,metadatas

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

def generate_answer(prompt: str):
    response=client_nvidia.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role":"system","content":"You answer based only on provided context."},
            {"role":"user","content":prompt}
        ], temperature=0.2
    )
    return response.choices[0].message.content

def rag_pipeline(query: str):
    print(f"Query: {query}")

    documents,metadatas=retrieve_chunk(query)

    print("Retrieved Context")
    for i, doc in enumerate(documents):
        print(f"\n-- Chunk {i+1}")
        print(doc[:200],"...")

    prompt=build_prompt(query,documents)

    answer=generate_answer(prompt)

    print(f"Answer: \n\n {answer}")

if __name__ == "__main__":
    rag_pipeline("Which endpoint should I use to retrieve the contents for a user query?")
