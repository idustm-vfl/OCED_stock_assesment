"""
Optional Google Cloud Secret Manager integration.

Allows loading secrets from GCP Secret Manager with fallback to environment variables.
Gracefully degrades if google-cloud-secret-manager is not installed.

Usage:
    from massive_tracker.secrets import get_secret

    api_key = get_secret(
        "MASSIVE_API_KEY",
        project_id="310466067504",
        fallback_env_var="MASSIVE_API_KEY"
    )
"""

from __future__ import annotations

import os
from typing import Optional

try:
    from google.cloud import secretmanager
    HAS_GCP_SECRET_MANAGER = True
except ImportError:
    HAS_GCP_SECRET_MANAGER = False


def get_secret(
    secret_id: str,
    project_id: Optional[str] = None,
    fallback_env_var: Optional[str] = None,
) -> Optional[str]:
    """
    Retrieve a secret from Google Cloud Secret Manager with fallback to environment.

    Args:
        secret_id: The secret name (e.g., "MASSIVE_API_KEY")
        project_id: GCP project ID. If None, uses GCP_PROJECT_ID env var
        fallback_env_var: Env var name to fall back to. If None, uses secret_id

    Returns:
        Secret value or None if not found anywhere

    Examples:
        # Load from GCP, fall back to env var
        api_key = get_secret("MASSIVE_API_KEY", project_id="310466067504")

        # Load from GCP, fall back to different env var
        token = get_secret(
            "api_token",
            project_id="my-project",
            fallback_env_var="AUTH_TOKEN"
        )
    """
    fallback_var = fallback_env_var or secret_id

    # Try GCP Secret Manager first (if available and project_id provided)
    if HAS_GCP_SECRET_MANAGER and project_id:
        try:
            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
            response = client.access_secret_version(request={"name": name})
            value = response.payload.data.decode("UTF-8")
            if value:
                return value
        except Exception as e:
            # Log but don't raise - we'll try fallback
            print(f"[GCP Secret Manager] Failed to load {secret_id}: {type(e).__name__}")

    # Fall back to environment variable
    value = os.getenv(fallback_var)
    if value:
        return value

    return None


def load_runtime_config_with_gcp(
    project_id: Optional[str] = None,
) -> dict:
    """
    Load runtime config from GCP Secret Manager with env var fallback.

    Args:
        project_id: GCP project ID. If None, uses GCP_PROJECT_ID env var

    Returns:
        Dict with loaded secrets

    Example:
        config = load_runtime_config_with_gcp(project_id="310466067504")
        api_key = config.get("MASSIVE_API_KEY")
    """
    project_id = project_id or os.getenv("GCP_PROJECT_ID")

    secrets_to_load = [
        "MASSIVE_API_KEY",
        "MASSIVE_ACCESS_KEY",
        "MASSIVE_SECRET_KEY",
        "MASSIVE_KEY_ID",
        "MASSIVE_S3_ENDPOINT",
        "MASSIVE_S3_BUCKET",
        "MASSIVE_STOCKS_PREFIX",
        "MASSIVE_OPTIONS_PREFIX",
    ]

    config = {}
    for secret_id in secrets_to_load:
        value = get_secret(secret_id, project_id=project_id)
        if value:
            config[secret_id] = value

    return config


def bootstrap_env_from_gcp(project_id: Optional[str] = None) -> None:
    """
    Bootstrap environment variables from GCP Secret Manager.

    Loads all secrets and sets them as environment variables.
    Useful for initializing before importing config.py.

    Args:
        project_id: GCP project ID. If None, uses GCP_PROJECT_ID env var

    Example:
        # Call at app startup, before importing config
        from massive_tracker.secrets import bootstrap_env_from_gcp
        bootstrap_env_from_gcp(project_id="310466067504")

        from massive_tracker.config import CFG
        # CFG now has secrets from GCP
    """
    config = load_runtime_config_with_gcp(project_id=project_id)
    for key, value in config.items():
        os.environ[key] = value
    print(f"[GCP Secrets] Bootstrapped {len(config)} env vars from Secret Manager")
