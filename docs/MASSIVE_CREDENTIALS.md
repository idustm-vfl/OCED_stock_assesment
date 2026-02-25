# Massive Data Platform Credentials

## Overview

The OCED Stock Assessment system uses **two separate sets of credentials** for accessing Massive data platform:

1. **REST API Credentials** - for real-time market data queries
2. **S3 Credentials** - for bulk flatfile downloads

## Credential Types

### 1. REST API Access (Real-time Data)

**Environment Variable**: `MASSIVE_API_KEY`

**Used For**:
- `/v2/last/trade/{ticker}` - Live stock prices
- `/v2/last/nbbo/{ticker}` - Bid/ask quotes
- `/v2/aggs/ticker/{ticker}/range/...` - Historical OHLCV bars
- `/v2/snapshot/options/{ticker}/{expiry}` - Option chains
- WebSocket streaming (`wss://delayed.massive.com/stocks`)

**Example**:
```bash
export MASSIVE_API_KEY="zwGIF..."  # 32-character key
```

**Used By**:
- `massive_tracker/massive_client.py` - All REST endpoints
- `massive_tracker/ws_client.py` - WebSocket streaming
- `massive_tracker/monitor.py` - Position monitoring
- `massive_tracker/picker.py` - Price fetching
- `massive_tracker/oced.py` - Historical data

---

### 2. S3 Flatfile Access (Bulk Downloads)

**Environment Variables**:
- `MASSIVE_KEY_ID` - S3 access key ID (36 characters)
- `MASSIVE_SECRET_KEY` - S3 secret access key (32 characters)

**Used For**:
- `s3://flatfiles/us_stocks_sip/day_aggs_v1/` - Daily stock data
- `s3://flatfiles/us_options_opra/day_aggs_v1/` - Daily option data

**Example**:
```bash
export MASSIVE_KEY_ID="3ffec..."      # 36-character S3 access key
export MASSIVE_SECRET_KEY="wB3aH..."  # 32-character S3 secret key
```

**Used By**:
- `massive_tracker/ingest.py` - Daily flatfile downloads
- `massive_tracker/s3_flatfiles.py` - S3 client wrapper
- `massive_tracker/flatfiles.py` - Flatfile processing

---

## Configuration Setup

### Development Environment

```bash
# REST API (required for all operations)
export MASSIVE_API_KEY="your-rest-api-key"

# S3 Access (optional, for flatfile bootstrap)
export MASSIVE_KEY_ID="your-s3-access-key-id"
export MASSIVE_SECRET_KEY="your-s3-secret-key"

# Optional: S3 endpoint override
export MASSIVE_S3_ENDPOINT="https://files.massive.com"
export MASSIVE_S3_BUCKET="flatfiles"
```

### Production with Google Cloud Secret Manager

```bash
export GCP_PROJECT_ID="310466067504"
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/credentials.json"

# Secrets automatically loaded from GCP:
# - MASSIVE_API_KEY
# - MASSIVE_KEY_ID
# - MASSIVE_SECRET_KEY
```

See [GCP_SECRETS_INTEGRATION.md](GCP_SECRETS_INTEGRATION.md) for details.

---

## Fallback Credentials

For AWS compatibility, the system also accepts:

- `AWS_ACCESS_KEY_ID` → falls back to `MASSIVE_KEY_ID`
- `AWS_SECRET_ACCESS_KEY` → falls back to `MASSIVE_SECRET_KEY`

---

## Verification

Check your credentials are loaded correctly:

```bash
python -c "from massive_tracker.config import print_key_status; print_key_status()"
```

Expected output:
```
MASSIVE_API_KEY: zwGIF*****
MASSIVE_SECRET_KEY: wB3aH*****
MASSIVE_KEY_ID: 3ffec*****
```

---

## Troubleshooting

### REST API Returns 401 "Unknown API Key"

**Symptom**: 
```
{"status":"ERROR","error":"Unknown API Key"}
```

**Solution**:
1. Verify `MASSIVE_API_KEY` is set correctly
2. Check the key is active in Massive dashboard
3. Confirm the key has REST API entitlements
4. Contact Massive support to enable API access

### S3 Returns 403 Forbidden

**Symptom**:
```
[skip] not available: s3://flatfiles/... (403)
```

**Solution**:
1. Verify `MASSIVE_KEY_ID` and `MASSIVE_SECRET_KEY` are set
2. Confirm S3/flatfile access is enabled for your account
3. Contact Massive support to enable S3 flatfile access

### Wrong Credentials Used

If the system is using the wrong key for an operation:

```bash
# Debug config loading
export VFL_DEBUG_CONFIG=1
python -m massive_tracker.cli run
```

This will show which keys are being loaded from which environment variables.

---

## Security Best Practices

1. **Never commit credentials** to version control
2. **Use .env files** for local development (git-ignored)
3. **Use Secret Manager** for production (GCP/AWS/Azure)
4. **Rotate keys regularly** (every 90 days recommended)
5. **Use separate keys** for dev/staging/production environments
6. **Audit key usage** via Massive dashboard logs

---

## Related Documentation

- [config.py](../massive_tracker/config.py) - Configuration loader
- [secrets.py](../massive_tracker/secrets.py) - GCP Secret Manager integration
- [GCP_SECRETS_INTEGRATION.md](GCP_SECRETS_INTEGRATION.md) - Cloud secret management
- [DATA_STORAGE_ARCHITECTURE.md](DATA_STORAGE_ARCHITECTURE.md) - Data flow and storage
