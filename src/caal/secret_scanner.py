"""Lightweight secret detection and scrubbing."""

from __future__ import annotations

import base64
import re
from typing import Iterable, Tuple

# Common credential-like patterns. This is intentionally lightweight and offline.
_PATTERNS: dict[str, re.Pattern[str]] = {
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "aws_secret": re.compile(r"(?i)aws(.{0,20})?(secret|key)['\"][=:]?\s*([A-Za-z0-9/+=]{40})"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "api_key": re.compile(r"(?i)(api|token|key)[\"'=:\s]{1,5}([A-Za-z0-9\-_]{20,})"),
    "private_key": re.compile(r"-----BEGIN (?:RSA|DSA|EC|OPENSSH|PGP) PRIVATE KEY-----"),
    "ssh_key": re.compile(r"ssh-rsa\s+[A-Za-z0-9+/]+={0,3}\s+[^\s]+"),
}


def _looks_high_entropy(token: str) -> bool:
    """Crude entropy heuristic to catch random-looking tokens."""
    if len(token) < 24:
        return False
    try:
        # Reject if it decodes cleanly to ascii (likely not a secret)
        base64.b64decode(token + "==")
    except Exception:
        pass
    # Unique character ratio heuristic
    unique_ratio = len(set(token)) / max(len(token), 1)
    return unique_ratio > 0.55


def scan(text: str) -> Iterable[Tuple[str, str]]:
    """Yield (kind, value) for each detected secret-like token."""
    if not text:
        return []
    hits: list[Tuple[str, str]] = []
    for kind, pattern in _PATTERNS.items():
        for match in pattern.finditer(text):
            hits.append((kind, match.group(0)))
    # Entropy pass on long tokens separated by whitespace/punctuation
    for token in re.findall(r"[A-Za-z0-9+/=_-]{24,}", text):
        if _looks_high_entropy(token):
            hits.append(("high_entropy", token))
    return hits


def scrub(text: str) -> Tuple[str, dict[str, str]]:
    """Replace detected secrets with secret_ref tokens.

    Returns scrubbed_text and a dict secret_ref -> original.
    """
    if not text:
        return text, {}
    secrets: dict[str, str] = {}
    scrubbed = text
    for idx, (kind, value) in enumerate(scan(text)):
        ref = f"secret_ref:{kind}:{idx}"
        secrets[ref] = value
        scrubbed = scrubbed.replace(value, ref)
    return scrubbed, secrets
