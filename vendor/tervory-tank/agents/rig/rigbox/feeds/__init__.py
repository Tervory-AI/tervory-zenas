"""Catalogue feed URLs. Register once, then a caller runs pull on a fetched body."""

from .mapper import map_google_merchant, map_shopify, map_woocommerce, pull, register

__all__ = [
    "map_google_merchant",
    "map_shopify",
    "map_woocommerce",
    "pull",
    "register",
]
