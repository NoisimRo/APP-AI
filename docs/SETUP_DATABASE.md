# Database Setup Guide

This guide explains how to set up Cloud SQL PostgreSQL with pgvector for ExpertAP and import the CNSC decisions data.

## Prerequisites

- Google Cloud SDK (`gcloud`) installed and authenticated
- Access to GCP project `gen-lang-client-0706147575`
- Access to GCS bucket `date-ap-raw/decizii-cnsc`

## Step 1: Install Google Cloud SDK

If you don't have gcloud installed:

```bash
# For Linux
curl https://sdk.cloud.google.com | bash
exec -l $SHELL

# For macOS
brew install --cask google-cloud-sdk

# Authenticate
gcloud auth login
gcloud config set project gen-lang-client-0706147575
```

## Step 2: Create Cloud SQL Instance

Run the automated setup script:

```bash
./scripts/setup_cloud_sql.sh
```

This script will:
1. Create a PostgreSQL 15 instance with pgvector enabled
2. Create the `expertap` database
3. Create a database user with a secure password
4. Display connection details

**Important**: Save the connection details shown at the end!

### Manual Setup (Alternative)

If you prefer manual setup:

```bash
# Set variables
PROJECT_ID="gen-lang-client-0706147575"
INSTANCE_NAME="expertap-db"
REGION="europe-west1"

# Create instance
gcloud sql instances create $INSTANCE_NAME \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region=$REGION \
    --database-flags=cloudsql.enable_pgvector=on \
    --project=$PROJECT_ID

# Create database
gcloud sql databases create expertap \
    --instance=$INSTANCE_NAME \
    --project=$PROJECT_ID

# Create user (replace with your password)
gcloud sql users create expertap \
    --instance=$INSTANCE_NAME \
    --password=YOUR_SECURE_PASSWORD \
    --project=$PROJECT_ID
```

## Step 3: Enable pgvector Extension

Connect to the database and enable the pgvector extension:

```bash
gcloud sql connect expertap-db --user=expertap --database=expertap --project=gen-lang-client-0706147575
```

Then run:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
\q
```

## Step 4: Configure Cloud Run Connection

Update your Cloud Run service to connect to Cloud SQL:

```bash
# Get connection name
CONNECTION_NAME=$(gcloud sql instances describe expertap-db \
    --project=gen-lang-client-0706147575 \
    --format="value(connectionName)")

# Update Cloud Run service
gcloud run services update expertap-api \
    --add-cloudsql-instances=$CONNECTION_NAME \
    --update-env-vars="DATABASE_URL=postgresql://expertap:YOUR_PASSWORD@/expertap?host=/cloudsql/$CONNECTION_NAME" \
    --update-env-vars="SKIP_DB=false" \
    --region=europe-west1 \
    --project=gen-lang-client-0706147575
```

## Step 5: Create Database Tables

The tables will be created automatically on first run, or you can create them manually:

```bash
# Using the import script with --create-tables flag
python scripts/import_decisions_from_gcs.py --create-tables --limit 0

# Or using alembic migrations
cd backend
alembic upgrade head
```

## Step 6: Import CNSC Decisions

Import all ~3000 decisions from GCS bucket:

```bash
# Full import
python scripts/import_decisions_from_gcs.py

# Test with limited import (first 10 files)
python scripts/import_decisions_from_gcs.py --limit 10

# Import with table creation
python scripts/import_decisions_from_gcs.py --create-tables
```

Options:
- `--bucket`: GCS bucket name (default: `date-ap-raw`)
- `--folder`: Folder in bucket (default: `decizii-cnsc`)
- `--project`: GCP project ID (default: `gen-lang-client-0706147575`)
- `--limit`: Limit number of files (for testing)
- `--batch-size`: Batch size for commits (default: 50)
- `--skip-embeddings`: Skip embedding generation
- `--create-tables`: Create database tables before importing

### Monitor Import Progress

The script will display progress:

```
2025-12-25 10:00:00 - import_starting - total_files: 3000
2025-12-25 10:00:05 - batch_committed - batch_num: 1, processed: 50, total: 3000
2025-12-25 10:00:10 - batch_committed - batch_num: 2, processed: 100, total: 3000
...
```

### Import Summary

At the end, you'll see:

```
============================================================
IMPORT SUMMARY
============================================================
Total files found: 3000
Successfully imported: 2985
Already existed: 0
Failed: 15

Errors (15):
  - BO2024_1234_R2_CPV_55520000-1_A.txt: Invalid date format
  ...
============================================================
```

## Step 7: Verify Data

Connect to the database and verify:

```sql
-- Count decisions
SELECT COUNT(*) FROM decizii_cnsc;

-- Check by type
SELECT tip_contestatie, COUNT(*)
FROM decizii_cnsc
GROUP BY tip_contestatie;

-- Check by solution
SELECT solutie_contestatie, COUNT(*)
FROM decizii_cnsc
GROUP BY solutie_contestatie;

-- Check criticism codes
SELECT UNNEST(coduri_critici) as cod, COUNT(*)
FROM decizii_cnsc
GROUP BY cod
ORDER BY COUNT(*) DESC;
```

## Step 8: Test Application

Test the application locally or in Cloud Run:

```bash
# Test health endpoint
curl https://expertap-api-850584928584.europe-west1.run.app/health

# Test API docs
open https://expertap-api-850584928584.europe-west1.run.app/docs

# Test frontend
open https://expertap-api-850584928584.europe-west1.run.app/
```

## Troubleshooting

### Connection Issues

If you can't connect to Cloud SQL:

1. Check Cloud SQL instance is running:
   ```bash
   gcloud sql instances describe expertap-db --project=gen-lang-client-0706147575
   ```

2. Verify Cloud Run has the cloudsql-instances connection:
   ```bash
   gcloud run services describe expertap-api --region=europe-west1 --project=gen-lang-client-0706147575
   ```

3. Check logs:
   ```bash
   gcloud run services logs read expertap-api --region=europe-west1 --project=gen-lang-client-0706147575
   ```

### Import Issues

If import fails:

1. Check GCS bucket access:
   ```bash
   gsutil ls gs://date-ap-raw/decizii-cnsc/ | head
   ```

2. Test with a single file:
   ```bash
   python scripts/import_decisions_from_gcs.py --limit 1
   ```

3. Check database connection:
   ```bash
   gcloud sql connect expertap-db --user=expertap --database=expertap --project=gen-lang-client-0706147575
   ```

### pgvector Not Enabled

If you get "extension 'vector' does not exist":

```sql
-- Connect to database
gcloud sql connect expertap-db --user=expertap --database=expertap --project=gen-lang-client-0706147575

-- Enable extension
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

## Cost Optimization

The default setup uses `db-f1-micro` (smallest tier) which costs ~$7/month.

To reduce costs further:
- Use automatic backups only (not continuous)
- Schedule instance to stop during non-working hours
- Use committed use discounts

## Next Steps

After successful import:

1. **Generate Embeddings**: Run embedding generation for semantic search
2. **Create Indexes**: Optimize query performance
3. **Test Frontend**: Verify all frontend features work
4. **Monitor Performance**: Check query performance and optimize

See `TODO.md` for the complete roadmap.
