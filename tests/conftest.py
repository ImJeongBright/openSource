from pathlib import Path
import os
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Phase 4 tests do not connect to external services, but src.config creates
# Settings at import time and requires these deployment values.
os.environ.setdefault("OPENSQL_PASSWORD", "test-password")
os.environ.setdefault("EMBEDDING_API_KEY", "test-api-key")
os.environ.setdefault("TIKTOKEN_CACHE_DIR", "/tmp/opensql-tiktoken-cache")
