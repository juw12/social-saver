"""Vercel entry point: the Social Saver web downloader (saver/webdl.py)."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Only /tmp is writable on Vercel; gallery-dl and Social Saver keep their scratch files there.
os.environ.setdefault("SOCIAL_SAVER_HOME", str(Path(tempfile.gettempdir()) / "social-saver"))
os.environ.setdefault("XDG_CACHE_HOME", tempfile.gettempdir())

from saver.webdl import app  # noqa: E402

__all__ = ["app"]
