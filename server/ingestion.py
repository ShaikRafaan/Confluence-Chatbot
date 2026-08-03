# Import pipeline components
from pipeline.fetch_data import fetch_data
from pipeline.validate import validate_data
from pipeline.cleaner import clean_data
from pipeline.chunker import chunk_data
from pipeline.embedder import embed_data
from pipeline.chroma_upsert import upsert_data
from server.config import embedding_collection_suffix
from server.ingest_jobs import update_ingest_job


def run_pipeline(
    user_id: str,
    api_key: str,
    user_email: str,
    confluence_url: str,
    label: str = None,
    title: str = None,
    job_id: str = None
):

    try:
        print(f"Starting ingestion for user: {user_id} (job_id: {job_id})")
        if job_id:
            update_ingest_job(job_id, stage="fetching_data", processed_items=1, total_items=6)

        print("Fetching data...")
        if job_id:
            update_ingest_job(
                job_id,
                stage="Fetching pages & attachments from Confluence...",
                processed_items=1,
                total_items=6
            )

        raw_data = fetch_data(
            api_key=api_key,
            user=user_email,
            confluence_url=confluence_url,
            label=label,
            title=title
        )

        if not raw_data:
            raise Exception("No data fetched from Confluence. Check credentials or scope filters.")

        total_pages = raw_data.get("total_pages", 0) if isinstance(raw_data, dict) else 0

        if job_id:
            update_ingest_job(
                job_id,
                stage=f"Validating {total_pages} fetched pages...",
                processed_items=2
            )

        print("Validating data...")
        validated_data = validate_data(raw_data)

        if job_id:
            update_ingest_job(
                job_id,
                stage="Cleaning HTML & extracting content...",
                processed_items=3
            )

        print("Cleaning data...")
        clean_docs = clean_data(validated_data)

        if not clean_docs:
            raise Exception("Cleaning returned empty data")

        print(f"Clean documents: {len(clean_docs)}")

        if job_id:
            update_ingest_job(
                job_id,
                stage=f"Chunking {len(clean_docs)} documents...",
                processed_items=4
            )

        print("Chunking documents...")
        chunks = chunk_data(
            clean_documents=clean_docs,
            user_id=user_id
        )

        if not chunks:
            raise Exception("No chunks produced")

        print(f"Total chunks: {len(chunks)}")

        if job_id:
            update_ingest_job(
                job_id,
                stage=f"Generating embeddings for {len(chunks)} chunks...",
                processed_items=5
            )

        print("Generating embeddings...")
        vectors = embed_data(chunks)

        if not vectors:
            raise Exception("Embedding failed")

        print(f"Total embeddings: {len(vectors)}")

        if job_id:
            update_ingest_job(
                job_id,
                stage=f"Storing {len(vectors)} vectors in database...",
                processed_items=6
            )

        collection_name = f"user_{user_id}_{embedding_collection_suffix()}"
        print(f"Storing in collection: {collection_name}")

        result = upsert_data(
            vectors=vectors,
            collection_name=collection_name
        )

        print(f"Stored {result['vector_count']} vectors")
        print(f"✅ Ingestion completed for user: {user_id}")

        if job_id:
            update_ingest_job(
                job_id,
                status="complete",
                stage="complete",
                processed_items=6,
                total_items=6
            )

    except Exception as e:
        error_msg = str(e)
        print(f"\n❌ Pipeline failed for user {user_id}: {error_msg}")
        if job_id:
            update_ingest_job(
                job_id,
                status="failed",
                stage="failed",
                error=error_msg
            )