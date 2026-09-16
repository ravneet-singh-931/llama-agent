-- ============================================================================
-- 📚 LEARNER GUIDE: Database Initialization — init.sql (Postgres Setup)
-- ============================================================================
-- WHAT IS THIS FILE?
-- ------------------
-- SQL script that runs when PostgreSQL starts for the FIRST time (on empty volume).
-- It sets up extensions, types, and permissions needed by the app.
-- Mounted in docker-compose.yml as: ./init.sql:/docker-entrypoint-initdb.d/init.sql:ro
-- And in k8s via init container or direct mount (if using postgres.yaml with init.sql).
--
-- WHAT DOES IT DO?
-- ----------------
-- 1. Create extensions: uuid-ossp (for UUIDs), pg_trgm (for text search)
-- 2. Create custom type: message_role (ENUM for chat roles)
-- 3. Grant permissions on database
-- Note: Tables are created by SQLAlchemy (app code), not here
--
-- WHY NEEDED?
-- -----------
-- - Extensions like pg_trgm enable efficient text search (for RAG, etc.)
-- - uuid-ossp generates UUIDs for primary keys (users, conversations, messages)
-- - Without this, app would fail to create tables or search
--
-- HOW IT CONNECTS:
-- ----------------
-- - Used by: postgres service (docker-compose.yml, k8s/postgres.yaml)
-- - Runs: Only on first start of empty data volume (if volume already has data, skipped)
-- - App code (SQLAlchemy) creates tables after this
--
-- FLOW:
-- -----
-- 1. docker compose up -d postgres (first time, empty postgres_data volume)
-- 2. Postgres container starts, sees /docker-entrypoint-initdb.d/init.sql
-- 3. Runs this SQL as postgres user
-- 4. Creates extensions, types, grants
-- 5. App (api) connects and creates tables via SQLAlchemy
-- 6. Verify: docker exec -it llama-agent-db psql -U postgres -d llama_agent -c "\dx" (list extensions)
--            docker exec -it llama-agent-db psql -U postgres -d llama_agent -c "\d" (list tables)
-- ============================================================================

-- Create extensions (if not exists, so reruns are safe)
--   - uuid-ossp: Generate UUIDs (e.g., gen_random_uuid() for primary keys)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- pg_trgm: Trigram text search (for efficient LIKE/ILIKE, similarity)
--   - Used for searching messages, documents, etc.
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- For text search

-- Create custom type for message roles (user, assistant, system)
--   - ENUM = fixed set of values (like Python Enum)
--   - Used in messages table: role message_role
--   - DO $$ ... EXCEPTION ... = Create if not exists (avoid error if already exists)
DO $$ BEGIN
    CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system');
EXCEPTION
    WHEN duplicate_object THEN null;  -- If type already exists, do nothing
END $$;

-- Note: Tables are created by SQLAlchemy migrations (app code, not here)
-- This file is for any additional setup needed beyond tables

-- Create indexes for better performance (if needed, but SQLAlchemy does this)
-- (These are created by SQLAlchemy, but we can add more here if needed)
-- Example: CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);

-- Grant all privileges on database to postgres user (so app can create tables, etc.)
--   - GRANT ALL = SELECT, INSERT, UPDATE, DELETE, CREATE, etc.
--   - ON DATABASE llama_agent = The database we created (POSTGRES_DB=llama_agent)
--   - TO postgres = The user (POSTGRES_USER=postgres)
GRANT ALL PRIVILEGES ON DATABASE llama_agent TO postgres;

-- 💡 LEARNER TIP: Postgres Extensions
--   Check extensions: docker exec -it llama-agent-db psql -U postgres -d llama_agent -c "\dx"
--   Check types: docker exec -it llama-agent-db psql -U postgres -d llama_agent -c "\dT"
--   This file only runs ONCE (on empty volume). To rerun, delete volume: docker volume rm llama-agent_postgres_data
