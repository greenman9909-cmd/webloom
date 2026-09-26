import os
from typing import Any, Dict

import requests


FUSION_URL = os.environ.get("WEBLOOM_FUSION_URL", "").rstrip("/")
FUSION_TOKEN = os.environ.get("WEBLOOM_FUSION_TOKEN", "")


def fusion_ready() -> bool:
    return bool(FUSION_URL)


def fusion_request(path: str, payload: Dict[str, Any], timeout: int = 180) -> Dict[str, Any]:
    if not FUSION_URL:
        raise RuntimeError("WebLoom Fusion is not configured.")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if FUSION_TOKEN:
        headers["Authorization"] = f"Bearer {FUSION_TOKEN}"
    response = requests.post(
        FUSION_URL + "/" + path.lstrip("/"),
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError("Fusion returned a non-JSON response.") from exc
    if not response.ok or not data.get("ok"):
        raise RuntimeError(data.get("error") or f"Fusion request failed with HTTP {response.status_code}.")
    return data
