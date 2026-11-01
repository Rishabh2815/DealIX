"""Vercel FastAPI entrypoint; Docker runs the same app via dealix.api:app."""
from dealix.api import app

__all__ = ["app"]
