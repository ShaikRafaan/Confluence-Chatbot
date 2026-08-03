import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from uuid import uuid4

from server.redis_client import get_redis_client


JOB_TTL_SECONDS = 6 * 60 * 60
JOB_KEY = "ingest_job:{job_id}"
USER_RUNNING_JOB_KEY = "ingest:user:{user_id}:running_job"
_memory_jobs: Dict[str, Dict[str, Any]] = {}
_memory_running_jobs: Dict[str, str] = {}


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _redis():
    return get_redis_client()


def _job_key(job_id: str) -> str:
    return JOB_KEY.format(job_id=job_id)


def create_ingest_job(user_id: str) -> Dict[str, Any]:
    job_id = str(uuid4())
    now = _now()
    job = {
        "job_id": job_id,
        "user_id": user_id,
        "status": "running",
        "stage": "queued",
        "total_items": 6,
        "processed_items": 0,
        "started_at": now,
        "updated_at": now,
        "errors": [],
    }

    redis = _redis()
    if redis:
        redis.set(_job_key(job_id), json.dumps(job), ex=JOB_TTL_SECONDS)
        redis.set(USER_RUNNING_JOB_KEY.format(user_id=user_id), job_id, ex=JOB_TTL_SECONDS)
    else:
        _memory_jobs[job_id] = job
        _memory_running_jobs[user_id] = job_id
    return job


def get_running_job(user_id: str) -> Optional[Dict[str, Any]]:
    redis = _redis()
    if redis:
        job_id = redis.get(USER_RUNNING_JOB_KEY.format(user_id=user_id))
        if isinstance(job_id, bytes):
            job_id = job_id.decode("utf-8")
        return get_ingest_job(job_id) if job_id else None

    job_id = _memory_running_jobs.get(user_id)
    return get_ingest_job(job_id) if job_id else None


def clear_user_running_job(user_id: str):
    """Force clear any running job lock for a user."""
    redis = _redis()
    if redis:
        user_key = USER_RUNNING_JOB_KEY.format(user_id=user_id)
        job_id = redis.get(user_key)
        if job_id:
            if isinstance(job_id, bytes):
                job_id = job_id.decode("utf-8")
            raw = redis.get(_job_key(job_id))
            if raw:
                job = json.loads(raw)
                job["status"] = "failed"
                job["stage"] = "cancelled"
                redis.set(_job_key(job_id), json.dumps(job), ex=JOB_TTL_SECONDS)
            redis.delete(user_key)
    _memory_running_jobs.pop(user_id, None)


def get_ingest_job(job_id: str) -> Optional[Dict[str, Any]]:
    if not job_id:
        return None

    redis = _redis()
    if redis:
        raw = redis.get(_job_key(job_id))
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        job = json.loads(raw)
    else:
        job = _memory_jobs.get(job_id)

    if job and job.get("status") == "running":
        # Check if the job has been inactive for more than 3 minutes
        updated_at_str = job.get("updated_at") or job.get("started_at")
        try:
            updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00")).replace(tzinfo=None)
            if datetime.utcnow() - updated_at > timedelta(minutes=3):
                job["status"] = "failed"
                job["stage"] = "stale"
                job.setdefault("errors", []).append({
                    "message": "Ingestion timed out or became inactive.",
                    "timestamp": _now(),
                })
                job["updated_at"] = _now()
                redis = _redis()
                if redis:
                    redis.set(_job_key(job_id), json.dumps(job), ex=JOB_TTL_SECONDS)
                    redis.delete(USER_RUNNING_JOB_KEY.format(user_id=job["user_id"]))
                else:
                    _memory_jobs[job_id] = job
                    _memory_running_jobs.pop(job["user_id"], None)
        except Exception:
            pass

    return job


def update_ingest_job(
    job_id: str,
    *,
    stage: Optional[str] = None,
    status: Optional[str] = None,
    total_items: Optional[int] = None,
    processed_items: Optional[int] = None,
    increment: int = 0,
    error: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    job = get_ingest_job(job_id)
    if not job:
        return None

    if stage is not None:
        job["stage"] = stage
    if status is not None:
        job["status"] = status
    if total_items is not None:
        job["total_items"] = total_items
    if processed_items is not None:
        job["processed_items"] = processed_items
    if increment:
        job["processed_items"] = job.get("processed_items", 0) + increment
    if error:
        job.setdefault("errors", []).append({"message": error, "timestamp": _now()})

    total = job.get("total_items")
    if isinstance(total, int) and total > 0:
        job["processed_items"] = min(job.get("processed_items", 0), total)

    job["updated_at"] = _now()

    redis = _redis()
    if redis:
        redis.set(_job_key(job_id), json.dumps(job), ex=JOB_TTL_SECONDS)
        if job.get("status") != "running":
            redis.delete(USER_RUNNING_JOB_KEY.format(user_id=job["user_id"]))
    else:
        _memory_jobs[job_id] = job
        if job.get("status") != "running":
            _memory_running_jobs.pop(job["user_id"], None)

    return job
