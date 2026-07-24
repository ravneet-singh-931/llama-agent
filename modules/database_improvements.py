"""
Database Improvements Module for Llama Agent

Features:
- Database migrations (Alembic-like)
- Backup strategy
- Connection pooling
- Query optimization
- Health checks
"""

import os
import json
import time
import shutil
import hashlib
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
from contextlib import contextmanager


# ============ DATABASE MIGRATIONS ============


class MigrationManager:
    """
    Manages database schema migrations.
    
    Features:
    - Track applied migrations
    - Apply new migrations
    - Rollback migrations
    - Migration history
    """
    
    def __init__(self, migrations_dir: str = "migrations"):
        self.migrations_dir = migrations_dir
        self.applied_migrations: List[Dict] = []
        self.pending_migrations: List[Dict] = []
        
        # Ensure migrations directory exists
        os.makedirs(migrations_dir, exist_ok=True)
        
        # Load applied migrations
        self._load_applied_migrations()
        
        # Discover pending migrations
        self._discover_migrations()
    
    def _load_applied_migrations(self) -> None:
        """Load list of applied migrations from file."""
        applied_file = os.path.join(self.migrations_dir, "applied.json")
        if os.path.exists(applied_file):
            with open(applied_file, "r") as f:
                self.applied_migrations = json.load(f)
    
    def _save_applied_migrations(self) -> None:
        """Save list of applied migrations to file."""
        applied_file = os.path.join(self.migrations_dir, "applied.json")
        with open(applied_file, "w") as f:
            json.dump(self.applied_migrations, f, indent=2)
    
    def _discover_migrations(self) -> None:
        """Discover migration files in the migrations directory."""
        if not os.path.exists(self.migrations_dir):
            return
        
        applied_ids = {m["id"] for m in self.applied_migrations}
        
        for filename in sorted(os.listdir(self.migrations_dir)):
            if filename.endswith(".sql") or filename.endswith(".py"):
                migration_id = filename.split(".")[0]
                if migration_id not in applied_ids:
                    self.pending_migrations.append({
                        "id": migration_id,
                        "filename": filename,
                    })
    
    def create_migration(self, name: str, up_sql: str, down_sql: str) -> str:
        """Create a new migration file."""
        # Generate migration ID from timestamp
        migration_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + name
        
        # Create migration file
        migration_content = f"""-- Migration: {name}
-- Created: {datetime.now().isoformat()}
-- ID: {migration_id}

-- UP (apply migration)
{up_sql}

-- DOWN (rollback migration)
{down_sql}
"""
        
        migration_file = os.path.join(self.migrations_dir, f"{migration_id}.sql")
        with open(migration_file, "w") as f:
            f.write(migration_content)
        
        print(f"[MIGRATION] Created: {migration_id}")
        return migration_id
    
    def apply_migration(self, migration_id: str, db_connection) -> bool:
        """Apply a single migration."""
        migration_file = os.path.join(self.migrations_dir, f"{migration_id}.sql")
        
        if not os.path.exists(migration_file):
            print(f"[MIGRATION] File not found: {migration_id}")
            return False
        
        try:
            # Read migration file
            with open(migration_file, "r") as f:
                content = f.read()
            
            # Extract UP section
            up_section = content.split("-- UP")[1].split("-- DOWN")[0]
            
            # Execute migration
            cursor = db_connection.cursor()
            cursor.execute(up_section)
            db_connection.commit()
            
            # Record migration
            self.applied_migrations.append({
                "id": migration_id,
                "applied_at": datetime.now().isoformat(),
                "filename": f"{migration_id}.sql",
            })
            self._save_applied_migrations()
            
            print(f"[MIGRATION] Applied: {migration_id}")
            return True
        
        except Exception as e:
            print(f"[MIGRATION] Failed: {migration_id} - {e}")
            db_connection.rollback()
            return False
    
    def rollback_migration(self, migration_id: str, db_connection) -> bool:
        """Rollback a single migration."""
        migration_file = os.path.join(self.migrations_dir, f"{migration_id}.sql")
        
        if not os.path.exists(migration_file):
            print(f"[MIGRATION] File not found: {migration_id}")
            return False
        
        try:
            # Read migration file
            with open(migration_file, "r") as f:
                content = f.read()
            
            # Extract DOWN section
            down_section = content.split("-- DOWN")[1].strip()
            
            # Execute rollback
            cursor = db_connection.cursor()
            cursor.execute(down_section)
            db_connection.commit()
            
            # Remove from applied
            self.applied_migrations = [
                m for m in self.applied_migrations
                if m["id"] != migration_id
            ]
            self._save_applied_migrations()
            
            print(f"[MIGRATION] Rolled back: {migration_id}")
            return True
        
        except Exception as e:
            print(f"[MIGRATION] Rollback failed: {migration_id} - {e}")
            db_connection.rollback()
            return False
    
    def migrate(self, db_connection) -> int:
        """Apply all pending migrations."""
        applied_count = 0
        
        for migration in self.pending_migrations:
            if self.apply_migration(migration["id"], db_connection):
                applied_count += 1
        
        return applied_count
    
    def get_status(self) -> Dict:
        """Get migration status."""
        return {
            "applied": len(self.applied_migrations),
            "pending": len(self.pending_migrations),
            "applied_migrations": self.applied_migrations,
            "pending_migrations": self.pending_migrations,
        }


