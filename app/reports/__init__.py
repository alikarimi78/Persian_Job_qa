"""PDF reports built from a search answer. See `render.py` for why the payload comes
from the client rather than from a fresh search."""

from .render import build_html, filename, printable, render_pdf

__all__ = ["build_html", "filename", "printable", "render_pdf"]
