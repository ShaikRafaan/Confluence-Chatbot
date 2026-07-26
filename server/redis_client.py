import os
import logging
from upstash_redis import Redis

logger = logging.getLogger(__name__)

_redis_client = None


def get_redis_client():
    """Get or initialize Redis client using Upstash credentials."""
    global _redis_client
    
    if _redis_client is not None:
        return _redis_client
    
    redis_url = os.getenv("UPSTASH_REDIS_URL")
    redis_token = os.getenv("UPSTASH_REDIS_TOKEN")
    
    if not redis_url or not redis_token:
        logger.warning(
            "UPSTASH_REDIS_URL or UPSTASH_REDIS_TOKEN not set. "
            "Redis will be unavailable."
        )
        return None
    
    try:
        _redis_client = Redis(url=redis_url, token=redis_token)
        # Test connection
        _redis_client.ping()
        logger.info("Redis connection established successfully")
        return _redis_client
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        return None


def is_redis_available():
    """Check if Redis client is available."""
    client = get_redis_client()
    return client is not None
