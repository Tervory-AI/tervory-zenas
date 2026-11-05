"""Plugin card arguments, separate from manifest loading during Command imports."""
from pydantic import BaseModel, ConfigDict, Field


class PluginArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=30)
