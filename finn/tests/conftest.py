"""Tests get their own empty database and never reach the network or a model."""
import os
import tempfile

os.environ["FINN_DATA_DIR"] = tempfile.mkdtemp(prefix="finn-test-")
os.environ["FINN_LLM"] = "none"
# Tests never use real keys, even if the developer has a .env file.
os.environ["FINN_NO_DOTENV"] = "1"
for _key in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
    os.environ.pop(_key, None)
os.environ.pop("FINN_REFRESH_MINUTES", None)
