"""Shared DicePP instance layout and persistent-data catalog.

This package intentionally depends only on the Python standard library so the
Bot, Dashboard, and Manager can inspect instance data before starting NoneBot.
"""

from .assets import (
    ARCHIVE_PROFILE_FULL,
    ARCHIVE_PROFILE_REGULAR,
    BOT_CORE_ASSET,
    BOT_CORE_SCHEMA,
    BOT_LOG_ASSET,
    BOT_LOG_SCHEMA,
    BOT_CONFIGS_ASSET,
    CONTENT_ASSET,
    DATA_CATALOG,
    INSTANCE_DB_ASSET,
    INSTANCE_SCHEMA,
    LOCAL_IMAGES_ASSET,
    PERSONA_DB_ASSET,
    PERSONA_SCHEMA,
    USER_CONFIG_ASSET,
    DataAsset,
    DataAssetCatalog,
    DataAssetKind,
    DataAssetMatch,
    DataAssetRestoreTarget,
    SchemaReference,
)
from .layout import InstanceLayout

__all__ = [
    "ARCHIVE_PROFILE_FULL",
    "ARCHIVE_PROFILE_REGULAR",
    "BOT_CONFIGS_ASSET",
    "BOT_CORE_ASSET",
    "BOT_CORE_SCHEMA",
    "BOT_LOG_ASSET",
    "BOT_LOG_SCHEMA",
    "CONTENT_ASSET",
    "DATA_CATALOG",
    "INSTANCE_DB_ASSET",
    "INSTANCE_SCHEMA",
    "LOCAL_IMAGES_ASSET",
    "PERSONA_DB_ASSET",
    "PERSONA_SCHEMA",
    "USER_CONFIG_ASSET",
    "DataAsset",
    "DataAssetCatalog",
    "DataAssetKind",
    "DataAssetMatch",
    "DataAssetRestoreTarget",
    "InstanceLayout",
    "SchemaReference",
]
