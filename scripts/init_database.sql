-- Initialize ExpertAP Database
-- This script sets up the database with required extensions
-- Run this after creating the Cloud SQL instance

-- Enable required PostgreSQL extensions
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector for embeddings
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- Trigram for full-text search

-- Verify extensions
SELECT * FROM pg_extension WHERE extname IN ('vector', 'pg_trgm');

-- Grant necessary permissions
GRANT ALL PRIVILEGES ON DATABASE expertap TO expertap;

-- Create schema (optional, for future organization)
-- CREATE SCHEMA IF NOT EXISTS public;
-- GRANT ALL ON SCHEMA public TO expertap;

-- Display version info
SELECT version();
SELECT extversion FROM pg_extension WHERE extname = 'vector';
SELECT extversion FROM pg_extension WHERE extname = 'pg_trgm';

\echo 'Database initialization completed successfully!'
\echo 'You can now run the import script or alembic migrations.'