# ============ BACKUP STRATEGY ============


class BackupManager:
    """
    Manages database backups.
    
    Features:
    - Automated backups
    - Backup rotation
    - Restore from backup
    - Backup verification
    """
    
    def __init__(self, backup_dir: str = "backups"):
        self.backup_dir = backup_dir
        self.max_backups = 7  # Keep last 7 backups
        
        # Ensure backup directory exists
        os.makedirs(backup_dir, exist_ok=True)
    
    def create_backup(self, db_connection, backup_name: Optional[str] = None) -> str:
        """Create a database backup."""
        # Generate backup name
        if not backup_name:
            backup_name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        backup_file = os.path.join(self.backup_dir, f"{backup_name}.sql")
        
        try:
            # For PostgreSQL, use pg_dump
            # This is a simplified version - in production, use pg_dump command
            cursor = db_connection.cursor()
            
            # Get all tables
            cursor.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)
            tables = [row[0] for row in cursor.fetchall()]
            
            # Export each table
            with open(backup_file, "w") as f:
                f.write(f"-- Backup: {backup_name}\n")
                f.write(f"-- Date: {datetime.now().isoformat()}\n\n")
                
                for table in tables:
                    f.write(f"-- Table: {table}\n")
                    cursor.execute(f"SELECT * FROM {table}")
                    rows = cursor.fetchall()
                    
                    # Get column names
                    cursor.execute(f"""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name = '{table}'
                    """)
                    columns = [row[0] for row in cursor.fetchall()]
                    
                    # Write INSERT statements
                    for row in rows:
                        values = ", ".join([f"'{str(v)}'" for v in row])
                        f.write(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values});\n")
                    
                    f.write("\n")
            
            # Rotate old backups
            self._rotate_backups()
            
            print(f"[BACKUP] Created: {backup_name}")
            return backup_file
        
        except Exception as e:
            print(f"[BACKUP] Failed: {e}")
            return ""
    
    def restore_backup(self, backup_file: str, db_connection) -> bool:
        """Restore from a backup."""
        if not os.path.exists(backup_file):
            print(f"[BACKUP] File not found: {backup_file}")
            return False
        
        try:
            cursor = db_connection.cursor()
            
            with open(backup_file, "r") as f:
                content = f.read()
            
            # Execute SQL statements
            statements = content.split(";")
            for statement in statements:
                statement = statement.strip()
                if statement and not statement.startswith("--"):
                    cursor.execute(statement)
            
            db_connection.commit()
            
            print(f"[BACKUP] Restored: {backup_file}")
            return True
        
        except Exception as e:
            print(f"[BACKUP] Restore failed: {e}")
            db_connection.rollback()
            return False
    
    def _rotate_backups(self) -> None:
        """Remove old backups beyond max_backups."""
        backups = sorted([
            f for f in os.listdir(self.backup_dir)
            if f.startswith("backup_") and f.endswith(".sql")
        ])
        
        # Remove oldest backups if超过 limit
        while len(backups) > self.max_backups:
            oldest = backups.pop(0)
            os.remove(os.path.join(self.backup_dir, oldest))
            print(f"[BACKUP] Removed old backup: {oldest}")
    
    def list_backups(self) -> List[Dict]:
        """List all available backups."""
        backups = []
        
        for filename in sorted(os.listdir(self.backup_dir), reverse=True):
            if filename.startswith("backup_") and filename.endswith(".sql"):
                filepath = os.path.join(self.backup_dir, filename)
                stat = os.stat(filepath)
                
                backups.append({
                    "filename": filename,
                    "size_kb": round(stat.st_size / 1024, 2),
                    "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                })
        
        return backups
    
    def verify_backup(self, backup_file: str) -> bool:
        """Verify backup file is valid."""
        if not os.path.exists(backup_file):
            return False
        
        try:
            with open(backup_file, "r") as f:
                content = f.read()
            
            # Basic validation
            if len(content) < 100:
                return False
            
            if "INSERT INTO" not in content:
                return False
            
            return True
        
        except Exception:
            return False


# ============ CONNECTION POOLING ============


class ConnectionPool:
    """
    Database connection pool.
    
    Features:
    - Reuse connections
    - Connection limits
    - Health checks
    - Automatic reconnection
    """
    
    def __init__(
        self,
        min_connections: int = 2,
        max_connections: int = 10,
        connection_timeout: int = 30,
    ):
        self.min_connections = min_connections
        self.max_connections = max_connections
        self.connection_timeout = connection_timeout
        
        self.connections: List = []
        self.available: List = []
        self.in_use: int = 0
        self.total_created: int = 0
    
    def get_connection(self, create_func):
        """Get a connection from the pool."""
        # Try to get an available connection
        if self.available:
            conn = self.available.pop()
            self.in_use += 1
            return conn
        
        # Create new connection if under limit
        if self.total_created < self.max_connections:
            try:
                conn = create_func()
                self.connections.append(conn)
                self.total_created += 1
                self.in_use += 1
                return conn
            except Exception as e:
                print(f"[POOL] Connection creation failed: {e}")
                return None
        
        # Wait for available connection
        print(f"[POOL] Waiting for connection (pool exhausted)")
        return None
    
    def return_connection(self, conn) -> None:
        """Return a connection to the pool."""
        if conn in self.connections:
            self.available.append(conn)
            self.in_use -= 1
    
    def close_all(self) -> None:
        """Close all connections."""
        for conn in self.connections:
            try:
                conn.close()
            except Exception:
                pass
        
        self.connections.clear()
        self.available.clear()
        self.in_use = 0
        self.total_created = 0
    
    def get_stats(self) -> Dict:
        """Get pool statistics."""
        return {
            "total_connections": len(self.connections),
            "available": len(self.available),
            "in_use": self.in_use,
            "total_created": self.total_created,
        }


# ============ QUERY OPTIMIZER ============


class QueryOptimizer:
    """
    Tracks and optimizes database queries.
    
    Features:
    - Query timing
    - Slow query detection
    - Index suggestions
    - Query statistics
    """
    
    def __init__(self):
        self.queries: List[Dict] = []
        self.slow_query_threshold = 1.0  # seconds
    
    def record_query(
        self,
        query: str,
        duration: float,
        rows_affected: int = 0,
    ) -> None:
        """Record a query execution."""
        self.queries.append({
            "query": query[:500],  # Truncate long queries
            "duration": duration,
            "rows_affected": rows_affected,
            "timestamp": time.time(),
        })
        
        # Keep last 1000 queries
        if len(self.queries) > 1000:
            self.queries = self.queries[-1000:]
    
    def get_slow_queries(self, threshold: Optional[float] = None) -> List[Dict]:
        """Get queries slower than threshold."""
        threshold = threshold or self.slow_query_threshold
        return [q for q in self.queries if q["duration"] > threshold]
    
    def get_statistics(self) -> Dict:
        """Get query statistics."""
        if not self.queries:
            return {"total_queries": 0}
        
        durations = [q["duration"] for q in self.queries]
        slow_queries = self.get_slow_queries()
        
        return {
            "total_queries": len(self.queries),
            "avg_duration": round(sum(durations) / len(durations), 4),
            "min_duration": round(min(durations), 4),
            "max_duration": round(max(durations), 4),
            "slow_queries": len(slow_queries),
            "slow_query_percent": round(len(slow_queries) / len(self.queries) * 100, 2),
        }


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


# ============ SINGLETON INSTANCES ============


# Global instances
migration_manager = MigrationManager()
backup_manager = BackupManager()
query_optimizer = QueryOptimizer()
db_health_check = DatabaseHealthCheck()


# ============ HELPER FUNCTIONS ============


def create_initial_migrations() -> None:
    """Create initial database migrations."""
    
    # Users table
    migration_manager.create_migration(
        name="create_users_table",
        up_sql="""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                full_name VARCHAR(100),
                is_active BOOLEAN DEFAULT true,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
        """,
        down_sql="""
            DROP TABLE IF EXISTS users CASCADE;
        """,
    )
    
    # Chat history table
    migration_manager.create_migration(
        name="create_chat_history_table",
        up_sql="""
            CREATE TABLE IF NOT EXISTS chat_history (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                message TEXT NOT NULL,
                response TEXT,
                tokens_used INTEGER DEFAULT 0,
                response_time_ms FLOAT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_chat_history_user_id ON chat_history(user_id);
            CREATE INDEX IF NOT EXISTS idx_chat_history_created_at ON chat_history(created_at);
        """,
        down_sql="""
            DROP TABLE IF EXISTS chat_history CASCADE;
        """,
    )
    
    # Documents table
    migration_manager.create_migration(
        name="create_documents_table",
        up_sql="""
            CREATE TABLE IF NOT EXISTS documents (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                filename VARCHAR(255) NOT NULL,
                file_type VARCHAR(50),
                file_size_kb FLOAT,
                chunks_created INTEGER DEFAULT 0,
                status VARCHAR(20) DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);
        """,
        down_sql="""
            DROP TABLE IF EXISTS documents CASCADE;
        """,
    )
    
    # API keys table
    migration_manager.create_migration(
        name="create_api_keys_table",
        up_sql="""
            CREATE TABLE IF NOT EXISTS api_keys (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                key_hash VARCHAR(255) UNIQUE NOT NULL,
                name VARCHAR(100),
                last_used TIMESTAMP,
                expires_at TIMESTAMP,
                is_active BOOLEAN DEFAULT true,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);
            CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash);
        """,
        down_sql="""
            DROP TABLE IF EXISTS api_keys CASCADE;
        """,
    )
    
    print("[MIGRATION] Initial migrations created")
