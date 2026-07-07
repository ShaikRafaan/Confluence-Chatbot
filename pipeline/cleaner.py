import json
import sys
from pipeline.validate import ConfluenceExport
from bs4 import BeautifulSoup
from typing import List,Dict
import html
import re

def clean_html(html_text: str) -> str:
    if not html_text:
        return ""
    
    decode = html.unescape(html_text)

    soup = BeautifulSoup(decode,"html.parser")

    for tag in soup.find_all(["ac:placeholder"]):
        tag.decompose()
    
    text=soup.get_text(separator=" ",strip=True)

    text=re.sub(r"\s"," ",text).strip()

    return text

def build_clean_doc(page: dict) -> dict:
    page_id = page.get("id")
    title = page.get("title")
    url= page.get("url")

    clean_body = clean_html(page.get("body_storage"," "))

    attachment_parts = []

    for attachment in page.get("attachments",[]):
        attachment_title = attachment.get("title"," ")
        attachment_content = attachment.get("content")

        if attachment_content:
            attachment_parts.append(
                f"Attachment: {attachment_title} \n {attachment_content}" 
            )
    
    comment_parts=[]
    for comment in page.get("comments",[]):
        clean_comment= clean_html(comment.get("body",""))

        if clean_comment:
            comment_parts.append(clean_comment)
    
    combined_parts = []

    if title:
        combined_parts.append(f"Title: {title}")
    
    if clean_body:
        combined_parts.append(f"Page Body:\n {clean_body}")
    
    if attachment_parts:
        combined_parts.append("Attachments: \n"+"\n\n".join(attachment_parts))
    
    if comment_parts:
        combined_parts.append("Comments: \n"+"\n\n".join(comment_parts))
    
    combined_text="\n\n".join(combined_parts)

    return{
        "page_id":page_id,
        "title":title,
        "url": url,
        "clean_body":clean_body,
        "attachment_text":"\n\n".join(attachment_parts),
        "comment_text":"\n\n".join(comment_parts),
        "combined_text": combined_text,
        "source_metadata":{
            "status": page.get("status"),
            "labels": page.get("labels"),
            "updated_at": page.get("updated-at"),
            "version_number":page.get("version_number")
        }
    }

def clean_data(raw_data: Dict) -> List[Dict]:
    validate = ConfluenceExport.model_validate(raw_data)
    pages = validate.model_dump(mode="json")["pages"]

    clean_documents=[]

    for page in pages:
        clean_doc = build_clean_doc(page)
        clean_documents.append(clean_doc)
    
    print(f"Cleaned {len(clean_documents)} documents")

    return clean_documents




def clean_export(input_path: str , output_path:str):
    with open(input_path,"r",encoding="utf-8") as file:
        data=json.load(file)
    
    validated = ConfluenceExport.model_validate(data)

    clean_documents=[]

    for page in validated.model_dump(mode="json")["pages"]:
        clean_doc=build_clean_doc(page)
        clean_documents.append(clean_doc)

        output = {
            "filter":data.get("filter",{}),
            "total_documents": len(clean_documents),
            "documents": clean_documents,

        }

        with open(output_path,"w",encoding="utf-8") as file:
            json.dump(output,file,ensure_ascii=False,indent=2)
        
        print(f"Clean export written to:{output_path}")
    
if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python clean_export.py <input.json> <output.json>")
        sys.exit(1)
    
    clean_export(sys.argv[1],sys.argv[2])
    


