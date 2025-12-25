# Configuring Cloud Run with Cloud SQL

This guide shows how to update the Cloud Run deployment to connect to Cloud SQL.

## Prerequisites

- Cloud SQL instance created (see `SETUP_DATABASE.md`)
- Cloud SQL instance name: `expertap-db`
- Database credentials configured

## Option 1: Manual Update via Console

1. Go to [Cloud Run Console](https://console.cloud.google.com/run)
2. Select `expertap-api` service
3. Click "EDIT & DEPLOY NEW REVISION"
4. Go to "CONNECTIONS" tab
5. Click "ADD CONNECTION"
6. Select your Cloud SQL instance: `gen-lang-client-0706147575:europe-west1:expertap-db`
7. Go to "VARIABLES & SECRETS" tab
8. Add/Update environment variables:
   ```
   DATABASE_URL=postgresql://expertap:YOUR_PASSWORD@/expertap?host=/cloudsql/gen-lang-client-0706147575:europe-west1:expertap-db
   SKIP_DB=false
   ```
9. Click "DEPLOY"

## Option 2: Update via gcloud Command

```bash
# Set variables
PROJECT_ID="gen-lang-client-0706147575"
REGION="europe-west1"
SERVICE_NAME="expertap-api"
INSTANCE_NAME="expertap-db"
DB_USER="expertap"
DB_PASSWORD="YOUR_PASSWORD_HERE"  # Replace with your password
DB_NAME="expertap"

# Get connection name
CONNECTION_NAME=$(gcloud sql instances describe $INSTANCE_NAME \
    --project=$PROJECT_ID \
    --format="value(connectionName)")

echo "Connection name: $CONNECTION_NAME"

# Update Cloud Run service
gcloud run services update $SERVICE_NAME \
    --add-cloudsql-instances=$CONNECTION_NAME \
    --update-env-vars="DATABASE_URL=postgresql://$DB_USER:$DB_PASSWORD@/$DB_NAME?host=/cloudsql/$CONNECTION_NAME,SKIP_DB=false" \
    --region=$REGION \
    --project=$PROJECT_ID

echo "Cloud Run service updated successfully!"
```

## Option 3: Update cloudbuild.yaml (Automated)

Update the `cloudbuild.yaml` file to include Cloud SQL connection:

```yaml
# In the deploy step, update --set-env-vars to:
- '--set-env-vars'
- 'ENVIRONMENT=production,SKIP_DB=false,DEBUG=false,LOG_LEVEL=INFO,DATABASE_URL=postgresql://expertap:${_DB_PASSWORD}@/expertap?host=/cloudsql/${_CONNECTION_NAME}'
- '--add-cloudsql-instances'
- '${_CONNECTION_NAME}'
```

And add to substitutions:

```yaml
substitutions:
  _SERVICE_NAME: expertap-api
  _REGION: europe-west1
  _CONNECTION_NAME: gen-lang-client-0706147575:europe-west1:expertap-db
  _DB_PASSWORD: ${_DB_PASSWORD}  # Set in Cloud Build trigger settings
```

## Verify Connection

After deployment, check logs:

```bash
gcloud run services logs read expertap-api \
    --region=europe-west1 \
    --project=gen-lang-client-0706147575 \
    --limit=50
```

Look for:
- ✅ `database_connection_initialized` - Success
- ❌ `database_connection_failed` - Check credentials/connection

## Test Application

```bash
# Health check
curl https://expertap-api-850584928584.europe-west1.run.app/health

# Should return:
# {"status":"healthy","database":"connected","timestamp":"..."}

# Test API
curl https://expertap-api-850584928584.europe-west1.run.app/api/v1/decisions?limit=10
```

## Troubleshooting

### Error: "Cloud SQL instance not found"

Make sure the Cloud SQL instance exists:

```bash
gcloud sql instances describe expertap-db --project=gen-lang-client-0706147575
```

### Error: "Connection refused"

1. Check Cloud Run has the cloudsql-instances connection
2. Verify DATABASE_URL format is correct
3. Check Cloud SQL instance is running

### Error: "Password authentication failed"

1. Reset database password:
   ```bash
   gcloud sql users set-password expertap \
       --instance=expertap-db \
       --password=NEW_PASSWORD \
       --project=gen-lang-client-0706147575
   ```
2. Update DATABASE_URL with new password

## Security Notes

⚠️ **NEVER** commit database passwords to git!

Instead:
1. Use Cloud Secret Manager for passwords
2. Or set as Cloud Build substitution variables (encrypted)
3. Or use IAM authentication (recommended for production)

### Using Secret Manager (Recommended)

```bash
# Create secret
echo -n "YOUR_DB_PASSWORD" | gcloud secrets create db-password --data-file=- --project=gen-lang-client-0706147575

# Grant Cloud Run access
gcloud secrets add-iam-policy-binding db-password \
    --member="serviceAccount:850584928584-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor" \
    --project=gen-lang-client-0706147575

# Update Cloud Run to use secret
gcloud run services update expertap-api \
    --update-secrets=DB_PASSWORD=db-password:latest \
    --region=europe-west1 \
    --project=gen-lang-client-0706147575
```

Then reference in DATABASE_URL as: `${DB_PASSWORD}`
