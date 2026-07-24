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
import hashlib
from typing import Dict, List, Optional, Callable
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
        self.rules: Dict[str, Dict] = {
            "high_error_rate": {
                "condition": lambda metrics: metrics.get("error_rate", 0) > 5,
                "severity": AlertSeverity.ERROR,
                "message": "High error rate detected: {error_rate}%",
            },
            "slow_response": {
                "condition": lambda metrics: metrics.get("avg_response_time", 0) > 2,
                "severity": AlertSeverity.WARNING,
                "message": "Slow response times: {avg_response_time}s average",
            },
            "high_cost": {
                "condition": lambda metrics: metrics.get("daily_cost", 0) > 10,
                "severity": AlertSeverity.WARNING,
                "message": "High API cost: ${daily_cost:.2f} today",
            },
            "low_uptime": {
                "condition": lambda metrics: metrics.get("uptime_percent", 100) < 99.9,
                "severity": AlertSeverity.CRITICAL,
                "message": "Low uptime: {uptime_percent}%",
            },
            "memory_high": {
                "condition": lambda metrics: metrics.get("memory_usage_percent", 0) > 80,
                "severity": AlertSeverity.WARNING,
                "message": "High memory usage: {memory_usage_percent}%",
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


class CostTracker:
    """
    Tracks API costs and usage.
    
    Tracks:
    - Groq API costs
    - Token usage
    - Cost per user
    - Cost per query type
    - Daily/monthly spending
    """
    
    def __init__(self):
        # Groq pricing (per 1M tokens)
        self.pricing = {
            "llama3-8b-8192": {
                "input": 0.05,  # $0.05 per 1M input tokens
                "output": 0.10,  # $0.10 per 1M output tokens
            },
            "llama3-70b-8192": {
                "input": 0.59,
                "output": 0.79,
            },
            "mixtral-8x7b-32768": {
                "input": 0.24,
                "output": 0.24,
            },
        }
        
        self.usage = {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost": 0,
            "daily_cost": defaultdict(float),
            "cost_by_model": defaultdict(float),
            "cost_by_user": defaultdict(float),
            "cost_by_query_type": defaultdict(float),
        }
        
        self.start_time = time.time()
    
    def record_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        user_id: str = "unknown",
        query_type: str = "general",
    ) -> float:
        """Record API usage and return cost."""
        # Calculate cost
        if model in self.pricing:
            input_cost = (input_tokens / 1_000_000) * self.pricing[model]["input"]
            output_cost = (output_tokens / 1_000_000) * self.pricing[model]["output"]
            total_cost = input_cost + output_cost
        else:
            # Default pricing
            input_cost = (input_tokens / 1_000_000) * 0.05
            output_cost = (output_tokens / 1_000_000) * 0.10
            total_cost = input_cost + output_cost
        
        # Update totals
        self.usage["total_input_tokens"] += input_tokens
        self.usage["total_output_tokens"] += output_tokens
        self.usage["total_cost"] += total_cost
        
        # Update daily cost
        today = datetime.now().strftime("%Y-%m-%d")
        self.usage["daily_cost"][today] += total_cost
        
        # Update by model
        self.usage["cost_by_model"][model] += total_cost
        
        # Update by user
        self.usage["cost_by_user"][user_id] += total_cost
        
        # Update by query type
        self.usage["cost_by_query_type"][query_type] += total_cost
        
        return total_cost
    
    def get_cost_summary(self) -> Dict:
        """Get cost summary."""
        today = datetime.now().strftime("%Y-%m-%d")
        
        return {
            "total_cost": round(self.usage["total_cost"], 4),
            "today_cost": round(self.usage["daily_cost"].get(today, 0), 4),
            "total_input_tokens": self.usage["total_input_tokens"],
            "total_output_tokens": self.usage["total_output_tokens"],
            "total_tokens": (
                self.usage["total_input_tokens"] + 
                self.usage["total_output_tokens"]
            ),
            "cost_by_model": dict(self.usage["cost_by_model"]),
            "cost_by_query_type": dict(self.usage["cost_by_query_type"]),
            "top_users_by_cost": dict(
                sorted(
                    self.usage["cost_by_user"].items(),
                    key=lambda x: x[1],
                    reverse=True,
                )[:10]
            ),
        }
    
    def get_daily_costs(self, days: int = 30) -> Dict[str, float]:
        """Get daily costs for the last N days."""
        result = {}
        for i in range(days):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            result[date] = round(self.usage["daily_cost"].get(date, 0), 4)
        return result


# ============ SLA MONITORING ============


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
        
        # Calculate error rate
        total_requests = len(self.response_times)
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
cost_tracker = CostTracker()
sla_monitor = SLAMonitor()


# ============ NOTIFICATION CHANNELS ============


def slack_notification(alert: Dict) -> None:
    """Send alert to Slack (placeholder)."""
    # In production, use Slack API
    print(f"[SLACK] {alert['severity'].value.upper()}: {alert['message']}")


def email_notification(alert: Dict) -> None:
    """Send alert via email (placeholder)."""
    # In production, use SMTP
    print(f"[EMAIL] {alert['severity'].value.upper()}: {alert['message']}")


def webhook_notification(alert: Dict) -> None:
    """Send alert via webhook (placeholder)."""
    # In production, use HTTP POST
    print(f"[WEBHOOK] {alert['severity'].value.upper()}: {alert['message']}")


# Register default notification channels
alert_manager.add_notification_channel(slack_notification)
alert_manager.add_notification_channel(email_notification)
alert_manager.add_notification_channel(webhook_notification)


# ============ MONITORING DASHBOARD ============


def get_monitoring_dashboard() -> Dict:
    """Get all metrics for the monitoring dashboard."""
    return {
        "timestamp": datetime.now().isoformat(),
        "business": business_metrics.get_metrics(),
        "costs": cost_tracker.get_cost_summary(),
        "sla": sla_monitor.get_sla_status(),
        "alerts": alert_manager.get_recent_alerts(hours=24),
    }
