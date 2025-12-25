# ExpertAP - Quick Start Guide

Get ExpertAP up and running with database in 3 steps.

## Current Status

✅ **Deployed**: https://expertap-api-850584928584.europe-west1.run.app/
⚠️ **Database**: Not configured - running in demo mode
📦 **Data**: ~3000 CNSC decisions in GCS bucket ready to import

## What You Need

To get the full application working, you need to:

1. ✅ **Cloud Run** - Already deployed
2. ❌ **Cloud SQL** - PostgreSQL database (needs setup)
3. ❌ **Data Import** - Import CNSC decisions from GCS
4. ❌ **Connection** - Connect Cloud Run to Cloud SQL

## Setup in 3 Steps

### Step 1: Create Cloud SQL Instance (5 minutes)

```bash
# Run the automated setup script
cd /path/to/APP-AI
./scripts/setup_cloud_sql.sh

# This will:
# - Create PostgreSQL 15 instance with pgvector
# - Create database and user
# - Display connection details (save these!)
```

**Expected output:**
```
Instance Connection Name: gen-lang-client-0706147575:europe-west1:expertap-db
Database: expertap
User: expertap
Password: <generated-password>

Add these to Cloud Run:
DATABASE_URL=postgresql://expertap:<password>@/expertap?host=/cloudsql/<connection-name>
SKIP_DB=false
```

### Step 2: Connect Cloud Run to Database (2 minutes)

```bash
# Update Cloud Run with database connection
gcloud run services update expertap-api \
    --add-cloudsql-instances=gen-lang-client-0706147575:europe-west1:expertap-db \
    --update-env-vars="DATABASE_URL=postgresql://expertap:YOUR_PASSWORD@/expertap?host=/cloudsql/gen-lang-client-0706147575:europe-west1:expertap-db,SKIP_DB=false" \
    --region=europe-west1 \
    --project=gen-lang-client-0706147575

# Replace YOUR_PASSWORD with the password from Step 1
```

**Verify:**
```bash
curl https://expertap-api-850584928584.europe-west1.run.app/health
# Should show: "database": "connected"
```

### Step 3: Import CNSC Decisions (10-15 minutes)

```bash
# Install dependencies
cd backend
pip install -r requirements.txt

# Run import script
cd ..
python scripts/import_decisions_from_gcs.py --create-tables

# This will:
# - Create database tables
# - Download ~3000 decisions from GCS
# - Parse and import to database
```

**Expected output:**
```
============================================================
IMPORT SUMMARY
============================================================
Total files found: 3000
Successfully imported: 2985
Already existed: 0
Failed: 15
============================================================
```

## Verify Everything Works

1. **Check Health**:
   ```bash
   curl https://expertap-api-850584928584.europe-west1.run.app/health
   ```
   Should return: `{"status":"healthy","database":"connected"}`

2. **Test API**:
   ```bash
   curl "https://expertap-api-850584928584.europe-west1.run.app/api/v1/decisions?limit=5"
   ```
   Should return decisions list

3. **Test Frontend**:
   - Open: https://expertap-api-850584928584.europe-west1.run.app/
   - Try searching for decisions
   - Ask a question in the chat

## Common Issues

### "gcloud: command not found"

Install Google Cloud SDK:
```bash
# Linux
curl https://sdk.cloud.google.com | bash

# macOS
brew install --cask google-cloud-sdk

# Then authenticate
gcloud auth login
gcloud config set project gen-lang-client-0706147575
```

### "Permission denied"

Make sure you're authenticated:
```bash
gcloud auth login
gcloud auth application-default login
```

### "Database connection failed"

1. Check Cloud SQL is running:
   ```bash
   gcloud sql instances describe expertap-db --project=gen-lang-client-0706147575
   ```

2. Verify Cloud Run has the connection:
   ```bash
   gcloud run services describe expertap-api --region=europe-west1 --project=gen-lang-client-0706147575
   ```

3. Check logs:
   ```bash
   gcloud run services logs read expertap-api --region=europe-west1 --limit=50
   ```

### "Import failed: Connection refused"

The import script needs to run from a machine that can connect to Cloud SQL:

Option 1: Run from Cloud Shell (recommended):
```bash
# Open Cloud Shell at: https://console.cloud.google.com/
git clone <your-repo>
cd APP-AI
./scripts/import_decisions_from_gcs.py
```

Option 2: Use Cloud SQL Proxy locally:
```bash
# Download proxy
wget https://dl.google.com/cloudsql/cloud_sql_proxy.linux.amd64 -O cloud_sql_proxy
chmod +x cloud_sql_proxy

# Run proxy
./cloud_sql_proxy -instances=gen-lang-client-0706147575:europe-west1:expertap-db=tcp:5432 &

# Update DATABASE_URL to use localhost
export DATABASE_URL="postgresql://expertap:PASSWORD@localhost:5432/expertap"

# Run import
python scripts/import_decisions_from_gcs.py
```

## Next Steps

After successful setup:

1. **Generate Embeddings**: Enable semantic search
   ```bash
   python scripts/generate_embeddings.py
   ```

2. **Test All Features**:
   - Search decisions by criteria
   - Ask legal questions
   - Generate legal documents

3. **Monitor Performance**:
   ```bash
   gcloud run services logs read expertap-api --region=europe-west1 --follow
   ```

4. **Optimize Costs**:
   - Review Cloud SQL tier (currently db-f1-micro)
   - Set up auto-scaling limits
   - Configure automatic backups

## Detailed Documentation

- **Database Setup**: See `docs/SETUP_DATABASE.md`
- **Cloud Run Config**: See `docs/CLOUD_RUN_DATABASE_CONFIG.md`
- **Project Context**: See `PROJECT_CONTEXT.md`
- **Development**: See `TODO.md`

## Need Help?

- Check logs: `gcloud run services logs read expertap-api --region=europe-west1`
- Review documentation in `docs/`
- Check GitHub issues

## Cost Estimate

With current configuration:
- Cloud Run: ~$0-5/month (depends on usage)
- Cloud SQL (db-f1-micro): ~$7/month
- Cloud Storage: ~$0.50/month
- **Total: ~$8-13/month**

For production, consider upgrading Cloud SQL tier for better performance.
