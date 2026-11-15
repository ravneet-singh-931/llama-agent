"""
Advanced Monitoring Module for Llama Agent

Features:
- Custom alerts (email, Slack, webhook)
- Business metrics (questions asked, success rate)
- Cost tracking (Groq API spending)
- SLA monitoring (uptime, response time)
- Alert routing and escalation
"""

import os
import time
import json
from typing import Dict, List, Callable
from datetime import datetime, timedelta
from collections import defaultdict
from enum import Enum


# ============ ALERT SEVERITY ============


class AlertSeverity(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ============ CUSTOM ALERTS ============


class AlertManager:
    """
    Manages alerts and notifications.
    
    Features:
    - Multiple notification channels (email, Slack, webhook)
    - Alert deduplication
    - Escalation policies
    - Alert history
    """
    
    def __init__(self):
        self.alerts: List[Dict] = []
        self.notification_channels: List[Callable] = []
        self.alert_history: List[Dict] = []
        self.dedup_window = 300  # 5 minutes
        
        # Alert rules
        #
        # Every condition below must key off a field that the metrics dict fed
        # to check_alerts() actually contains -- otherwise the rule is dead
        # code that reads as a working control. Four of these used to:
        # ``error_rate`` (the feed publishes ``failure_rate``), ``daily_cost``
        # (spend lives in cost_summary()/Prometheus, not business metrics),
        # ``uptime_percent`` (the feed has ``uptime_hours``) and
        # ``memory_usage_percent`` (nothing measured memory; psutil is not a
        # dependency). The alert manager swallowed the empty result as "no
        # alerts", so a permanently-green alert list was indistinguishable
        # from a healthy service.
        self.rules: Dict[str, Dict] = {
            "high_error_rate": {
                "condition": lambda metrics: metrics.get("failure_rate", 0) > 5,
                "severity": AlertSeverity.ERROR,
                "message": "High error rate detected: {failure_rate}%",
            },
            "slow_response": {
                "condition": lambda metrics: metrics.get("avg_response_time", 0) > 2,
                "severity": AlertSeverity.WARNING,
                "message": "Slow response times: {avg_response_time}s average",
            },
        }
    
    def add_notification_channel(self, channel: Callable) -> None:
        """Add a notification channel (email, Slack, webhook)."""
        self.notification_channels.append(channel)
    
    def _should_alert(self, alert_type: str) -> bool:
        """Check if we should send this alert (deduplication)."""
        now = time.time()
        
        # Check recent alerts of same type
        recent_alerts = [
            a for a in self.alert_history
            if a["type"] == alert_type
            and now - a["timestamp"] < self.dedup_window
        ]
        
        return len(recent_alerts) == 0
    
    def check_alerts(self, metrics: Dict) -> List[Dict]:
        """Check all alert rules against current metrics."""
        triggered_alerts = []
        
        for rule_name, rule in self.rules.items():
            try:
                if rule["condition"](metrics):
                    if self._should_alert(rule_name):
                        alert = {
                            "type": rule_name,
                            "severity": rule["severity"],
                            "message": rule["message"].format(**metrics),
                            "timestamp": time.time(),
                            "metrics": metrics,
                        }
                        
                        triggered_alerts.append(alert)
                        self.alert_history.append(alert)
                        
                        # Send notifications
                        for channel in self.notification_channels:
                            try:
                                channel(alert)
                            except Exception as e:
                                print(f"[ALERT] Notification failed: {e}")
            
            except Exception as e:
                print(f"[ALERT] Rule check failed: {rule_name} - {e}")
        
        return triggered_alerts
    
    def get_recent_alerts(self, hours: int = 24) -> List[Dict]:
        """Get alerts from the last N hours."""
        cutoff = time.time() - (hours * 3600)
        return [
            a for a in self.alert_history
            if a["timestamp"] > cutoff
        ]


# ============ BUSINESS METRICS ============


class BusinessMetrics:
    """
    Tracks business-relevant metrics.
    
    Metrics tracked:
    - Total questions asked
    - Success/failure rate
    - Average response time
    - Most common queries
    - User activity patterns
    - Feature usage
    """
    
    def __init__(self):
        self.metrics = {
            "total_questions": 0,
            "successful_answers": 0,
            "failed_answers": 0,
            "total_users": 0,
            "active_users": set(),
            "documents_uploaded": 0,
            "questions_by_hour": defaultdict(int),
            "questions_by_day": defaultdict(int),
            "common_queries": defaultdict(int),
            "response_times": [],
            "feature_usage": defaultdict(int),
        }
        
        self.start_time = time.time()
    
    def record_question(
        self,
        user_id: str,
        success: bool,
        response_time: float,
        query_type: str = "general",
    ) -> None:
        """Record a question was asked."""
        self.metrics["total_questions"] += 1
        
        if success:
            self.metrics["successful_answers"] += 1
        else:
            self.metrics["failed_answers"] += 1
        
        # Track user
        self.metrics["active_users"].add(user_id)
        
        # Track time patterns
        now = datetime.now()
        self.metrics["questions_by_hour"][now.hour] += 1
        self.metrics["questions_by_day"][now.strftime("%Y-%m-%d")] += 1
        
        # Track response times (keep last 1000)
        self.metrics["response_times"].append(response_time)
        if len(self.metrics["response_times"]) > 1000:
            self.metrics["response_times"] = self.metrics["response_times"][-1000:]
        
        # Track query type
        self.metrics["common_queries"][query_type] += 1
    
    def record_document_upload(self, user_id: str) -> None:
        """Record a document was uploaded."""
        self.metrics["documents_uploaded"] += 1
        self.metrics["active_users"].add(user_id)
    
    def record_feature_usage(self, feature: str) -> None:
        """Record feature usage."""
        self.metrics["feature_usage"][feature] += 1
    
    def get_metrics(self) -> Dict:
        """Get all business metrics."""
        total = self.metrics["total_questions"]
        success = self.metrics["successful_answers"]
        
        response_times = self.metrics["response_times"]
        avg_response_time = (
            sum(response_times) / len(response_times)
            if response_times else 0
        )
        
        return {
            "total_questions": total,
            "success_rate": round(success / total * 100, 2) if total > 0 else 0,
            "failure_rate": round((total - success) / total * 100, 2) if total > 0 else 0,
            "total_users": len(self.metrics["active_users"]),
            "documents_uploaded": self.metrics["documents_uploaded"],
            "avg_response_time": round(avg_response_time, 3),
            "p95_response_time": round(self._percentile(response_times, 95), 3),
            "p99_response_time": round(self._percentile(response_times, 99), 3),
            "uptime_hours": round((time.time() - self.start_time) / 3600, 2),
            "questions_by_hour": dict(self.metrics["questions_by_hour"]),
            "top_query_types": dict(
                sorted(
                    self.metrics["common_queries"].items(),
                    key=lambda x: x[1],
                    reverse=True,
                )[:10]
            ),
            "feature_usage": dict(self.metrics["feature_usage"]),
        }
    
    def _percentile(self, data: List[float], percentile: int) -> float:
        """Calculate percentile of a list."""
        if not data:
            return 0
        
        sorted_data = sorted(data)
        index = int(len(sorted_data) * percentile / 100)
        return sorted_data[min(index, len(sorted_data) - 1)]


# ============ COST TRACKING ============


class SLAMonitor:
    """
    Monitors Service Level Agreement (SLA) compliance.
    
    Tracks:
    - Uptime percentage
    - Response time SLAs
    - Error rate SLAs
    - Availability
    """
    
    def __init__(self):
        self.start_time = time.time()
        self.downtime_events: List[Dict] = []
        self.response_times: List[float] = []
        self.errors: List[Dict] = []
        
        # SLA targets
        self.targets = {
            "uptime_percent": 99.9,  # 99.9% uptime
            "avg_response_time": 2.0,  # 2 seconds max
            "p95_response_time": 5.0,  # 5 seconds max
            "error_rate_percent": 1.0,  # 1% max error rate
        }
    
    def record_downtime(self, start: float, end: float, reason: str) -> None:
        """Record a downtime event."""
        self.downtime_events.append({
            "start": start,
            "end": end,
            "duration": end - start,
            "reason": reason,
        })
    
    def record_response_time(self, response_time: float) -> None:
        """Record a response time."""
        self.response_times.append(response_time)
        # Keep last 10000
        if len(self.response_times) > 10000:
            self.response_times = self.response_times[-10000:]
    
    def record_error(self, error_type: str, details: str) -> None:
        """Record an error."""
        self.errors.append({
            "timestamp": time.time(),
            "type": error_type,
            "details": details,
        })
    
    def get_sla_status(self) -> Dict:
        """Get current SLA compliance status."""
        now = time.time()
        total_time = now - self.start_time
        
        # Calculate downtime
        total_downtime = sum(e["duration"] for e in self.downtime_events)
        uptime_percent = ((total_time - total_downtime) / total_time) * 100
        
        # Calculate response times
        if self.response_times:
            avg_response = sum(self.response_times) / len(self.response_times)
            sorted_times = sorted(self.response_times)
            p95_index = int(len(sorted_times) * 0.95)
            p95_response = sorted_times[min(p95_index, len(sorted_times) - 1)]
        else:
            avg_response = 0
            p95_response = 0
        
        # Calculate error rate.
        #
        # The denominator used to be len(response_times) -- successful requests
        # only -- and 0 when nothing had succeeded yet, so a service whose
        # FIRST request failed reported "error rate 0%, met: True". Measured:
        # one error and no successes gave current=0.0 met=True.
        #
        # Requests here are attempts: one recorded response time or one error.
        # A non-zero error count against zero successes is 100%, not 0%.
        total_requests = len(self.response_times) + len(self.errors)
        error_count = len(self.errors)
        error_rate = (error_count / total_requests * 100) if total_requests > 0 else 0
        
        # Check SLA compliance
        compliance = {
            "uptime": {
                "current": round(uptime_percent, 3),
                "target": self.targets["uptime_percent"],
                "met": uptime_percent >= self.targets["uptime_percent"],
            },
            "avg_response_time": {
                "current": round(avg_response, 3),
                "target": self.targets["avg_response_time"],
                "met": avg_response <= self.targets["avg_response_time"],
            },
            "p95_response_time": {
                "current": round(p95_response, 3),
                "target": self.targets["p95_response_time"],
                "met": p95_response <= self.targets["p95_response_time"],
            },
            "error_rate": {
                "current": round(error_rate, 3),
                "target": self.targets["error_rate_percent"],
                "met": error_rate <= self.targets["error_rate_percent"],
            },
        }
        
        # Overall status
        all_met = all(c["met"] for c in compliance.values())
        
        return {
            "overall_status": "COMPLIANT" if all_met else "NON-COMPLIANT",
            "uptime_hours": round(total_time / 3600, 2),
            "total_downtime_minutes": round(total_downtime / 60, 2),
            "compliance": compliance,
        }


# ============ SINGLETON INSTANCES ============


# Global instances
alert_manager = AlertManager()
business_metrics = BusinessMetrics()
sla_monitor = SLAMonitor()


# ============ NOTIFICATION CHANNELS ============


# Notification channels.
#
# There used to be three functions here -- slack_notification,
# email_notification, webhook_notification -- all of which only printed, and
# all three were registered on alert_manager at import time. So a firing alert
# printed the same line three times with a different prefix and was delivered
# nowhere. That is worse than having no channel: the dashboard showed "alerts
# are configured", and the only thing configured was stdout, which is already
# the alert's own destination via the print in check_alerts().
#
# Deleted rather than implemented. Real delivery needs a webhook URL, retries,
# a failure mode of its own, and somewhere for its own failures to go; that is
# a feature with an env var and a test, not a placeholder. The channel list
# stays, and add_notification_channel() is the hook for whoever adds one.


# ============ MONITORING DASHBOARD ============


def _cost_summary() -> Dict:
    """Costs, read from Prometheus rather than a second in-process ledger.

    There used to be a CostTracker singleton here with its own dict. It was
    per-process, so two replicas disagreed and a restart erased both, while
    the same calls were ALSO going into the llama_agent_llm_cost_usd_total
    counter that Grafana reads. Two numbers, one truth, no reconciliation.
    """
    from modules.prometheus_metrics import cost_summary
    return cost_summary()


def get_monitoring_dashboard() -> Dict:
    """Get all metrics for the monitoring dashboard."""
    return {
        "timestamp": datetime.now().isoformat(),
        "business": business_metrics.get_metrics(),
        "costs": _cost_summary(),
        "sla": sla_monitor.get_sla_status(),
        "alerts": alert_manager.get_recent_alerts(hours=24),
    }
