#!/bin/bash
# Setup Cloud SQL for ExpertAP
# This script creates and configures a Cloud SQL PostgreSQL instance with pgvector

set -e

# Configuration
PROJECT_ID="${GCP_PROJECT_ID:-gen-lang-client-0706147575}"
REGION="${GCP_REGION:-europe-west1}"
INSTANCE_NAME="${CLOUD_SQL_INSTANCE:-expertap-db}"
DB_VERSION="POSTGRES_15"
TIER="${DB_TIER:-db-f1-micro}"  # Smallest tier for MVP
DB_NAME="expertap"
DB_USER="expertap"

echo "=================================================="
echo "Setting up Cloud SQL for ExpertAP"
echo "=================================================="
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Instance: $INSTANCE_NAME"
echo "=================================================="

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "ERROR: gcloud CLI is not installed"
    echo "Install from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Set project
echo "Setting GCP project..."
gcloud config set project "$PROJECT_ID"

# Check if instance already exists
if gcloud sql instances describe "$INSTANCE_NAME" --project="$PROJECT_ID" &> /dev/null; then
    echo "Instance $INSTANCE_NAME already exists"
    read -p "Do you want to delete and recreate it? (yes/no): " RECREATE
    if [ "$RECREATE" = "yes" ]; then
        echo "Deleting existing instance..."
        gcloud sql instances delete "$INSTANCE_NAME" --project="$PROJECT_ID" --quiet
    else
        echo "Using existing instance"
        exit 0
    fi
fi

# Create Cloud SQL instance
echo "Creating Cloud SQL instance (this may take several minutes)..."
gcloud sql instances create "$INSTANCE_NAME" \
    --project="$PROJECT_ID" \
    --database-version="$DB_VERSION" \
    --tier="$TIER" \
    --region="$REGION" \
    --network=default \
    --no-assign-ip \
    --database-flags=cloudsql.enable_pgvector=on \
    --backup-start-time="03:00" \
    --maintenance-window-day=SUN \
    --maintenance-window-hour=4

echo "Instance created successfully!"

# Generate random password
DB_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)

# Create database
echo "Creating database..."
gcloud sql databases create "$DB_NAME" \
    --instance="$INSTANCE_NAME" \
    --project="$PROJECT_ID"

# Create user
echo "Creating database user..."
gcloud sql users create "$DB_USER" \
    --instance="$INSTANCE_NAME" \
    --project="$PROJECT_ID" \
    --password="$DB_PASSWORD"

# Get instance connection name
CONNECTION_NAME=$(gcloud sql instances describe "$INSTANCE_NAME" \
    --project="$PROJECT_ID" \
    --format="value(connectionName)")

echo ""
echo "=================================================="
echo "Cloud SQL setup completed!"
echo "=================================================="
echo "Instance Connection Name: $CONNECTION_NAME"
echo "Database: $DB_NAME"
echo "User: $DB_USER"
echo "Password: $DB_PASSWORD"
echo ""
echo "Add these to your Cloud Run environment variables:"
echo "DATABASE_URL=postgresql://$DB_USER:$DB_PASSWORD@/$DB_NAME?host=/cloudsql/$CONNECTION_NAME"
echo "SKIP_DB=false"
echo ""
echo "Or save to .env file:"
echo "echo 'DATABASE_URL=postgresql://$DB_USER:$DB_PASSWORD@/$DB_NAME?host=/cloudsql/$CONNECTION_NAME' >> .env"
echo "echo 'SKIP_DB=false' >> .env"
echo "=================================================="

# Enable pgvector extension (requires connection)
echo ""
echo "To enable pgvector extension, run this SQL command:"
echo "CREATE EXTENSION IF NOT EXISTS vector;"
echo ""
echo "You can connect to the database using:"
echo "gcloud sql connect $INSTANCE_NAME --user=$DB_USER --database=$DB_NAME --project=$PROJECT_ID"
