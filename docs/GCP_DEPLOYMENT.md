# ExpertAP - GCP Deployment Guide

This guide walks you through deploying ExpertAP to Google Cloud Platform (GCP).

## Project Information

- **Project Name**: ExpertAPP
- **Project Number**: 850584928584
- **Project ID**: gen-lang-client-0706147575
- **Region**: europe-west1 (Belgium)

---

## Prerequisites

1. [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) installed
2. GitHub repository with the ExpertAP code
3. A GCP account with billing enabled

---

## Step 1: Configure Google Cloud CLI

```bash
# Login to GCP
gcloud auth login

# Set the project
gcloud config set project gen-lang-client-0706147575

# Verify configuration
gcloud config list
```

---

## Step 2: Enable Required APIs

Run the following commands to enable all necessary APIs:

```bash
# Enable Cloud Build API
gcloud services enable cloudbuild.googleapis.com

# Enable Cloud Run API
gcloud services enable run.googleapis.com

# Enable Container Registry API
gcloud services enable containerregistry.googleapis.com

# Enable Artifact Registry API (recommended over Container Registry)
gcloud services enable artifactregistry.googleapis.com

# Enable Cloud SQL Admin API
gcloud services enable sqladmin.googleapis.com

# Enable Secret Manager API
gcloud services enable secretmanager.googleapis.com

# Enable Compute Engine API (required for Cloud SQL)
gcloud services enable compute.googleapis.com

# Verify enabled APIs
gcloud services list --enabled
```

---

## Step 3: Create Cloud SQL Instance (PostgreSQL with pgvector)

```bash
# Create Cloud SQL PostgreSQL instance
gcloud sql instances create expertap-db \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region=europe-west1 \
    --storage-type=SSD \
    --storage-size=10GB \
    --availability-type=zonal

# Set root password
gcloud sql users set-password postgres \
    --instance=expertap-db \
    --password=YOUR_SECURE_PASSWORD

# Create database
gcloud sql databases create expertap \
    --instance=expertap-db

# Create application user
gcloud sql users create expertap_user \
    --instance=expertap-db \
    --password=YOUR_APP_PASSWORD
```

### Enable pgvector Extension

Connect to the database and enable pgvector:

```bash
# Get instance connection name
gcloud sql instances describe expertap-db --format="value(connectionName)"
# Output: gen-lang-client-0706147575:europe-west1:expertap-db

# Connect using Cloud SQL Proxy or Cloud Shell
gcloud sql connect expertap-db --user=postgres

# In PostgreSQL prompt:
CREATE EXTENSION IF NOT EXISTS vector;
\q
```

---

## Step 4: Create Secrets in Secret Manager

```bash
# Create database URL secret
echo -n "postgresql://expertap_user:YOUR_APP_PASSWORD@/expertap?host=/cloudsql/gen-lang-client-0706147575:europe-west1:expertap-db" | \
    gcloud secrets create expertap-db-url --data-file=-

# Create Gemini API key secret
echo -n "YOUR_GEMINI_API_KEY" | \
    gcloud secrets create gemini-api-key --data-file=-

# Grant Cloud Run access to secrets
gcloud secrets add-iam-policy-binding expertap-db-url \
    --member="serviceAccount:850584928584-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding gemini-api-key \
    --member="serviceAccount:850584928584-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
```

---

## Step 5: Connect GitHub to Cloud Build

### Option A: Via Google Cloud Console (Recommended)

