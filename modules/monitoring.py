"""
Monitoring & Logging Module
Tracks what the agent does, errors, and performance.
This helps you understand how users interact with your agent!
"""

import json
import os
import threading
import time
import traceback
from datetime import datetime
from typing import Dict, List

from modules.silent import degrade

class AgentLogger:
    """The forensic record: what was asked, what was answered, what failed.

    This class used to keep a running `metrics.json` alongside those logs --
    total_queries, total_tool_calls, avg_response_time, popular_tools -- and
    rewrote it on every request, after first re-reading the whole
    interactions file. Three problems, all recorded in
    docs/OBSERVABILITY_FINDINGS.md finding 7:

      * it was a second, per-process set of counts for events Prometheus
        already counts (`prometheus_middleware`, `record_chat`,
        `record_tool_seconds`), and the two could never agree;
      * it did two full-file rewrites inside the request handler;
      * two of its numbers were never true. `documents_uploaded` had no
        writer anywhere. `total_tool_calls` was only incremented by looping
        over the `tools_used` argument that every caller passes as `[]`.

    The log files stay: Prometheus counts events, it cannot tell you what a
    user said in the request that broke. Counts now have one owner.
    """

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

        self.log_file = os.path.join(log_dir, "agent.log")
        self.interactions_file = os.path.join(log_dir, "interactions.json")
        self.errors_file = os.path.join(log_dir, "errors.json")

        # Serializes all file I/O in log_interaction/log_error so concurrent
        # requests can't corrupt or lose each other's writes.
        self._lock = threading.Lock()
    
    def log_interaction(self, user_input: str, agent_response: str, 
                       tools_used: List[str], response_time: float):
        """Log a user-agent interaction.

        High-risk identifiers (SSNs, payment card numbers) are redacted before
        the record is written: whatever a user pastes would otherwise be
        persisted verbatim in a log file that outlives the request. Redaction
        happens here, on the way out only - the agent still reasons over the
        original text.
        """
        from modules.pii import redact
        interaction = {
            "timestamp": datetime.now().isoformat(),
            "user_input": redact(user_input),
            "agent_response": redact(agent_response[:500]),  # Truncate long responses
            "tools_used": tools_used,
            "response_time": response_time
        }
        
        # All file I/O is serialized so concurrent requests can't corrupt or
        # lose each other's interaction/metrics writes (the previous
        # read-modify-write was not atomic under load).
        with self._lock:
            # Save to interactions file
            interactions = []
            if os.path.exists(self.interactions_file):
                try:
                    with open(self.interactions_file, 'r') as f:
                        interactions = json.load(f)
                except (OSError, ValueError) as _e:
                    # Start from an empty list rather than lose this
                    # interaction; the old history is discarded.
                    degrade("corrupt/unreadable interaction history (old entries discarded)", _e)
                    interactions = []
            
            interactions.append(interaction)

            # Keep only last 1000 interactions
            if len(interactions) > 1000:
                interactions = interactions[-1000:]

            with open(self.interactions_file, 'w', encoding='utf-8') as f:
                json.dump(interactions, f, indent=2, ensure_ascii=False)

            # Also log to text file
            self._log_text(f"[{datetime.now()}] User: {user_input[:100]}")
            self._log_text(f"[{datetime.now()}] Agent: {agent_response[:100]}")
            self._log_text(f"[{datetime.now()}] Tools: {tools_used}")
            self._log_text(f"[{datetime.now()}] Time: {response_time:.2f}s")
            self._log_text("---")
    
    def log_error(self, error: Exception, context: str = ""):
        """Log an error"""
        error_info = {
            "timestamp": datetime.now().isoformat(),
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
            "context": context
        }
        
        # Serialized with the same lock as log_interaction so the two never
        # interleave on the shared metrics/error files.
        with self._lock:
            # Save to errors file
            errors = []
            if os.path.exists(self.errors_file):
                try:
                    with open(self.errors_file, 'r') as f:
                        errors = json.load(f)
                except (OSError, ValueError) as _e:
                    # Start from an empty list; the old error log is discarded.
                    degrade("corrupt/unreadable error log (old entries discarded)", _e)
                    errors = []
            
            errors.append(error_info)
            
            # Keep only last 100 errors
            if len(errors) > 100:
                errors = errors[-100:]
            
            with open(self.errors_file, 'w', encoding='utf-8') as f:
                json.dump(errors, f, indent=2)

            # Log to text file
            self._log_text(f"[{datetime.now()}] ERROR: {type(error).__name__}: {str(error)}")
    
    def log_tool_call(self, tool_name: str, input_data: str, output_data: str):
        """Log a tool call"""
        self._log_text(f"[{datetime.now()}] TOOL CALL: {tool_name}")
        self._log_text(f"  Input: {input_data[:100]}")
        self._log_text(f"  Output: {output_data[:100]}")
    
    def _log_text(self, message: str):
        """Append to log file"""
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(message + "\n")
    
    def get_error_count(self) -> int:
        """Errors on record. Counted from the log file, not a second counter.

        Prometheus counts HTTP errors by status; this counts the exceptions
        this process logged, which is the same set `get_error_summary()`
        reports. Deriving it from the file means the count and the list can
        never disagree.
        """
        return self.get_error_summary()["total_errors"]

    def get_recent_interactions(self, n: int = 10) -> List[Dict]:
        """Get recent interactions"""
        if os.path.exists(self.interactions_file):
            try:
                with open(self.interactions_file, 'r') as f:
                    interactions = json.load(f)
                return interactions[-n:]
            except (OSError, ValueError):
                return []
        return []
    
    def get_error_summary(self) -> Dict:
        """Get error summary"""
        if os.path.exists(self.errors_file):
            try:
                with open(self.errors_file, 'r') as f:
                    errors = json.load(f)
                
                error_types = {}
                for error in errors:
                    error_type = error["error_type"]
                    error_types[error_type] = error_types.get(error_type, 0) + 1
                
                return {
                    "total_errors": len(errors),
                    "error_types": error_types,
                    "recent_errors": errors[-5:]
                }
            except (OSError, ValueError, KeyError, TypeError) as _e:
                degrade("error log unreadable or malformed (empty error summary returned)", _e)
        return {"total_errors": 0, "error_types": {}, "recent_errors": []}


