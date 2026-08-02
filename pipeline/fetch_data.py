#Imports

import json
import os
import time
import sys
from typing import Any,Dict,List,Optional
import requests
from urllib.parse import urlparse,urljoin
from requests.utils import requote_uri
from requests.auth import HTTPBasicAuth
import csv
import io
from io import StringIO
from PyPDF2 import PdfReader
from docx import Document
import html
from dotenv import load_dotenv
load_dotenv()

#Config Variables
#User inputs
CONFLUENCE_URL=""
CONFLUENCE_TOKEN=os.getenv("CONFLUENCE_API_TOKEN")
CONFLUENCE_USER=""

FILTER_LABEL="con"
FILTER_TITLE=None

OUTPUT_DIR="."

EXPAND= "version,body.storage,metadata.labels,history,ancestors,children.page"

#Helper Functions
def api(base_url: str , path:str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


#Set up session via auth
def build_session(user: str , token: str) -> requests.Session:
    session=requests.Session()
    session.auth=HTTPBasicAuth(user,token)
    session.headers.update({"Accept":"application/json"})
    return session

#Handles time out error and retrieves get requests
def get(session: requests.Session , url: str, params: Dict = None, retries: int=3) -> Any:
    for attempt in range(1, retries+1):
        resp = session.get(url, params= params, timeout= 30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After",10))
            print(f"Rate-limited, wait: {wait}", file=sys.stderr)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Failed after {retries} retries: {url}")

#Paginate using atlassians link and cursor to retrieve all data
def paginate(session: requests.Session, url: str, param: Dict= None,
              key: str= "results") -> List[Any]:
    
    parsed = urlparse(url)
    base=f"{parsed.scheme}://{parsed.netloc}"
    params= dict(param or {})
    params.setdefault("limit",50)
    collected: List[Any] = []

    next_url: Optional[str] = url
    next_params: Optional[dict] = params

    while next_url:
        data = get(session, next_url, next_params)
        results = data.get(key,[])
        collected.extend(results)

        if not results and not data.get("_links",{}).get("next"):
            break

        next_path = data.get("_links",{}).get("next")

        if next_path:
            next_url = base + next_path
            next_params = None
        else:
            break

    return collected


#Fetch pages filtered based off of page title or page label
# def fetch_filtered(session: requests.Session ,confluence_url: str, label: str = None, title: str = None):
#     print(f"label='{label}' title='{title}'", file=sys.stderr)

#     if title == "":
#         title = None
    
#     if label == "":
#         label = None

#     if label and title:
#         params={
#             "cql": f'type = "page" AND labels = "{label}" AND title ~ "{title}"',
#             "expand":EXPAND
#         }
#     elif label:
#         params={
#             "cql":f'type="page" AND labels = "con"',
#             "expand":EXPAND
#         }
#     elif title:
#         params={
#             "cql":f'type="page" AND title ~ "{title}"',
#             "expand":EXPAND
#         }
#     else:
#         params={
#             "cql":'type="page"',
#             "expand":EXPAND
#         }

#     return paginate(session,api(confluence_url,"/rest/api/content/search"),params)

def _escape_cql_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def fetch_filtered(session, confluence_url, label=None, title=None):
    label = (label or "").strip() or None
    title = (title or "").strip() or None

    escaped_title = _escape_cql_literal(title) if title else None
    escaped_label = _escape_cql_literal(label) if label else None

    if escaped_title and escaped_label:
        params = {
            "cql": f'type="page" AND title ~ "{escaped_title}" AND label = "{escaped_label}"',
            "expand": EXPAND
        }
    elif escaped_title:
        params = {
            "cql": f'type="page" AND title ~ "{escaped_title}"',
            "expand": EXPAND
        }
    elif escaped_label:
        params = {
            "cql": f'type="page" AND label = "{escaped_label}"',
            "expand": EXPAND
        }
    else:
        params = {
            "cql": 'type="page"',
            "expand": EXPAND
        }

    pages = paginate(session, api(confluence_url, "/rest/api/content/search"), params)

    if label and not title:
        pages = [
            p for p in pages
            if label in [
                l["name"]
                for l in p.get("metadata", {}).get("labels", {}).get("results", [])
            ]
        ]

    return pages
    
#Fetch all pages when title or label is not provided
def fetch_all(session: requests.Session, confluence_url: str) -> List[Dict]:
    params={
        "type":"page",
        "expand":EXPAND
    }
    return paginate(session,api(confluence_url,"/rest/api/content"),params)

#Fetches children of a given page

def fetch_children_recursive(session: requests.Session,confluence_url: str, page_id: str, depth: int = 0,
                             max_depth: int=10) -> List[Dict]:
    
    if depth >= max_depth:
        return []
    params = {"expand":"version,body.storage,metadata.labels,history,ancestors"}
    children= paginate(session, api(confluence_url,f"/rest/api/content/{page_id}/child/page"),params)
    all_children = list(children)
    for child in children:
        all_children.extend(fetch_children_recursive(session,confluence_url,child["id"],depth+1,max_depth))
    return all_children

#Fetches attachments on a page

def fetch_attachments(session: requests.Session,confluence_url: str, page_id:str) -> List[Dict]:
    return paginate(session,api(confluence_url,f"/rest/api/content/{page_id}/child/attachment"),{"expand": "version,metadata"})

#Fetches comments on a page

def fetch_comments(session: requests.Session,confluence_url: str ,page_id: str) -> List[Dict]:
    return paginate(session, api(confluence_url, f"/rest/api/content/{page_id}/child/comment"),{"expand":"body.storage,version,history"})

#Download an attachment to parse through it
def download_attachment(session: requests.Session, confluence_url: str, page_id:str,attachment_id:str,
                        max_bytes: int = 25_000_000):
    download_endpoint=api(confluence_url,f"/rest/api/content/{page_id}/child/attachment/{attachment_id}/download")
    try:
        with session.get(download_endpoint, timeout=60, stream=True) as resp:
            
            print("ATTACHMENT DOWNLOAD STATUS:", resp.status_code)
            print("ATTACHMENT DOWNLOAD URL:", resp.url)
            print("CONTENT TYPE:", resp.headers.get("Content-Type"))

            resp.raise_for_status()

            content_length = int(resp.headers.get("Content-Length", 0))

            if content_length and content_length > max_bytes:
                print(
                    f"Skipping large attachment {attachment_id}: "
                    f"{content_length} bytes"
                )
                return None
            
            chunks=[]
            downloaded=0

            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue

                downloaded+=len(chunk)

                if downloaded > max_bytes:
                    print(f"Skipping attachment {attachment_id}: "f"exceeded max size of {max_bytes} bytes")

                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except Exception as e:
        print(f"Error downloading attachment {attachment_id}:{e}")
        return None

#Parse through the endpoint to extract content
def parse_attachment(attachment,content_bytes):
    if content_bytes is None:
        return None
    
    media_type= (attachment.get("media_type") or "").lower()

    try:
        if "csv" in media_type:
            text=content_bytes.decode("utf-8",errors="ignore")
            reader = csv.reader(StringIO(text))

            rows=[]
            for row in reader:
                rows.append(", ".join(row))
            return "\n".join(rows)
        elif "text" in media_type or "html" in media_type:
            return content_bytes.decode("utf-8",errors="ignore")
        elif "json" in media_type:
            return json.dumps(json.loads(content_bytes.decode("utf-8",errors="ignore")),indent=2)
        elif "pdf" in media_type:
            pdf_stream= io.BytesIO(content_bytes)
            reader = PdfReader(pdf_stream)

            if len(reader.pages) > 100:
                print(f"Skipping large PDF:{attachment.get('title')}")
                return None

            pages_text=[]
            for page in reader.pages:
                text=page.extract_text()
                if text:
                    pages_text.append(text)
            return "\n".join(pages_text)
        
        elif "word" in media_type or "docx" in media_type:
            doc_stream= io.BytesIO(content_bytes)
            doc = Document(doc_stream)
            paragraphs=[p.text for p in doc.paragraphs if p.text]
            return "\n".join(paragraphs)
        else:
            return None
    except Exception as e:
        print(f"Error processing attachment id  {attachment.get('id')} attachment title {attachment.get('title')} : {e}")
        return None
    
#Main attachment function combines the other two functions to return attachment content
def process_attachment(session: requests.Session, confluence_url: str,page_id: str, raw_attachment:Dict):

    att=attachment_format(raw_attachment)
    att_id = att.get("id")
    if not att_id:
        return att
    
    content_bytes=download_attachment(session,confluence_url,page_id,att_id)
    parsed_content= parse_attachment(att,content_bytes)

    if not parsed_content:
        return None

    att["content"] = parsed_content

    return att


def construct_page_url(raw: Dict, confluence_url: str = "") -> str:
    links = raw.get("_links", {})
    webui = links.get("webui", "")
    base_link = links.get("base", "")
    if not webui:
        return ""
    if webui.startswith("http://") or webui.startswith("https://"):
        return webui
    if base_link:
        return f"{base_link.rstrip('/')}/{webui.lstrip('/')}"
    if confluence_url:
        return f"{confluence_url.rstrip('/')}/{webui.lstrip('/')}"
    return webui

#Output format

#Formats Output for pages
def page_format(raw: Dict, confluence_url: str = "") -> Dict:
    version = raw.get("version",{})
    history = raw.get("history",{})
    labels = raw.get("labels") if "labels" in raw and isinstance(raw["labels"], list) else [
        l["name"] for l in raw.get("metadata",{}).get("labels",{}).get("results",[])
    ]
    ancestors = raw.get("ancestors",[])
    formatted_ancestors = [
        {"id": a["id"], "title": a["title"]} for a in ancestors if isinstance(a, dict) and "id" in a and "title" in a
    ]

    body_content = raw.get("body_storage") or raw.get("body",{}).get("storage",{}).get("value","")

    return{
        "id": raw["id"],
        "title": raw["title"],
        "status": raw.get("status"),
        "created_at": history.get("createdDate") or raw.get("created_at"),
        "created_by": history.get("createdBy",{}).get("displayName") or raw.get("created_by"),
        "updated_at": version.get("when") or raw.get("updated_at"),
        "updated_by": version.get("by",{}).get("displayName") or raw.get("updated_by"),
        "version_number": version.get("number") or raw.get("version_number"),
        "labels": labels,
        "ancestors": formatted_ancestors,
        "url": construct_page_url(raw, confluence_url) if "_links" in raw else raw.get("url",""),
        "body_storage": body_content,
        "attachments": raw.get("attachments", []),
        "comments": raw.get("comments", []),
    }

#Formats output for attachments

def attachment_format(raw:Dict) -> Dict:
    version = raw.get("version",{})
    return{
        "id": raw["id"],
        "title": raw["title"],
        "media_type": raw.get("metadata",{}).get("mediaType"),
        "created_at": version.get("when"),
        "created_by": version.get("by",{}).get("displayName"),
        "download_url": raw.get("_links",{}).get("download",""),
    }
#Formats outputs for comments
def comment_format(raw:Dict) -> Dict:
    version = raw.get("version",{})
    history = raw.get("history",{})
    return{
        "id": raw["id"],
        "created_at": history.get("createdDate"),
        "created_by": history.get("createdBy",{}).get("displayName"),
        "updated_at":version.get("when"),
        "body": raw.get("body",{}).get("storage",{}).get("value",""),

    }

#To be implemented

def load_last_sync():
    return None

def save_last_sync():
    return None

def fetch_data(api_key: str, user: str, confluence_url: Optional[str] = None, label: Optional[str] = None,
               title: Optional[str] = None) -> Dict:
    confluence_url = confluence_url or CONFLUENCE_URL

    session = build_session(user, api_key)

    if not label and not title:
        pages_raw = fetch_all(session, confluence_url)
    else:
        pages_raw = fetch_filtered(session, confluence_url, label, title)

    print(f"Pages found: {len(pages_raw)}")

    all_pages_raw = list(pages_raw)
    seen_ids = {p["id"] for p in pages_raw}

    for page in list(pages_raw):
        children = fetch_children_recursive(session, confluence_url, page["id"])
        for child in children:
            if child["id"] not in seen_ids:
                all_pages_raw.append(child)
                seen_ids.add(child["id"])

    print(f"Total pages including children: {len(all_pages_raw)}")

    pages_out = []

    for raw in all_pages_raw:
        page = page_format(raw, confluence_url)
        raw_attachments = fetch_attachments(session, confluence_url, page["id"])
        processed_attachments = []

        for raw_attachment in raw_attachments:
            att = process_attachment(session, confluence_url, page["id"], raw_attachment)
            if att:
                processed_attachments.append(att)

        page["attachments"] = processed_attachments
        page["comments"] = [comment_format(c) for c in fetch_comments(session, confluence_url, page["id"])]
        pages_out.append(page)

    output = {
        "filter": {"label": label, "title": title},
        "total_pages": len(pages_out),
        "pages": pages_out,
    }

    print("Fetch complete")
    return output


    


#Main (for testing purposes)
def main(confluence_url,user,token, label=None, title = None):
    session = build_session(user,token)

    last_sync=load_last_sync()

    if label is None and title is None:

        print("No filters specified fetching all data")
        pages_raw=fetch_all(session,confluence_url)
    else:
        print(f"Filters specified title:{title} and label:{label}")
        pages_raw = fetch_filtered(session,confluence_url,label,title)
    
    print(f"found {len(pages_raw)} pages")

    print("fetching child pages")
    all_pages_raw = list(pages_raw)
    seen_ids = {p["id"] for p in pages_raw}
    for page in list(pages_raw):
        for child in fetch_children_recursive(session,confluence_url,page["id"]):
            if child["id"] not in seen_ids:
                all_pages_raw.append(child)
                seen_ids.add(child["id"])
    print(f"Total pages with children pages: {len(all_pages_raw)}")

    pages_out=[]
    for i , raw in enumerate(all_pages_raw, 1):
        page=page_format(raw)
        raw_attachments= fetch_attachments(session,confluence_url,page["id"])
        processed_attachments=[]

        for raw_attachment in raw_attachments:
            att=process_attachment(session,confluence_url,page["id"],raw_attachment)
            if att:
              processed_attachments.append(att)

        page["attachments"]= processed_attachments
        page["comments"] = [comment_format(c) for c in fetch_comments(session,confluence_url,page["id"])]
        pages_out.append(page)

    output={
        "filter": {"label":label, "title":title},
        "total_pages": len(pages_out),
        "pages": pages_out,
    }

    slug = (label or title or user).replace(" ","_").lower()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f"{slug}.json")
    with open(out_path,"w",encoding="utf-8") as fh:
        json.dump(output,fh, ensure_ascii=False, indent=2)
    
    print(f"Output path:{out_path}")

if __name__ == "__main__":
    main(CONFLUENCE_URL,CONFLUENCE_USER,CONFLUENCE_TOKEN,FILTER_LABEL,FILTER_TITLE)


