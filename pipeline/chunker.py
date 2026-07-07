import json
import sys
import hashlib
from typing import Dict, List,Optional
from langchain_text_splitters import RecursiveCharacterTextSplitter

DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 100


def build_text_splitter( chunk_size: int = DEFAULT_CHUNK_SIZE, chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
                           ) -> RecursiveCharacterTextSplitter:


    return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=[
                "\n\n",
                "\n",
                ". ",
                " ",
                ""
            ],
            length_function=len,
            is_separator_regex=False
        )

def make_chunk_id( page_id: str,chunk_index: int,chunk_text: str) -> str:
    text_hash = hashlib.sha256(
        chunk_text.encode("utf-8")
    ).hexdigest()[:12]

    return f"page_{page_id}_chunk_{chunk_index}_{text_hash}"

def chunk_document(document: Dict, splitter: RecursiveCharacterTextSplitter, user_id: str="local_test_user",
                   connection_id: str = "local_connection") -> List:
    page_id = document.get("page_id")
    title = document.get("title","")
    url = document.get("url","")
    combined_text= document.get("combined_text","")

    if not page_id:
        print("Skipping document with missing page_id")
        return []
    if not combined_text or not combined_text.strip():
        print(f"Skipping page {page_id}: no combined_text found")
        return []
    raw_chunks = splitter.split_text(combined_text)

    chunk_objects=[]

    for index, chunk_text in enumerate(raw_chunks):
        chunk_text = chunk_text.strip()

        if not chunk_text:
            continue

        chunk_id = make_chunk_id(
            page_id=page_id,
            chunk_index=index,
            chunk_text=chunk_text
        )

        chunk_objects.append(
            {
                "chunk_id": chunk_id,
                "text": chunk_text,
                "metadata":{
                    "user_id": user_id,
                    "connection_id": connection_id,
                    "page_id": page_id,
                    "page_title": title,
                    "url":url,
                    "chunk_index": index,
                    "source_type": "confluence_page"
                }
            }
        )
    return chunk_objects

def chunk_data(clean_documents: List[Dict],user_id: str,connection_id: str = "default_connection",
               chunk_size: int = DEFAULT_CHUNK_SIZE, chunk_overlap: int=DEFAULT_CHUNK_OVERLAP) -> List[Dict]:
    splitter=build_text_splitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap
    )

    all_chunks = []

    for document in clean_documents:
        chunks = chunk_document(
            document=document,
            splitter=splitter,
            user_id=user_id,
            connection_id = connection_id
        ) 
        if chunks:
            all_chunks.extend(chunks)
        else:
            print(f"No chunks returned for page: {document.get('page_id')}")
    
    print("Chunking complete")

    return all_chunks
    
               

    
def chunk_clean_documents(input_path: str, output_path: str, user_id: str = "local_test_user",
                         connection_id: str= "local_connection",chunk_size: int = DEFAULT_CHUNK_SIZE , 
                         chunk_overlap: int = DEFAULT_CHUNK_OVERLAP):
    with open(input_path,"r", encoding="utf-8") as file:
        data=json.load(file)
    documents= data.get("documents",[])
    
    splitter=build_text_splitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap
    )

    all_chunks = []

    for document in documents:
        chunks = chunk_document(
            document=document,
            splitter=splitter,
            user_id=user_id,
            connection_id = connection_id
        ) 
        if chunks:
            all_chunks.extend(chunks)
        else:
            print(f"No chunks returned for page: {document.get('page_id')}")
    
    
    output = {
            "source_file": input_path,
            "chunking_strategy": "RecursiveCharacterTextSplitter",
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "total_documents": len(documents),
            "total_chunks": len(all_chunks),
            "chunks": all_chunks
        }

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)

    print("Chunking complete")
    print(f"Documents processed: {len(documents)}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python chunker.py <clean_documents.json> <chunks.json>")
        sys.exit(1)

    input_json = sys.argv[1]
    output_json = sys.argv[2]

    chunk_clean_documents(
        input_path=input_json,
        output_path=output_json
    )







