"""Deployable composition of video-shop and Rig's catalogue/cart host.

Uses Rig's existing tenant data and credentials. No separate catalogue or cart.
"""
import os
from pathlib import Path

from rigbox.three_d.asgi import app
from .video_room import mount

mount(app, Path(os.environ.get('DATA_DIR', '/data')) / 'video-shop')
