"""Schema version constants for persisted objects.

Centralizing version numbers keeps migrations predictable and avoids
per-file drift when we extend the data model.
"""

TRACE_ROUND_VERSION = 1
TRACE_EVENT_VERSION = 1
MEMORY_BUCKET_VERSION = 1
REGISTER_VERSION = 1
JOB_VERSION = 1
STATE_VERSION = 1
AUDIO_ARTIFACT_VERSION = 1
