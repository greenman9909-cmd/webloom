import hashlib
import os
from urllib.parse import unquote, urlparse


def query_variant_relpath(target_url: str) -> str:
    """Map a URL to a safe local path while keeping query variants unique."""
    parsed = urlparse(target_url)
    decoded = unquote(parsed.path).lstrip("/") or "index.html"

    norm = os.path.normpath(decoded)
    if norm.startswith("..") or os.path.isabs(norm):
        norm = norm.replace("..", "").lstrip(os.sep)

    if parsed.query:
        digest = hashlib.sha256(parsed.query.encode("utf-8")).hexdigest()[:12]
        directory, filename = os.path.split(norm)
        stem, ext = os.path.splitext(filename)

        if not stem:
            stem = filename or "asset"
            ext = ""

        filename = f"{stem}__q_{digest}{ext}"
        norm = os.path.join(directory, filename)

    return norm
