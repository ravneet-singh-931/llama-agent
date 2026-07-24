"""
Feature Flags Module for Llama Agent

Features:
- Toggle features without deployment
- A/B testing support
- Gradual rollouts
- User-specific feature access
"""

import os
import json
import hashlib
from typing import Dict, List, Optional, Set
from datetime import datetime


# ============ FEATURE FLAGS ============


class FeatureFlagManager:
    """
    Manages feature flags for gradual rollouts.
    
    Features:
    - Enable/disable features without deployment
    - Percentage-based rollouts
    - User-specific overrides
    - Environment-based flags
    """
    
    def __init__(self, config_file: str = "config/feature_flags.json"):
        self.config_file = config_file
        self.flags: Dict[str, Dict] = {}
        
        # Load flags
        self._load_flags()
    
    def _load_flags(self) -> None:
        """Load feature flags from file."""
        if os.path.exists(self.config_file):
            with open(self.config_file, "r") as f:
                self.flags = json.load(f)
        else:
            # Default flags
            self.flags = {
                "enable_advanced_rag": {
                    "enabled": True,
                    "description": "Enable advanced RAG features (re-ranking, sub-questions)",
                },
                "enable_phoenix_tracing": {
                    "enabled": True,
                    "description": "Enable Phoenix observability tracing",
                },
                "enable_caching": {
                    "enabled": True,
                    "description": "Enable response caching",
                },
                "enable_rate_limiting": {
                    "enabled": True,
                    "description": "Enable rate limiting",
                },
                "enable_websockets": {
                    "enabled": True,
                    "description": "Enable WebSocket support",
                },
                "enable_file_upload": {
                    "enabled": True,
                    "description": "Enable file upload feature",
                },
                "enable_mcp_agents": {
                    "enabled": False,
                    "description": "Enable MCP multi-agent team",
                },
                "enable_beta_features": {
                    "enabled": False,
                    "description": "Enable experimental features",
                },
            }
            self._save_flags()
    
    def _save_flags(self) -> None:
        """Save feature flags to file."""
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
        with open(self.config_file, "w") as f:
            json.dump(self.flags, f, indent=2)
    
    def is_enabled(self, flag_name: str, user_id: Optional[str] = None) -> bool:
        """
        Check if a feature flag is enabled.
        
        Args:
            flag_name: Name of the feature flag
            user_id: Optional user ID for user-specific overrides
        """
        if flag_name not in self.flags:
            return False
        
        flag = self.flags[flag_name]
        
        # Check if globally enabled
        if not flag.get("enabled", False):
            return False
        
        # Check percentage rollout
        rollout_percent = flag.get("rollout_percent", 100)
        if rollout_percent < 100 and user_id:
            # Deterministic rollout based on user_id
            user_hash = int(hashlib.md5(user_id.encode()).hexdigest(), 16) % 100
            if user_hash >= rollout_percent:
                return False
        
        # Check user-specific overrides
        if user_id:
            user_overrides = flag.get("user_overrides", {})
            if user_id in user_overrides:
                return user_overrides[user_id]
        
        return True
    
    def set_flag(
        self,
        flag_name: str,
        enabled: bool,
        description: str = "",
        rollout_percent: int = 100,
    ) -> None:
        """Set a feature flag."""
        self.flags[flag_name] = {
            "enabled": enabled,
            "description": description or self.flags.get(flag_name, {}).get("description", ""),
            "rollout_percent": rollout_percent,
            "updated_at": datetime.now().isoformat(),
        }
        self._save_flags()
    
    def set_user_override(self, flag_name: str, user_id: str, enabled: bool) -> None:
        """Set a user-specific override for a feature flag."""
        if flag_name not in self.flags:
            self.flags[flag_name] = {"enabled": False, "description": ""}
        
        if "user_overrides" not in self.flags[flag_name]:
            self.flags[flag_name]["user_overrides"] = {}
        
        self.flags[flag_name]["user_overrides"][user_id] = enabled
        self._save_flags()
    
    def get_all_flags(self) -> Dict:
        """Get all feature flags."""
        return self.flags.copy()
    
    def get_enabled_flags(self) -> List[str]:
        """Get list of enabled feature flags."""
        return [name for name, flag in self.flags.items() if flag.get("enabled", False)]
    
    def get_disabled_flags(self) -> List[str]:
        """Get list of disabled feature flags."""
        return [name for name, flag in self.flags.items() if not flag.get("enabled", False)]


# ============ A/B TESTING ============