class PerformanceMonitor:
    """
    Process health: how long this process has been up.

    This class used to also keep ``request_count`` / ``error_count`` and
    derive a ``status`` from their ratio. That was a second set of books for
    an event Prometheus already counts in ``prometheus_middleware`` -- and a
    worse set:

      * it only counted the chat and MCP-tool handlers (``track_request`` had
        2 call sites) while the middleware counts every request, so the two
        totals could never agree;
      * ``track_error`` had 4 call sites, so most failures never reached it;
      * ``/health`` is the k8s liveness/readiness/startup probe
        (``k8s/api.yaml``), so probe traffic grew the denominator. On a quiet
        day that is most of the traffic, which made the error rate -- and
        therefore ``status`` -- get rosier the more the probe ran;
      * the counters lived in process memory, and the API runs 2-10 replicas
        (``k8s/api.yaml``, ``k8s/api-hpa.yaml``), so each pod reported its own
        fraction of the truth. The same reasoning already moved per-user spend
        to a Prometheus counter (see ``modules/prometheus_metrics.py``).

    Request and error counting now has exactly one owner: Prometheus, scraped
    from ``/metrics``. Uptime is the one number this class owns honestly --
    it really is *this process's* uptime, and it is labelled that way in the
    callers.
    """

    def __init__(self):
        self.start_time = time.time()

    def get_uptime(self) -> float:
        """Get uptime in seconds"""
        return time.time() - self.start_time

    def get_health(self) -> Dict:
        """Process health for /health and /agents/stats.

        Deliberately contains no request or error counts: see the class
        docstring. ``status`` is about the process being up, not about traffic.
        """
        uptime = self.get_uptime()

        return {
            "status": "healthy",
            "uptime_seconds": uptime,
            "uptime_human": self._format_uptime(uptime),
        }
    
    def _format_uptime(self, seconds: float) -> str:
        """Format uptime as human readable"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours}h {minutes}m {secs}s"



