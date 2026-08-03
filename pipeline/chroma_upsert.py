import json
import chromadb
from typing import List, Dict

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path="./chroma_db")
    return _client


def upsert_data(vectors: List[Dict], collection_name: str):
    
    collection = _get_client().get_or_create_collection(name=collection_name)
    
    ids = []
    documents = []
    embeddings = []
    metadatas = []

    for vec in vectors:
        ids.append(vec["id"])
        documents.append(vec["metadata"]["text"])
        embeddings.append(vec["values"])

        # ✅ metadata WITHOUT text
        metadatas.append({
            k: v for k, v in vec["metadata"].items()
            if k != "text"
        })
    collection.upsert(ids=ids,documents=documents,embeddings=embeddings,metadatas=metadatas)
    
    return{"collection_name": collection_name,
           "vector_count":len(ids)}



# def load_vectors(file_path: str):
#     with open(file_path,"r",encoding="utf-8") as f:
#         vectors=json.load(f)
    
#     print(f"Length of vectors:{len(vectors)}")

#     ids=[]
#     documents=[]
#     embeddings=[]
#     metadatas=[]

#     for vec in vectors:
#         ids.append(vec["id"])
#         documents.append(vec["metadata"]["text"])
#         embeddings.append(vec["values"])
#         metadatas.append({
#             k: v for k, v in vec["metadata"].items() if k != "text"
#         })
    
#     collection.upsert(
#         ids=ids,
#         documents=documents,
#         embeddings=embeddings,
#         metadatas=metadatas
#     )
#     print("Data upserted into chroma")


# if __name__ == "__main__":
#     load_vectors("vectors.json")