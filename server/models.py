from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from datetime import datetime


class Message(BaseModel):
    """Individual chat message."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: str  # ISO 8601 format
    sources: List[Dict[str, Any]] = Field(default_factory=list)


class ChatSession(BaseModel):
    """Chat session containing messages and metadata."""
    session_id: str
    user_id: str
    messages: List[Message]
    created_at: str
    updated_at: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "session_id": "550e8400-e29b-41d4-a716-446655440000",
                "user_id": "demo-user",
                "messages": [
                    {
                        "role": "user",
                        "content": "What is this?",
                        "timestamp": "2026-07-08T13:43:17Z"
                    },
                    {
                        "role": "assistant",
                        "content": "This is a response",
                        "timestamp": "2026-07-08T13:43:20Z"
                    }
                ],
                "created_at": "2026-07-08T13:43:17Z",
                "updated_at": "2026-07-08T13:43:20Z"
            }
        }


class SessionMetadata(BaseModel):
    """Metadata about a chat session."""
    session_id: str
    user_id: str
    message_count: int
    created_at: str
    updated_at: str
    last_accessed_at: str


class ChatHistory(BaseModel):
    """User's chat history with multiple sessions."""
    user_id: str
    sessions: List[SessionMetadata]


class ClearHistoryRequest(BaseModel):
    """Request to clear chat history."""
    session_id: Optional[str] = None
    user_id: Optional[str] = None
