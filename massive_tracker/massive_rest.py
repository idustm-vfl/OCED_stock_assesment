from __future__ import annotations

import requests
from .config import CFG


def _mask(val: str | None) -> str:
    if not val:
        return "None"
    return val[:5] + "*****"


class MassiveREST:
    def __init__(self, base: str | None = None, api_key: str | None = None):
        self.base = (base or CFG.rest_base or "https://api.massive.com").rstrip("/")
        self.api_key = api_key or CFG.massive_api_key

    def _get(self, path_or_url: str, params: dict | None = None) -> dict:
        url = path_or_url
        if not url.startswith("http"):
            url = self.base + path_or_url
        headers = {"Authorization": f"Bearer {self.api_key}"}
        print(f"[MASSIVE REST] endpoint={url} key={_mask(self.api_key)}")
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"[MASSIVE REST ERROR] endpoint={url} key={_mask(self.api_key)} status={resp.status_code}")
        resp.raise_for_status()
        return resp.json()

    def get_options_contracts(self, **kwargs) -> list[dict]:
        params = {k: v for k, v in kwargs.items() if v is not None}
        results: list[dict] = []
        data = self._get("/v3/reference/options/contracts", params=params)

        results.extend(data.get("results", []) or [])
        next_url = data.get("next_url")

        safety = 0
        while next_url and safety < 50:
            data = self._get(next_url)
            results.extend(data.get("results", []) or [])
            next_url = data.get("next_url")
            safety += 1

        return results

    def get_option_chain_snapshot(self, *, underlying: str, expiry: str) -> dict:
        params = {"underlying": underlying.upper().strip(), "expiration": expiry}
        return self._get("/v3/options/chain/snapshot", params=params)
