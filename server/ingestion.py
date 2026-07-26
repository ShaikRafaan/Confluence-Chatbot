# Import pipeline components
from pipeline.fetch_data import fetch_data
from pipeline.validate import validate_data
from pipeline.cleaner import clean_data
from pipeline.chunker import chunk_data
from pipeline.embedder import embed_data
from pipeline.chroma_upsert import upsert_data
from server.config import embedding_collection_suffix


def run_pipeline(
    user_id: str,
    api_key: str,
    user_email: str,
    confluence_url: str,
    label: str = None,
    title: str = None
):

    try:
        print(f"Starting ingestion for user: {user_id}")
        print("Fetching data...")

        raw_data = fetch_data(
            api_key=api_key,
            user=user_email,
            confluence_url=confluence_url,
            label=label,
            title=title
        )

        if not raw_data:
            raise Exception("No data fetched")

        print("Validating data...")

        validated_data = validate_data(raw_data)

        print("Cleaning data...")

        clean_docs = clean_data(validated_data)

        if not clean_docs:
            raise Exception("Cleaning returned empty data")

        print(f"Clean documents: {len(clean_docs)}")

        print("Chunking documents...")

        chunks = chunk_data(
            clean_documents=clean_docs,
            user_id=user_id
        )

        if not chunks:
            raise Exception("No chunks produced")

        print(f"Total chunks: {len(chunks)}")
        print("Generating embeddings...")

        vectors = embed_data(chunks)

        if not vectors:
            raise Exception("Embedding failed")

        print(f"Total embeddings: {len(vectors)}")


        collection_name = f"user_{user_id}_{embedding_collection_suffix()}"
        print(f"Storing in collection: {collection_name}")

        result = upsert_data(
            vectors=vectors,
            collection_name=collection_name
        )

        print(f"Stored {result['vector_count']} vectors")
        print(f"✅ Ingestion completed for user: {user_id}")

    except Exception as e:
        print(f"\n❌ Pipeline failed for user {user_id}: {e}")