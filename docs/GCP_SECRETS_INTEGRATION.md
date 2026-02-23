# Google Cloud Secret Manager Integration

## Overview

The OCED Stock Assessment system now supports optional integration with **Google Cloud Secret Manager** for secure credential management in production environments.

## Benefits

✅ **No hardcoded secrets** in environment or code  
✅ **Centralized secret management** - manage all keys in one place  
✅ **Automatic rotation support** - update secrets without redeploying  
✅ **Audit trail** - GCP logs all access to secrets  
✅ **Graceful fallback** - works with or without GCP  
✅ **Zero code changes** - transparent to application logic  

## Quick Start

### 1. Install GCP Secret Manager client (optional)

```bash
pip install google-cloud-secret-manager
```

### 2. Set up credentials

```bash
export GCP_PROJECT_ID="310466067504"
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"
```

### 3. Create secrets in GCP Console

Go to **Secret Manager** → **Create Secret** and add:
- `MASSIVE_API_KEY`
- `MASSIVE_ACCESS_KEY`
- `MASSIVE_SECRET_KEY`
- `MASSIVE_KEY_ID`
- `MASSIVE_S3_ENDPOINT`
- `MASSIVE_S3_BUCKET`
- etc.

### 4. Run application

```bash
python -m massive_tracker.cli run
# Automatically loads secrets from GCP
```

## How It Works

### Load Order

1. **Check for GCP_PROJECT_ID** env var
2. **Try GCP Secret Manager** (if google-cloud-secret-manager installed)
3. **Fall back to environment variables** (always works)
4. **Fail gracefully** if neither available

### Configuration Examples

#### Example 1: Development (env vars only)

```bash
export MASSIVE_API_KEY="your-api-key"
export MASSIVE_ACCESS_KEY="your-access-key"
python -m massive_tracker.cli run
```

#### Example 2: Production (GCP Secret Manager)

```bash
export GCP_PROJECT_ID="310466067504"
export GOOGLE_APPLICATION_CREDENTIALS="/secure/credentials.json"
python -m massive_tracker.cli run
# No env vars needed - all loaded from GCP
```

#### Example 3: Hybrid (GCP primary, env fallback)

```bash
export GCP_PROJECT_ID="310466067504"
export GOOGLE_APPLICATION_CREDENTIALS="/secure/credentials.json"
export MASSIVE_API_KEY="fallback-key"  # Used if GCP fails

python -m massive_tracker.cli run
```

## API Reference

### get_secret()

Retrieve a single secret with fallback to environment variable.

```python
from massive_tracker.secrets import get_secret

# Load from GCP, fall back to env var
api_key = get_secret(
    "MASSIVE_API_KEY",
    project_id="310466067504",
    fallback_env_var="MASSIVE_API_KEY"  # defaults to secret_id
)
```

### load_runtime_config_with_gcp()

Load entire config from GCP with fallback.

```python
from massive_tracker.secrets import load_runtime_config_with_gcp

config = load_runtime_config_with_gcp(project_id="310466067504")
api_key = config.get("MASSIVE_API_KEY")
s3_bucket = config.get("MASSIVE_S3_BUCKET")
```

### bootstrap_env_from_gcp()

Bootstrap all environment variables from GCP at startup.

```python
# Call BEFORE importing config.py
from massive_tracker.secrets import bootstrap_env_from_gcp

bootstrap_env_from_gcp(project_id="310466067504")

# Now config.py will use GCP secrets
from massive_tracker.config import CFG
print(CFG.massive_api_key)  # Loaded from GCP
```

## Deployment Examples

### Docker with GCP

```dockerfile
FROM python:3.11

ENV GCP_PROJECT_ID=310466067504
COPY credentials.json /secure/credentials.json
ENV GOOGLE_APPLICATION_CREDENTIALS=/secure/credentials.json

COPY . /app
WORKDIR /app
RUN pip install -r requirements.txt && \
    pip install google-cloud-secret-manager

CMD ["python", "-m", "massive_tracker.cli", "run"]
```

### Kubernetes with Secret

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: gcp-credentials
type: Opaque
data:
  credentials.json: <base64-encoded-credentials>
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: oced-tracker
spec:
  template:
    spec:
      containers:
      - name: tracker
        image: oced-tracker:latest
        env:
        - name: GCP_PROJECT_ID
          value: "310466067504"
        - name: GOOGLE_APPLICATION_CREDENTIALS
          value: "/etc/gcp/credentials.json"
        volumeMounts:
        - name: gcp-credentials
          mountPath: /etc/gcp
      volumes:
      - name: gcp-credentials
        secret:
          secretName: gcp-credentials
```

### GitHub Actions

```yaml
name: Run OCED Tracker
on: [schedule, push]

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: google-github-actions/auth@v1
        with:
          credentials_json: ${{ secrets.GCP_CREDENTIALS }}
      - uses: google-github-actions/setup-gcloud@v1
      - run: |
          export GCP_PROJECT_ID="310466067504"
          pip install -r requirements.txt google-cloud-secret-manager
          python -m massive_tracker.cli run
```

## Troubleshooting

### Secret not found

```
[GCP Secret Manager] Failed to load MASSIVE_API_KEY: NotFound
```

**Solution**: Verify secret exists in GCP Console and project ID is correct.

### Permission denied

```
[GCP Secret Manager] Failed to load MASSIVE_API_KEY: PermissionDenied
```

**Solution**: Verify service account has `roles/secretmanager.secretAccessor` permission.

### Falling back to env var

If GCP fails, check logs for why:
```bash
export VFL_DEBUG_CONFIG=1
python -m massive_tracker.cli run
```

## Best Practices

1. **Use service accounts** - Create dedicated SA for application
2. **Rotate keys regularly** - Update secrets in GCP Console
3. **Principle of least privilege** - Only grant needed permissions
4. **Audit access** - Monitor GCP Cloud Audit Logs
5. **Keep credentials secure** - Protect service account key file
6. **Document fallbacks** - Document env var fallbacks for troubleshooting

## No Changes Needed to Application Logic

The integration is **completely transparent**:

```python
# Application code unchanged
from massive_tracker.config import CFG
api_key = CFG.massive_api_key

# Automatically loaded from:
# 1. GCP Secret Manager (if GCP_PROJECT_ID set)
# 2. Environment variables (fallback)
```

No code modifications required in `monitor.py`, `picker.py`, `oced.py`, etc.

## Related Files

- [massive_tracker/secrets.py](../massive_tracker/secrets.py) - Secret management module
- [massive_tracker/config.py](../massive_tracker/config.py) - Configuration loader (with GCP support)
- [requirements.txt](../requirements.txt) - Optional dependency listed
