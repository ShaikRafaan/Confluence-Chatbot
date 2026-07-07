from datetime import datetime
from typing import List,Optional, Dict
from pydantic import BaseModel,Field,ConfigDict,field_validator,model_validator, ValidationError
import sys
import json


class Filter(BaseModel):
    model_config=ConfigDict(extra="forbid")

    label: Optional[str] = None
    title: Optional[str] = None

class Ancestor(BaseModel):
    model_config=ConfigDict(extra="forbid")

    id:str
    title:str

class Attachment(BaseModel):
    model_config=ConfigDict(extra="forbid")

    id:str
    title:str
    media_type:Optional[str] = None
    created_at: Optional[datetime] = None
    created_by:Optional[str] = None
    download_url:Optional[str] = ""
    content: Optional[str] = None

class Comment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    created_at:Optional[datetime] = None
    created_by: Optional[str] = None
    updated_at:Optional[datetime] = None
    body: Optional[str] = ""

class Page(BaseModel):
    model_config=ConfigDict(extra="allow")

    id: str
    title: str
    status: Optional[str] = None
    created_at: Optional[datetime] = None
    created_by:Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    version_number: Optional[int] = None
    labels: List[str] = Field(default_factory=list)
    ancestors:List[Ancestor] = Field(default_factory=list)
    url: Optional[str] = ""
    body_storage: Optional[str] = ""
    attachments: List[Attachment] = Field(default_factory=list)
    comments: List[Comment] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def page_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Page ID cannot be empty")
        return value


    @field_validator("title")
    @classmethod
    def title_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Page title cannot be empty")
        return value

class ConfluenceExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    filter: Filter
    total_pages: int
    pages: List[Page]

    
    @model_validator(mode="after")
    def validate_total_pages(self):
        if self.total_pages != len(self.pages):
            raise ValueError(
                f"total_pages={self.total_pages}, but actual pages count={len(self.pages)}"
            )
        return self

def validate_data(raw_data: Dict) -> Dict:
    try:
        validated = ConfluenceExport.model_validate(raw_data)

        print(f"Validated ({validated.total_pages} pages)")

        return validated.model_dump(mode="json")

    except ValidationError as e:
        print(f"Validation failed because of: {e} ")
        raise Exception("Schema validation failed")


def validate_json_file(file_path: str) -> ConfluenceExport:
    with open(file_path, "r", encoding="utf-8") as file:
        data = json.load(file)

    validated_data = ConfluenceExport.model_validate(data)
    return validated_data


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_json.py <json_file_path>")
        sys.exit(1)

    file_path = sys.argv[1]

    try:
        validated = validate_json_file(file_path)

        print("JSON validation passed")
        print(f"Total pages: {validated.total_pages}")

    except json.JSONDecodeError as e:
        print("Invalid JSON syntax")
        print(e)
        sys.exit(1)

    except ValidationError as e:
        print("JSON schema validation failed")
        print(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
    








