"""Composition entry: keep Rig's authentication, catalogue and cart unchanged.

Call mount(app, durable_data_dir) on an initialized Rig application. No model
fallback is enabled; any future fallback must use chassis Tank and retain receipts.
"""
from .video_routes import mount

__all__ = ['mount']
