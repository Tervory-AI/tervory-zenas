"""Market Lab owns this public source list; Autumbra imports the feed, not a copy."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str


SOURCES = (
    Source("osv", "Open Source Vulnerabilities", "https://api.osv.dev/v1/"),
    Source("github", "GitHub Security Advisories", "https://api.github.com/"),
    Source("cisa-kev", "CISA Known Exploited Vulnerabilities",
           "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"),
)
SOURCE_URLS = {source.id: source.url for source in SOURCES}
# Identities of the existing non-registry software assets in Forge's locks.
# These are source identities, not version pins. Versions always come from the lock.
TOOL_REPOSITORIES = {
    "blender": ("blender/blender", "v{version}"),
    "gltfpack": ("zeux/meshoptimizer", "v{version}"),
    "ktx": ("KhronosGroup/KTX-Software", "v{version}"),
    "meshroom": ("alicevision/Meshroom", "v{version}"),
    "alicevision": ("alicevision/AliceVision", "v{version}"),
}
