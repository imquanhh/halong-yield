"""Vercel entrypoint: every route is rewritten here (vercel.json) and served by the FastAPI app."""

from agent.server import app  # noqa: F401
