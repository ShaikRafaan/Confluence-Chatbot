import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4
from server.redis_client import get_redis_client, is_redis_available
from server.models import Message, SessionMetadata

logger = logging.getLogger(__name__)

# Redis key patterns
SESSION_KEY = "chat:session:{session_id}"
USER_SESSIONS_KEY = "chat:user:{user_id}:sessions"
LATEST_SESSION_KEY = "chat:user:{user_id}:latest_session"
SESSION_METADATA_KEY = "chat:session:{session_id}:metadata"

# TTL in seconds
SESSION_TTL = 7 * 24 * 60 * 60  # 7 days


def _get_redis():
    """Get Redis client, return None if unavailable."""
    return get_redis_client()


def _serialize_message(msg: Message) -> str:
    """Serialize message to JSON."""
    return msg.model_dump_json()


def _deserialize_message(msg_json) -> Message:
    """Deserialize message from JSON."""
    # Handle both bytes and strings from Redis
    if isinstance(msg_json, bytes):
        msg_json = msg_json.decode('utf-8')
    return Message.model_validate_json(msg_json)


async def create_new_session(user_id: str) -> str:
    """Create a new chat session for a user."""
    if not is_redis_available():
        logger.warning("Redis unavailable, returning UUID-based session_id")
        return str(uuid4())
    
    try:
        redis = _get_redis()
        session_id = str(uuid4())
        now = datetime.utcnow().isoformat() + "Z"
        
        # Store session metadata
        metadata = {
            "session_id": session_id,
            "user_id": user_id,
            "message_count": 0,
            "created_at": now,
            "updated_at": now,
            "last_accessed_at": now
        }
        
        metadata_key = SESSION_METADATA_KEY.format(session_id=session_id)
        redis.set(metadata_key, json.dumps(metadata), ex=SESSION_TTL)
        
        # Add to user's session list
        user_sessions_key = USER_SESSIONS_KEY.format(user_id=user_id)
        redis.sadd(user_sessions_key, session_id)
        
        # Set as latest session
        latest_key = LATEST_SESSION_KEY.format(user_id=user_id)
        redis.set(latest_key, session_id)
        
        logger.info(f"Created new session {session_id} for user {user_id}")
        return session_id
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        return str(uuid4())


async def save_message(
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    sources: Optional[List[Dict[str, Any]]] = None
) -> bool:
    """Save a message to a session."""
    if not is_redis_available():
        logger.warning("Redis unavailable, message not saved")
        return False
    
    try:
        redis = _get_redis()
        now = datetime.utcnow().isoformat() + "Z"
        
        message = Message(role=role, content=content, timestamp=now, sources=sources or [])
        session_key = SESSION_KEY.format(session_id=session_id)
        
        # Add message to session list
        redis.rpush(session_key, _serialize_message(message))
        
        # Update session TTL
        redis.expire(session_key, SESSION_TTL)
        
        # Update metadata
        metadata_key = SESSION_METADATA_KEY.format(session_id=session_id)
        metadata_json = redis.get(metadata_key)
        
        if metadata_json:
            metadata = json.loads(metadata_json)
            metadata["message_count"] = redis.llen(session_key)
            metadata["updated_at"] = now
            metadata["last_accessed_at"] = now
            redis.set(metadata_key, json.dumps(metadata), ex=SESSION_TTL)
        
        logger.debug(f"Saved message to session {session_id}")
        return True
    except Exception as e:
        logger.error(f"Error saving message: {e}")
        return False


async def get_session_history(session_id: str) -> List[Message]:
    """Retrieve all messages from a session."""
    if not is_redis_available():
        logger.warning("Redis unavailable, returning empty history")
        return []
    
    try:
        redis = _get_redis()
        session_key = SESSION_KEY.format(session_id=session_id)
        
        messages_json = redis.lrange(session_key, 0, -1)
        logger.debug(f"Retrieved {len(messages_json)} raw items from Redis for session {session_id}")
        
        if not messages_json:
            logger.debug(f"No messages found in Redis key: {session_key}")
            return []
        
        messages = []
        for msg_json in messages_json:
            try:
                msg = _deserialize_message(msg_json)
                messages.append(msg)
            except Exception as e:
                logger.error(f"Error deserializing message: {e}, raw: {msg_json}")
                continue
        
        logger.debug(f"Successfully retrieved and deserialized {len(messages)} messages from session {session_id}")
        return messages
    except Exception as e:
        logger.error(f"Error retrieving session history: {e}")
        return []


async def get_user_sessions(user_id: str) -> List[SessionMetadata]:
    """Get all sessions for a user."""
    if not is_redis_available():
        logger.warning("Redis unavailable, returning empty sessions")
        return []
    
    try:
        redis = _get_redis()
        user_sessions_key = USER_SESSIONS_KEY.format(user_id=user_id)
        
        session_ids = redis.smembers(user_sessions_key)
        
        if not session_ids:
            return []
        
        sessions = []
        for session_id in session_ids:
            metadata_key = SESSION_METADATA_KEY.format(session_id=session_id)
            metadata_json = redis.get(metadata_key)
            
            if metadata_json:
                metadata_dict = json.loads(metadata_json)
                metadata = SessionMetadata(**metadata_dict)
                sessions.append(metadata)
        
        # Sort by updated_at descending (most recent first)
        sessions.sort(key=lambda x: x.updated_at, reverse=True)
        logger.debug(f"Retrieved {len(sessions)} sessions for user {user_id}")
        return sessions
    except Exception as e:
        logger.error(f"Error retrieving user sessions: {e}")
        return []


async def clear_session_history(session_id: str) -> bool:
    """Clear all messages from a session."""
    if not is_redis_available():
        logger.warning("Redis unavailable, cannot clear history")
        return False
    
    try:
        redis = _get_redis()
        session_key = SESSION_KEY.format(session_id=session_id)
        metadata_key = SESSION_METADATA_KEY.format(session_id=session_id)
        
        redis.delete(session_key)
        redis.delete(metadata_key)
        
        logger.info(f"Cleared history for session {session_id}")
        return True
    except Exception as e:
        logger.error(f"Error clearing session history: {e}")
        return False


async def delete_session(user_id: str, session_id: str) -> bool:
    """Delete a session entirely."""
    if not is_redis_available():
        logger.warning("Redis unavailable, cannot delete session")
        return False
    
    try:
        redis = _get_redis()
        session_key = SESSION_KEY.format(session_id=session_id)
        metadata_key = SESSION_METADATA_KEY.format(session_id=session_id)
        user_sessions_key = USER_SESSIONS_KEY.format(user_id=user_id)
        
        redis.delete(session_key)
        redis.delete(metadata_key)
        redis.srem(user_sessions_key, session_id)
        
        logger.info(f"Deleted session {session_id} for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
        return False


async def update_session_accessed(session_id: str) -> bool:
    """Update the last_accessed_at timestamp for a session."""
    if not is_redis_available():
        return False
    
    try:
        redis = _get_redis()
        metadata_key = SESSION_METADATA_KEY.format(session_id=session_id)
        metadata_json = redis.get(metadata_key)
        
        if metadata_json:
            metadata = json.loads(metadata_json)
            metadata["last_accessed_at"] = datetime.utcnow().isoformat() + "Z"
            redis.set(metadata_key, json.dumps(metadata), ex=SESSION_TTL)
            return True
        return False
    except Exception as e:
        logger.error(f"Error updating session access time: {e}")
        return False
