-- ============================================
-- Database Initialization SQL
-- ============================================
-- This runs when PostgreSQL starts for the first time

-- Create extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- For text search

-- Create custom types
DO $$ BEGIN
    CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Note: Tables are created by SQLAlchemy migrations
-- This file is for any additional setup needed

-- Create indexes for better performance
-- (These are created by SQLAlchemy, but we can add more here)

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE llama_agent TO postgres;
