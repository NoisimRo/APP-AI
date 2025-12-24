-- ExpertAP Database Initialization Script
-- This script runs when the PostgreSQL container starts

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- For fuzzy text search

-- Create custom types (if needed)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ruling_type') THEN
        CREATE TYPE ruling_type AS ENUM ('ADMIS', 'RESPINS', 'PARTIAL');
    END IF;
END $$;

-- Note: Tables are created by SQLAlchemy/Alembic migrations
-- This script only sets up extensions and initial configuration

-- Performance settings for vector search
-- (These can be tuned based on data size)
-- ALTER SYSTEM SET max_parallel_workers_per_gather = 4;
-- ALTER SYSTEM SET effective_cache_size = '4GB';