1. Go to [Cloud Build Triggers](https://console.cloud.google.com/cloud-build/triggers?project=gen-lang-client-0706147575)

2. Click **"Connect Repository"**

3. Select **"GitHub (Cloud Build GitHub App)"**

4. Click **"Install Google Cloud Build"** on GitHub

5. Authorize and select your repository: `NoisimRo/APP-AI`

6. Click **"Connect"**

### Option B: Via CLI (if already connected)

```bash
# List connected repositories
gcloud builds repositories list --region=europe-west1 --connection=github
```

---

## Step 6: Create Cloud Build Trigger

### Via Console:

1. Go to [Cloud Build Triggers](https://console.cloud.google.com/cloud-build/triggers?project=gen-lang-client-0706147575)

2. Click **"Create Trigger"**

3. Configure:
   - **Name**: `deploy-to-cloud-run`
   - **Event**: Push to a branch
   - **Source**: Your connected GitHub repo
   - **Branch**: `^main$` (regex for main branch)
   - **Configuration**: Cloud Build configuration file
   - **Location**: `/cloudbuild.yaml`

4. Click **"Create"**

### Via CLI:

```bash
gcloud builds triggers create github \
    --name="deploy-to-cloud-run" \
    --repo-name="APP-AI" \
    --repo-owner="NoisimRo" \
    --branch-pattern="^main$" \
    --build-config="cloudbuild.yaml" \
    --region=europe-west1
```

---

## Step 7: Grant Cloud Build Permissions

Cloud Build needs permissions to deploy to Cloud Run and access Cloud SQL:

```bash
# Get Cloud Build service account
PROJECT_NUMBER=$(gcloud projects describe gen-lang-client-0706147575 --format="value(projectNumber)")

# Grant Cloud Run Admin role
gcloud projects add-iam-policy-binding gen-lang-client-0706147575 \
    --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
    --role="roles/run.admin"

# Grant Service Account User role (to act as the runtime service account)
gcloud projects add-iam-policy-binding gen-lang-client-0706147575 \
    --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
    --role="roles/iam.serviceAccountUser"

# Grant Cloud SQL Client role
gcloud projects add-iam-policy-binding gen-lang-client-0706147575 \
    --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
    --role="roles/cloudsql.client"

# Grant Secret Manager access
gcloud projects add-iam-policy-binding gen-lang-client-0706147575 \
    --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
```

---

## Step 8: Manual First Deployment (Optional)

To test the deployment manually before setting up the trigger:

```bash
# Build and push image manually
cd backend
gcloud builds submit --tag gcr.io/gen-lang-client-0706147575/expertap-api

# Deploy to Cloud Run
gcloud run deploy expertap-api \
    --image gcr.io/gen-lang-client-0706147575/expertap-api \
    --region europe-west1 \
    --platform managed \
    --allow-unauthenticated \
    --port 8000 \
    --memory 1Gi \
    --set-env-vars "ENVIRONMENT=production" \
    --add-cloudsql-instances gen-lang-client-0706147575:europe-west1:expertap-db \
    --set-secrets "DATABASE_URL=expertap-db-url:latest,GEMINI_API_KEY=gemini-api-key:latest"
```

---

## Step 9: Verify Deployment

```bash
# Get the Cloud Run URL
gcloud run services describe expertap-api --region europe-west1 --format="value(status.url)"

# Test the API
curl https://expertap-api-XXXXX-ew.a.run.app/health
curl https://expertap-api-XXXXX-ew.a.run.app/api/v1/decisions
```

---

## CI/CD Workflow Summary

### GitHub Actions (on PR):
- Runs tests with PostgreSQL + Redis services
- Runs linting (flake8) and type checking (mypy)
- Builds Docker image (verification)
- Runs security scan (Trivy)
- **Must pass before merge**

### Cloud Build (on push to main):
1. Runs tests
2. Builds Docker image
3. Pushes to Container Registry
4. Deploys to Cloud Run

---

## Monitoring & Logs

```bash
# View Cloud Run logs
gcloud run services logs read expertap-api --region europe-west1

# Stream logs in real-time
gcloud run services logs tail expertap-api --region europe-west1

# View Cloud Build history
gcloud builds list --limit=10

# View specific build logs
gcloud builds log BUILD_ID
```

---

## Troubleshooting

### Build Fails: "Permission denied"
```bash
# Ensure Cloud Build service account has correct roles
gcloud projects get-iam-policy gen-lang-client-0706147575 \
    --filter="bindings.members:cloudbuild.gserviceaccount.com"
```

### Cloud Run: "Connection refused to Cloud SQL"
```bash
# Verify Cloud SQL instance connection name
gcloud sql instances describe expertap-db --format="value(connectionName)"

# Ensure Cloud Run service account has cloudsql.client role
```

### Secret Access Denied
```bash
# List secret access permissions
gcloud secrets get-iam-policy expertap-db-url
```

---

## Cost Optimization

- Cloud Run: Pay only for requests (min-instances=0)
- Cloud SQL: Consider db-f1-micro for development
- Container Registry: Set lifecycle policies to remove old images

---

## Security Checklist

- [ ] Cloud SQL uses private IP (optional, requires VPC connector)
- [ ] Secrets stored in Secret Manager (not env vars)
- [ ] Cloud Run uses dedicated service account
- [ ] IAM follows least-privilege principle
- [ ] Cloud Armor enabled for DDoS protection (optional)
