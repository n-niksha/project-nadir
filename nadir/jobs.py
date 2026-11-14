"""Background jobs with staged progress, deduplication and cancellation.

Long operations (archive discovery, downloads, detection, rendering) run in a
small thread pool; the web client polls ``Job.snapshot()``.  A job with the
same ``key`` that is still running (or finished successfully a moment ago) is
reused instead of duplicated.  Cancellation is cooperative: work functions
call ``job.check()`` between items.
"""
from __future__ import annotations

import concurrent.futures
import itertools
import logging
import threading
import time
import traceback

log = logging.getLogger(__name__)


class JobCancelled(Exception):
    pass


class Job:
    _ids = itertools.count(1)

    def __init__(self, kind: str, key: str, stages: list[str]):
        self.id = f"j{next(self._ids)}"
        self.kind, self.key = kind, key
        self.stages = stages
        self.stage = stages[0] if stages else None
        self.status = "queued"
        self.done = self.total = 0
        self.message = ""
        self.detail: dict = {}
        self.error: str | None = None
        self.result = None
        self.created = self.updated = time.time()
        self.stage_times: dict = {}
        self._stage_t0 = time.time()
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    def set_stage(self, stage: str, message: str = "", total: int = 0):
        with self._lock:
            now = time.time()
            if self.stage:
                self.stage_times[self.stage] = round(now - self._stage_t0, 3)
            self.stage, self.message, self.done, self.total = stage, message, 0, total
            self._stage_t0 = self.updated = now
        self.check()

    def progress(self, done: int, total: int | None = None, message: str | None = None, **detail):
        with self._lock:
            self.done = done
            if total is not None:
                self.total = total
            if message is not None:
                self.message = message
            self.detail.update(detail)
            self.updated = time.time()

    def cancel(self):
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check(self):
        if self._cancel.is_set():
            raise JobCancelled()

    def snapshot(self) -> dict:
        with self._lock:
            idx = self.stages.index(self.stage) if self.stage in self.stages else 0
            return {"id": self.id, "kind": self.kind, "status": self.status, "stage": self.stage,
                    "stage_index": idx, "stages": self.stages, "done": self.done, "total": self.total,
                    "message": self.message, "detail": dict(self.detail), "error": self.error,
                    "result": self.result if self.status == "done" else None,
                    "elapsed_s": round(time.time() - self.created, 2), "stage_times": dict(self.stage_times)}


class JobManager:
    def __init__(self, workers: int = 3, keep_s: float = 600.0):
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="nadir-job")
        self._jobs: dict[str, Job] = {}
        self._by_key: dict[str, Job] = {}
        self._lock = threading.Lock()
        self.keep_s = keep_s

    def submit(self, kind: str, key: str, stages: list[str], fn) -> Job:
        with self._lock:
            self._gc()
            old = self._by_key.get(key)
            if old and old.status in ("queued", "running") and not old.cancelled:
                return old
            if old and old.status == "done" and time.time() - old.updated < 5.0:
                return old
            job = Job(kind, key, stages)
            self._jobs[job.id] = job
            self._by_key[key] = job
        self._pool.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn):
        job.status = "running"
        try:
            job.check()
            job.result = fn(job)
            if job.stage:
                job.stage_times[job.stage] = round(time.time() - job._stage_t0, 3)
            job.status = "done"
        except JobCancelled:
            job.status = "cancelled"
        except Exception as exc:  # reported to the client, never swallowed
            log.error("Job %s (%s) failed: %s\n%s", job.id, job.kind, exc, traceback.format_exc())
            job.status, job.error = "error", str(exc) or exc.__class__.__name__
        job.updated = time.time()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job and job.status in ("queued", "running"):
            job.cancel()
            return True
        return False

    def _gc(self):
        now = time.time()
        for jid in [j for j, job in self._jobs.items()
                    if job.status in ("done", "error", "cancelled") and now - job.updated > self.keep_s]:
            job = self._jobs.pop(jid)
            if self._by_key.get(job.key) is job:
                del self._by_key[job.key]


manager = JobManager()
