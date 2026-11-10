"""Settings guardrails and snapshot/apply/restore (ADR 0003). Filled in next commits."""
from __future__ import annotations

# Never changed by the tool: server binding, access, and where models live.
FORBIDDEN_MODEL_KEYS = {"model_alias", "model_type_override"}
FORBIDDEN_GLOBAL_KEYS = {"host", "port", "api_key", "sub_keys", "skip_api_key_verification",
                         "model_dirs", "model_dir", "ssd_cache_dir", "auth"}
