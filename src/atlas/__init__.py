"""atlas — schema maps and validation for data trees.

Atlas maps the forest: it owns the schemas (the backbones that define what a
valid data tree/stage looks like), validates local data against them, and
detects known data types on disk.  Atlas is standalone — it does not depend
on forest or on any data-management engine.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from atlas.detect import Detection, detect, extract_unit_ids
from atlas.log import get_logger
from atlas.manifest import (
    AtlasManifest,
    ManifestAssetKeyCapability,
    ManifestAttachmentUnmatched,
    ManifestCapabilities,
    ManifestEnricher,
    ManifestEnrichmentInput,
    ManifestEnrichmentStatus,
    ManifestError,
    ManifestJoinCardinality,
    ManifestQueryValue,
    ManifestRecord,
    ManifestRecordCapability,
    ManifestRelatedRecord,
    ManifestRelationshipBundle,
    ManifestRelationshipCapability,
    ManifestRelationshipEndpointCapability,
    ManifestRelationshipIssue,
    ManifestRelationshipIssueCode,
    ManifestRelationshipResult,
    ManifestTableLike,
    ManifestTagCapability,
    ManifestTagValue,
    attach_dataframe,
    load_dataframe,
    register_manifest_enricher,
    unregister_manifest_enricher,
)
from atlas.schema import (
    DetectionConfig,
    ManifestAssetKeyConfig,
    ManifestConfig,
    ManifestExtractorConfig,
    ManifestJoinConfig,
    ManifestRecordConfig,
    ManifestRelationshipConfig,
    ManifestRelationshipEndpointConfig,
    ManifestTableConfig,
    Schema,
    SchemaError,
    SyncConfig,
    ValidateConfig,
    discover_schemas,
    get_sync_files,
    load_all_schemas,
    load_schema,
    resolve_key_output,
    resolve_schema,
)
from atlas.validate import RuleResult, ValidationResult, validate_data_unit

try:
    # Distribution name (atlas-manifest), not the import name.
    __version__ = version("atlas-manifest")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "AtlasManifest",
    "Detection",
    "DetectionConfig",
    "ManifestAssetKeyCapability",
    "ManifestAssetKeyConfig",
    "ManifestAttachmentUnmatched",
    "ManifestCapabilities",
    "ManifestConfig",
    "ManifestEnricher",
    "ManifestEnrichmentInput",
    "ManifestEnrichmentStatus",
    "ManifestError",
    "ManifestExtractorConfig",
    "ManifestJoinCardinality",
    "ManifestJoinConfig",
    "ManifestQueryValue",
    "ManifestRecord",
    "ManifestRecordCapability",
    "ManifestRecordConfig",
    "ManifestRelatedRecord",
    "ManifestRelationshipBundle",
    "ManifestRelationshipCapability",
    "ManifestRelationshipConfig",
    "ManifestRelationshipEndpointCapability",
    "ManifestRelationshipEndpointConfig",
    "ManifestRelationshipIssue",
    "ManifestRelationshipIssueCode",
    "ManifestRelationshipResult",
    "ManifestTableConfig",
    "ManifestTableLike",
    "ManifestTagCapability",
    "ManifestTagValue",
    "RuleResult",
    "Schema",
    "SchemaError",
    "SyncConfig",
    "ValidateConfig",
    "ValidationResult",
    "attach_dataframe",
    "detect",
    "discover_schemas",
    "extract_unit_ids",
    "get_logger",
    "get_sync_files",
    "load_all_schemas",
    "load_dataframe",
    "load_schema",
    "register_manifest_enricher",
    "resolve_key_output",
    "resolve_schema",
    "unregister_manifest_enricher",
    "validate_data_unit",
]
