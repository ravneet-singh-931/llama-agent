"""
Database health checks for Llama Agent.

This module used to carry five more classes — MigrationManager, BackupManager,
ConnectionPool, QueryOptimizer and create_initial_migrations (546 of its 678
lines). None of them had a single external reference: `grep` over every non-test
Python file found zero uses, and the only thing importing them was a test.

Worse, three of them had module-level singletons that ran `os.makedirs()` at
import time, so merely importing this module created `backups/` and
`migrations/` directories in the working directory. Verified:

    $ python -c "import modules.database_improvements"
    $ ls
    backups/  migrations/

`DatabaseHealthCheck` is the module's only live export: `api.py:457` imports
`db_health_check` and runs it from `/health`. It is kept, and the rest is gone.
Deleting them removed 546 lines and one hidden import-time side effect.
"""

import time
from datetime import datetime
from typing import Dict, List

# ============ DATABASE HEALTH CHECK ============


class DatabaseHealthCheck:
    """
    Monitors database health.
    
    Checks:
    - Connection status
    - Response time
    - Disk usage
    - Connection pool status
    """
    
    def __init__(self):
        self.checks: List[Dict] = []
    
    def run_health_check(self, db_connection) -> Dict:
        """Run a comprehensive health check."""
        results = {
            "timestamp": datetime.now().isoformat(),
            "status": "healthy",
            "checks": {},
        }
        
        try:
            # Check connection
            start = time.time()
            cursor = db_connection.cursor()
            cursor.execute("SELECT 1")
            duration = time.time() - start
            
            results["checks"]["connection"] = {
                "status": "healthy",
                "response_time_ms": round(duration * 1000, 2),
            }
        
        except Exception as e:
            results["status"] = "unhealthy"
            results["checks"]["connection"] = {
                "status": "unhealthy",
                "error": str(e),
            }
        
        try:
            # Check disk usage
            cursor = db_connection.cursor()
            cursor.execute("""
                SELECT pg_database_size(current_database())
            """)
            size_bytes = cursor.fetchone()[0]
            
            results["checks"]["disk_usage"] = {
                "status": "healthy",
                "size_mb": round(size_bytes / 1024 / 1024, 2),
            }
        
        except Exception as e:
            results["checks"]["disk_usage"] = {
                "status": "unknown",
                "error": str(e),
            }
        
        # Store check result
        self.checks.append(results)
        if len(self.checks) > 100:
            self.checks = self.checks[-100:]
        
        return results
    
    def get_health_history(self, limit: int = 10) -> List[Dict]:
        """Get recent health check results."""
        return self.checks[-limit:]




# Global instance — the only export api.py uses (`from modules.database_improvements import db_health_check`)
db_health_check = DatabaseHealthCheck()
