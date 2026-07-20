"""
Monitoring & Logging Module
Tracks what the agent does, errors, and performance.
This helps you understand how users interact with your agent!
"""

import json
import os
import time
import traceback
from datetime import datetime
from typing import Dict, List, Optional
from functools import wraps


class AgentLogger:
    """
    Logs everything the agent does.
    Helps you understand user behavior and debug issues.
    """
    
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        
        self.log_file = os.path.join(log_dir, "agent.log")
        self.interactions_file = os.path.join(log_dir, "interactions.json")
        self.errors_file = os.path.join(log_dir, "errors.json")
        self.metrics_file = os.path.join(log_dir, "metrics.json")
        
        # Initialize metrics
        self.metrics = self._load_metrics()
    
    def _load_metrics(self) -> Dict:
        """Load existing metrics"""
        if os.path.exists(self.metrics_file):
            try:
                with open(self.metrics_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        
        return {
            "total_queries": 0,
            "total_errors": 0,
            "total_tool_calls": 0,
            "avg_response_time": 0,
            "response_times": [],
            "popular_tools": {},
            "first_seen": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat()
        }
    
    def _save_metrics(self):
        """Save metrics to file"""
        with open(self.metrics_file, 'w') as f:
            json.dump(self.metrics, f, indent=2)
    
    def log_interaction(self, user_input: str, agent_response: str, 
                       tools_used: List[str], response_time: float):
        """Log a user-agent interaction"""
        interaction = {
            "timestamp": datetime.now().isoformat(),
            "user_input": user_input,
            "agent_response": agent_response[:500],  # Truncate long responses
            "tools_used": tools_used,
            "response_time": response_time
        }
        
        # Save to interactions file
        interactions = []
        if os.path.exists(self.interactions_file):
            try:
                with open(self.interactions_file, 'r') as f:
                    interactions = json.load(f)
            except:
                interactions = []
        
        interactions.append(interaction)
        
        # Keep only last 1000 interactions
        if len(interactions) > 1000:
            interactions = interactions[-1000:]
        
        with open(self.interactions_file, 'w') as f:
            json.dump(interactions, f, indent=2, ensure_ascii=False)
        
        # Update metrics
        self.metrics["total_queries"] += 1
        self.metrics["response_times"].append(response_time)
        self.metrics["response_times"] = self.metrics["response_times"][-100:]
        self.metrics["avg_response_time"] = sum(self.metrics["response_times"]) / len(self.metrics["response_times"])
        self.metrics["last_seen"] = datetime.now().isoformat()
        
        for tool in tools_used:
            self.metrics["popular_tools"][tool] = self.metrics["popular_tools"].get(tool, 0) + 1
            self.metrics["total_tool_calls"] += 1
        
        self._save_metrics()
        
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
        
        # Save to errors file
        errors = []
        if os.path.exists(self.errors_file):
            try:
                with open(self.errors_file, 'r') as f:
                    errors = json.load(f)
            except:
                errors = []
        
        errors.append(error_info)
        
        # Keep only last 100 errors
        if len(errors) > 100:
            errors = errors[-100:]
        
        with open(self.errors_file, 'w') as f:
            json.dump(errors, f, indent=2)
        
        # Update metrics
        self.metrics["total_errors"] += 1
        self._save_metrics()
        
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
    
    def get_metrics(self) -> Dict:
        """Get current metrics"""
        return self.metrics
    
    def get_recent_interactions(self, n: int = 10) -> List[Dict]:
        """Get recent interactions"""
        if os.path.exists(self.interactions_file):
            try:
                with open(self.interactions_file, 'r') as f:
                    interactions = json.load(f)
                return interactions[-n:]
            except:
                return []
        return []
    
    def get_popular_tools(self) -> Dict:
        """Get most used tools"""
        return self.metrics.get("popular_tools", {})
    
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
            except:
                pass
        return {"total_errors": 0, "error_types": {}, "recent_errors": []}


class PerformanceMonitor:
    """
    Monitors agent performance and health.
    """
    
    def __init__(self):
        self.start_time = time.time()
        self.request_count = 0
        self.error_count = 0
    
    def track_request(self):
        """Track a request"""
        self.request_count += 1
    
    def track_error(self):
        """Track an error"""
        self.error_count += 1
    
    def get_uptime(self) -> float:
        """Get uptime in seconds"""
        return time.time() - self.start_time
    
    def get_health(self) -> Dict:
        """Get health status"""
        uptime = self.get_uptime()
        error_rate = self.error_count / max(self.request_count, 1)
        
        return {
            "status": "healthy" if error_rate < 0.1 else "degraded",
            "uptime_seconds": uptime,
            "uptime_human": self._format_uptime(uptime),
            "total_requests": self.request_count,
            "total_errors": self.error_count,
            "error_rate": f"{error_rate:.2%}"
        }
    
    def _format_uptime(self, seconds: float) -> str:
        """Format uptime as human readable"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours}h {minutes}m {secs}s"


def monitor_decorator(func):
    """Decorator to monitor function calls"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            elapsed = time.time() - start_time
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            raise
    return wrapper
