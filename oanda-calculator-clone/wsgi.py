"""WSGI entrypoint for serving the calculator with Gunicorn or another server."""

from oanda_calculator_web import app

__all__ = ["app"]
