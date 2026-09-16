#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import time
import uuid
import logging
import threading
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import requests

# ============================================================
# REDIS KEYS
# ============================================================


class RedisKeys:
    """Redis key names for client-side queue"""

    QUEUE = "cmms:client:queue"  # LIST: pending sync jobs
    INFLIGHT = "cmms:client:inflight"  # STRING: current job being processed
    LOCK = "cmms:client:lock"  # STRING: mutex (TTL-based)
    STATS_SUCCESS = "cmms:client:stats:success"  # INT: success count
    STATS_FAILED = "cmms:client:stats:failed"  # INT: failure count
    LAST_SYNC = "cmms:client:last_sync"  # STRING: timestamp


# ============================================================
# REDIS QUEUE SYNC (CLIENT-SIDE)
# ============================================================


class RedisQueueSync:
    """
    ✅ CLIENT-SIDE Redis queue for reliable sync

    Features:
    - One change at a time (ordered, reliable)
    - Mutex lock (no race conditions)
    - Crash recovery (inflight tracking)
    - Idempotency (sync_id on every event)
    - Exponential backoff retry
    """

    def __init__(
        self,
        redis_client,
        api_url: str,
        client_id: str,
        auth_token: str,
        logger: Optional[logging.Logger] = None,
        batch_size: int = 50,
        max_retry_attempts: int = 5,
    ):
        """
        Initialize Redis queue sync

        Args:
            redis_client: Redis connection
            api_url: Server API URL (e.g. "https://hq-server-dgs6.onrender.com/api/sync")
            client_id: Client identifier
            auth_token: Authentication token
            logger: Optional logger instance
            batch_size: Max events per batch (default: 50)
            max_retry_attempts: Max retry attempts (default: 5)
        """
        self.redis = redis_client
        self.api_url = api_url
        self.client_id = client_id
        self.auth_token = auth_token
        self.batch_size = batch_size
        self.max_retry_attempts = max_retry_attempts

        # Setup logger
        self.log = logger or logging.getLogger("redis_queue_sync")

        # Threading
        self.stop_event = threading.Event()
        self.worker_thread = None

        # Recover any crashed jobs on init
        self._recover_inflight()

        self.log.info("✅ Redis Queue Sync initialized")
        self.log.info(f"   Client ID: {client_id}")
        self.log.info(f"   Batch size: {batch_size}")
        self.log.info(f"   Max retries: {max_retry_attempts}")

    # ============================================================
    # 1️⃣ ENQUEUE (CALLED AFTER EVERY DB WRITE)
    # ============================================================

    def enqueue_change(
        self,
        entity: str,
        record_id: Any,
        action: str,
        data: Optional[Dict] = None,
        operation: str = None,
    ) -> str:
        """
        ✅ Queue a change for sync (INSTANT, NON-BLOCKING)

        Call this after EVERY local DB write:

        Examples:
            # After asset created/updated
            sync.enqueue_change("CalSoft_asset", 123, "u", asset_data)

            # After session created
            sync.enqueue_change("CalSoft_calibrationsession", 456, "u", session_data)

            # After soft delete
            sync.enqueue_change("CalSoft_asset", 123, "deactivate")

            # After hard delete
            sync.enqueue_change("CalSoft_asset", 123, "d")

        Args:
            entity: Table name (e.g. "CalSoft_asset")
            record_id: Primary key value
            action: Action type ("u", "d", "activate", "deactivate")
            data: Optional data payload
            operation: Optional operation override

        Returns:
            sync_id: Unique job identifier
        """
        sync_id = str(uuid.uuid4())

        # Build event in server format
        event = {
            "sync_id": sync_id,
            "table": entity,
            "record_id": str(record_id),
            "operation": operation or action,
            "data": data or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "client_id": self.client_id,
        }

        # Build job payload
        job = {
            "sync_id": sync_id,
            "events": [event],  # Single event per job (one at a time)
            "client_id": self.client_id,
            "attempt": 0,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }

        # Push to queue (RIGHT push = FIFO)
        self.redis.rpush(RedisKeys.QUEUE, json.dumps(job))

        self.log.debug(f"📝 Queued: {entity}:{record_id} (sync_id={sync_id[:8]}...)")

        return sync_id

    def enqueue_batch(self, events: List[Dict]) -> str:
        """
        ✅ Queue multiple events as a batch

        Used for bulk operations. Each event should have:
        - table: Table name
        - record_id: Primary key
        - operation: "u", "d", "activate", "deactivate"
        - data: Optional data dict

        Args:
            events: List of event dicts

        Returns:
            sync_id: Unique batch identifier
        """
        if not events:
            return None

        sync_id = str(uuid.uuid4())

        # Add sync_id and client_id to each event
        for event in events:
            if "sync_id" not in event:
                event["sync_id"] = sync_id
            if "client_id" not in event:
                event["client_id"] = self.client_id
            if "created_at" not in event:
                event["created_at"] = datetime.now(timezone.utc).isoformat()

        # Build job
        job = {
            "sync_id": sync_id,
            "events": events,
            "client_id": self.client_id,
            "attempt": 0,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }

        # Push to queue
        self.redis.rpush(RedisKeys.QUEUE, json.dumps(job))

        self.log.info(f"📝 Queued batch: {len(events)} events (sync_id={sync_id[:8]}...)")

        return sync_id

    # ============================================================
    # 2️⃣ SINGLE WORKER (ONE AT A TIME)
    # ============================================================

    def start_worker(self):
        """
        ✅ Start the sync worker thread

        This replaces the old upload_loop() method.
        Only one worker runs at a time (mutex enforced).
        """
        if self.worker_thread and self.worker_thread.is_alive():
            self.log.warning("Worker already running")
            return

        self.log.info("🚀 Starting Redis queue worker...")

        self.worker_thread = threading.Thread(
            target=self._worker_loop, name="redis-queue-worker", daemon=True
        )
        self.worker_thread.start()

        self.log.info("✅ Worker started")

    def _worker_loop(self):
        """
        ✅ Main worker loop (processes queue one job at a time)

        CRITICAL FEATURES:
        - Mutex lock (only one worker runs)
        - Inflight tracking (crash recovery)
        - Exponential backoff (on retry)
        - Idempotent uploads (sync_id)
        """
        self.log.info("=" * 80)
        self.log.info("⚡ REDIS QUEUE WORKER STARTED")
        self.log.info("=" * 80)

        while not self.stop_event.is_set():
            try:
                # ============================================================
                # MUTEX: Prevent parallel workers
                # ============================================================
                lock_acquired = self.redis.set(
                    RedisKeys.LOCK,
                    "1",
                    nx=True,  # Only set if not exists
                    ex=60,  # 60-second TTL (auto-release on crash)
                )

                if not lock_acquired:
                    # Another worker has the lock
                    time.sleep(1)
                    continue

                # ============================================================
                # FETCH NEXT JOB
                # ============================================================
                job_raw = self.redis.lpop(RedisKeys.QUEUE)

                if not job_raw:
                    # Queue empty
                    self.redis.delete(RedisKeys.LOCK)
                    time.sleep(1)
                    continue

                # ============================================================
                # MARK AS INFLIGHT (crash safety)
                # ============================================================
                self.redis.set(RedisKeys.INFLIGHT, job_raw)
                job = json.loads(job_raw)

                sync_id = job["sync_id"]
                events = job["events"]
                attempt = job.get("attempt", 0)

                self.log.info(
                    f"⚡ Processing: {len(events)} event(s) "
                    f"(sync_id={sync_id[:8]}..., attempt={attempt})"
                )

                # ============================================================
                # SEND TO SERVER
                # ============================================================
                success, response = self._send_to_server(job)

                if success:
                    # ✅ SUCCESS
                    self.redis.delete(RedisKeys.INFLIGHT)
                    self.redis.incr(RedisKeys.STATS_SUCCESS)
                    self.redis.set(RedisKeys.LAST_SYNC, datetime.now(timezone.utc).isoformat())

                    self.log.info(f"✅ Synced: {len(events)} event(s)")

                    # Log detailed results
                    if response:
                        if response.get("status") == "queued":
                            self.log.info(
                                f"   Server queued job: {response.get('job_id', 'N/A')[:8]}..."
                            )
                        elif response.get("deferred"):
                            self.log.warning(
                                f"   ⏸️  Server deferred {response['deferred']} events"
                            )
                else:
                    # ❌ FAILURE
                    self._handle_failure(job)

            except Exception as e:
                self.log.exception(f"💥 Worker error: {e}")

            finally:
                # Always release lock
                try:
                    self.redis.delete(RedisKeys.LOCK)
                except Exception:
                    pass

        self.log.info("Worker stopped")

    def _send_to_server(self, job: Dict) -> tuple[bool, Optional[Dict]]:
        """
        ✅ Send job to server with idempotency

        Returns:
            (success: bool, response: dict)
        """
        url = f"{self.api_url}/upload"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.auth_token}",
            "X-Client-ID": self.client_id,
        }

        # Build payload (server expects {"events": [...], "client_id": "..."})
        payload = {"events": job["events"], "client_id": self.client_id}

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)

            if response.status_code in (200, 202):
                # Success or queued
                response_json = response.json()
                sync_version = response_json.get("sync_version")
                if sync_version:
                    self.redis.set("cmms:sync_version", str(sync_version))
                return True, response_json

            elif response.status_code == 409:
                # Duplicate (idempotent) - treat as success
                self.log.warning(
                    f"⚠️  Duplicate detected (sync_id={job['sync_id'][:8]}...) - skipping"
                )
                return True, None

            else:
                self.log.error(f"❌ Server returned {response.status_code}: {response.text}")
                return False, None

        except requests.RequestException as e:
            self.log.error(f"❌ Network error: {e}")
            return False, None

    def _handle_failure(self, job: Dict):
        """
        ✅ Handle failed job with exponential backoff
        """
        job["attempt"] = job.get("attempt", 0) + 1

        self.redis.incr(RedisKeys.STATS_FAILED)

        if job["attempt"] >= self.max_retry_attempts:
            # Max retries exceeded - move to dead letter queue
            self.log.error(f"❌ Max retries exceeded for sync_id={job['sync_id'][:8]}...")
            self.log.error(f"   Moving to dead letter queue")

            # Store in dead letter queue for manual inspection
            self.redis.rpush("cmms:client:dlq", json.dumps(job))
            self.redis.delete(RedisKeys.INFLIGHT)
            return

        # Exponential backoff: 2^attempt seconds (max 60s)
        backoff_time = min(2 ** job["attempt"], 60)

        self.log.warning(
            f"⏸️  Retry in {backoff_time}s (attempt {job['attempt']}/{self.max_retry_attempts})"
        )

        # Put back in queue
        self.redis.rpush(RedisKeys.QUEUE, json.dumps(job))
        self.redis.delete(RedisKeys.INFLIGHT)

        # Sleep with backoff
        for _ in range(backoff_time):
            if self.stop_event.is_set():
                break
            time.sleep(1)

    # ============================================================
    # 3️⃣ CRASH RECOVERY
    # ============================================================

    def _recover_inflight(self):
        """
        ✅ Recover crashed job on startup

        If app crashed while processing a job, this recovers it.
        """
        job_raw = self.redis.get(RedisKeys.INFLIGHT)

        if job_raw:
            self.log.warning("🔄 CRASH RECOVERY: Found inflight job")
            self.log.warning(f"   Job: {job_raw[:100]}...")

            # Put back in queue (at front for priority)
            self.redis.lpush(RedisKeys.QUEUE, job_raw)
            self.redis.delete(RedisKeys.INFLIGHT)

            self.log.info("✅ Crashed job recovered and requeued")
        else:
            self.log.info("✅ No crashed jobs to recover")

    # ============================================================
    # 4️⃣ MONITORING
    # ============================================================

    def get_status(self) -> Dict:
        """
        Get current queue status

        Returns:
            dict with queue metrics
        """
        try:
            queue_len = self.redis.llen(RedisKeys.QUEUE)
            has_inflight = self.redis.exists(RedisKeys.INFLIGHT)
            success_count = int(self.redis.get(RedisKeys.STATS_SUCCESS) or 0)
            failed_count = int(self.redis.get(RedisKeys.STATS_FAILED) or 0)
            last_sync = self.redis.get(RedisKeys.LAST_SYNC)
            dlq_len = self.redis.llen("cmms:client:dlq")

            return {
                "queue_length": queue_len,
                "has_inflight": bool(has_inflight),
                "success_count": success_count,
                "failed_count": failed_count,
                "last_sync": last_sync,
                "dead_letter_queue": dlq_len,
                "status": "syncing" if queue_len > 0 or has_inflight else "idle",
            }
        except Exception as e:
            return {"error": str(e)}

    def get_dead_letter_queue(self) -> List[Dict]:
        """
        Get jobs that failed after max retries

        Returns:
            List of failed jobs
        """
        try:
            dlq_len = self.redis.llen("cmms:client:dlq")
            jobs = []

            for i in range(dlq_len):
                job_raw = self.redis.lindex("cmms:client:dlq", i)
                if job_raw:
                    jobs.append(json.loads(job_raw))

            return jobs
        except Exception as e:
            self.log.error(f"Error fetching DLQ: {e}")
            return []

    def clear_dead_letter_queue(self):
        """Clear the dead letter queue"""
        try:
            count = self.redis.delete("cmms:client:dlq")
            self.log.info(f"✅ Cleared {count} jobs from dead letter queue")
        except Exception as e:
            self.log.error(f"Error clearing DLQ: {e}")

    def retry_dead_letter_jobs(self):
        """
        Retry all jobs in dead letter queue

        Useful for recovering from temporary issues.
        """
        try:
            dlq_len = self.redis.llen("cmms:client:dlq")

            if dlq_len == 0:
                self.log.info("No jobs in dead letter queue")
                return

            self.log.info(f"🔄 Retrying {dlq_len} jobs from dead letter queue...")

            for _ in range(dlq_len):
                job_raw = self.redis.lpop("cmms:client:dlq")
                if job_raw:
                    job = json.loads(job_raw)
                    job["attempt"] = 0  # Reset attempt counter
                    self.redis.rpush(RedisKeys.QUEUE, json.dumps(job))

            self.log.info(f"✅ {dlq_len} jobs moved back to queue")

        except Exception as e:
            self.log.error(f"Error retrying DLQ jobs: {e}")

    # ============================================================
    # 5️⃣ LIFECYCLE
    # ============================================================

    def stop(self):
        """Stop the worker gracefully"""
        self.log.info("Stopping Redis queue worker...")
        self.stop_event.set()

        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=5)

        self.log.info("✅ Worker stopped")


# ============================================================
# USAGE EXAMPLE
# ============================================================

if __name__ == "__main__":
    """
    Example usage (standalone)
    """
    import redis
    from dotenv import load_dotenv

    load_dotenv()

    # Setup logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    # Connect to Redis
    redis_client = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        db=int(os.getenv("REDIS_DB", 0)),
        decode_responses=True,
    )

    # Create queue sync
    queue_sync = RedisQueueSync(
        redis_client=redis_client,
        api_url=os.getenv("API_URL", "https://hq-server-dgs6.onrender.com/api/sync"),
        client_id=os.getenv("CLIENT_ID", "test-client"),
        auth_token=os.getenv("AUTH_TOKEN", ""),
    )

    # Start worker
    queue_sync.start_worker()

    # Simulate some changes
    queue_sync.enqueue_change("CalSoft_asset", 123, "u", {"name": "Test Asset"})
    queue_sync.enqueue_change("CalSoft_asset", 124, "u", {"name": "Another Asset"})

    # Check status
    time.sleep(2)
    status = queue_sync.get_status()
    print(f"\nQueue status: {json.dumps(status, indent=2)}")

    # Keep running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        queue_sync.stop()
