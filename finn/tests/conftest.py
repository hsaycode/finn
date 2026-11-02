"""Tests get their own empty database and never reach the network or a model."""
import os
import tempfile

os.environ["FINN_DATA_DIR"] = tempfile.mkdtemp(prefix="finn-test-")
os.environ["FINN_LLM"] = "none"
os.environ.pop("FINN_REFRESH_MINUTES", None)
