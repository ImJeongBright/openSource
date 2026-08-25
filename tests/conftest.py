import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("OPENSQL_PASSWORD", "test-password")
os.environ.setdefault("EMBEDDING_API_PROVIDER", "ollama")
os.environ.setdefault("EMBEDDING_BASE_URL", "http://127.0.0.1:11434")
os.environ.setdefault("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
os.environ.setdefault("EMBEDDING_DIMENSIONS", "1024")
os.environ.setdefault("EMBEDDING_TIMEOUT_SECONDS", "60")
