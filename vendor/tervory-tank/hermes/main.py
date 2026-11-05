"""Hermes process. Uvicorn loads this module. Tests build their own app."""

from __future__ import annotations

from hermes.runtime import connect_production
from hermes.service import create_app

app = create_app(connect=connect_production)