class ABTestManager:
    """
    Manages A/B testing for features.
    
    Features:
    - Split users into groups
    - Track metrics per group
    - Statistical significance testing
    """
    
    def __init__(self):
        self.experiments: Dict[str, Dict] = {}
        self.user_assignments: Dict[str, str] = {}
        self.metrics: Dict[str, Dict[str, List]] = {}
    
    def create_experiment(
        self,
        experiment_id: str,
        variants: List[str],
        description: str = "",
    ) -> None:
        """Create a new A/B test experiment."""
        self.experiments[experiment_id] = {
            "variants": variants,
            "description": description,
            "created_at": datetime.now().isoformat(),
            "is_running": True,
        }
        
        # Initialize metrics for each variant
        self.metrics[experiment_id] = {variant: [] for variant in variants}
    
    def assign_variant(self, experiment_id: str, user_id: str) -> str:
        """Assign a user to a variant."""
        if experiment_id not in self.experiments:
            return "control"
        
        # Check if already assigned
        assignment_key = f"{experiment_id}:{user_id}"
        if assignment_key in self.user_assignments:
            return self.user_assignments[assignment_key]
        
        # Deterministic assignment based on user_id
        experiment = self.experiments[experiment_id]
        variants = experiment["variants"]
        
        user_hash = int(hashlib.md5(user_id.encode()).hexdigest(), 16) % len(variants)
        variant = variants[user_hash]
        
        # Store assignment
        self.user_assignments[assignment_key] = variant
        
        return variant
    
    def record_metric(
        self,
        experiment_id: str,
        variant: str,
        metric_name: str,
        value: float,
    ) -> None:
        """Record a metric for an experiment variant."""
        if experiment_id not in self.metrics:
            return
        
        if variant not in self.metrics[experiment_id]:
            return
        
        self.metrics[experiment_id][variant].append({
            "metric": metric_name,
            "value": value,
            "timestamp": datetime.now().isoformat(),
        })
    
    def get_experiment_results(self, experiment_id: str) -> Dict:
        """Get results for an experiment."""
        if experiment_id not in self.experiments:
            return {"error": "Experiment not found"}
        
        experiment = self.experiments[experiment_id]
        results = {
            "experiment_id": experiment_id,
            "description": experiment["description"],
            "is_running": experiment["is_running"],
            "variants": {},
        }
        
        for variant in experiment["variants"]:
            variant_metrics = self.metrics.get(experiment_id, {}).get(variant, [])
            
            # Calculate averages
            metric_values = {}
            for m in variant_metrics:
                name = m["metric"]
                if name not in metric_values:
                    metric_values[name] = []
                metric_values[name].append(m["value"])
            
            variant_results = {}
            for name, values in metric_values.items():
                variant_results[name] = {
                    "count": len(values),
                    "avg": round(sum(values) / len(values), 4) if values else 0,
                    "min": min(values) if values else 0,
                    "max": max(values) if values else 0,
                }
            
            results["variants"][variant] = variant_results
        
        return results


# ============ DEPLOYMENT MANAGER ============


class DeploymentManager:
    """
    Manages deployments and rollbacks.
    
    Features:
    - Version tracking
    - Rollback capability
    - Deployment history
    """
    
    def __init__(self, config_file: str = "config/deployments.json"):
        self.config_file = config_file
        self.deployments: List[Dict] = []
        self.current_version: Optional[str] = None
        
        # Load deployments
        self._load_deployments()
    
    def _load_deployments(self) -> None:
        """Load deployment history."""
        if os.path.exists(self.config_file):
            with open(self.config_file, "r") as f:
                data = json.load(f)
                self.deployments = data.get("deployments", [])
                self.current_version = data.get("current_version")
    
    def _save_deployments(self) -> None:
        """Save deployment history."""
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
        with open(self.config_file, "w") as f:
            json.dump({
                "deployments": self.deployments,
                "current_version": self.current_version,
            }, f, indent=2)
    
    def record_deployment(
        self,
        version: str,
        commit_hash: str,
        deployed_by: str = "system",
        notes: str = "",
    ) -> None:
        """Record a deployment."""
        deployment = {
            "version": version,
            "commit_hash": commit_hash,
            "deployed_by": deployed_by,
            "notes": notes,
            "deployed_at": datetime.now().isoformat(),
            "status": "success",
        }
        
        self.deployments.append(deployment)
        self.current_version = version
        
        self._save_deployments()
        print(f"[DEPLOY] Recorded deployment: {version}")
    
    def get_current_version(self) -> Optional[str]:
        """Get current version."""
        return self.current_version
    
    def get_deployment_history(self, limit: int = 10) -> List[Dict]:
        """Get recent deployment history."""
        return self.deployments[-limit:][::-1]
    
    def rollback(self, target_version: str) -> bool:
        """Rollback to a specific version."""
        # Find the target deployment
        target_deployment = None
        for d in self.deployments:
            if d["version"] == target_version:
                target_deployment = d
                break
        
        if not target_deployment:
            print(f"[DEPLOY] Version not found: {target_version}")
            return False
        
        # Record rollback
        rollback_deployment = {
            "version": target_version,
            "commit_hash": target_deployment["commit_hash"],
            "deployed_by": "rollback",
            "notes": f"Rollback from {self.current_version} to {target_version}",
            "deployed_at": datetime.now().isoformat(),
            "status": "rollback",
        }
        
        self.deployments.append(rollback_deployment)
        self.current_version = target_version
        
        self._save_deployments()
        print(f"[DEPLOY] Rolled back to: {target_version}")
        
        return True


# ============ SINGLETON INSTANCES ============


# Global instances
feature_flags = FeatureFlagManager()
ab_test_manager = ABTestManager()
deployment_manager = DeploymentManager()
