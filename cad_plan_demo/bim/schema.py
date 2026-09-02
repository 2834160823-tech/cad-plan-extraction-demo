from __future__ import annotations

from copy import deepcopy


SCHEMA_VERSION = "1.0"

REVIEW_STATUSES = {"ready", "needs_review", "confirmed", "rejected"}
SUPPORTED_COMPONENTS = ("levels", "grids", "columns", "walls", "slabs", "floor_openings", "stairs", "doors", "windows")
MODEL_SEQUENCE = ("levels", "grids", "columns", "walls", "slabs", "floor_openings", "stairs", "doors", "windows")


STANDARD_JSON_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AI Revit Modeling Standard Component Data",
    "type": "object",
    "required": ["schema_version", "project", "components", "validation"],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "project": {
            "type": "object",
            "required": ["name", "units"],
            "properties": {
                "name": {"type": "string"},
                "units": {"const": "mm"},
                "source_excel": {"type": "string"},
                "source_notes": {"type": "string"},
            },
        },
        "components": {
            "type": "object",
            "required": list(SUPPORTED_COMPONENTS),
            "properties": {
                "levels": {"type": "array", "items": {"$ref": "#/$defs/level"}},
                "grids": {"type": "array", "items": {"$ref": "#/$defs/grid"}},
                "columns": {"type": "array", "items": {"$ref": "#/$defs/column"}},
                "walls": {"type": "array", "items": {"$ref": "#/$defs/wall"}},
                "slabs": {"type": "array", "items": {"$ref": "#/$defs/slab"}},
                "floor_openings": {"type": "array", "items": {"$ref": "#/$defs/floor_opening"}},
                "stairs": {"type": "array", "items": {"$ref": "#/$defs/stair"}},
                "doors": {"type": "array", "items": {"$ref": "#/$defs/opening"}},
                "windows": {"type": "array", "items": {"$ref": "#/$defs/opening"}},
            },
        },
        "validation": {
            "type": "object",
            "properties": {
                "requires_human_confirmation": {"type": "boolean"},
                "issues": {"type": "array"},
                "model_sequence": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "$defs": {
        "common": {
            "type": "object",
            "required": ["id", "type", "source", "confidence", "review_status"],
            "properties": {
                "id": {"type": "string"},
                "type": {"type": "string"},
                "source": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "review_status": {"enum": sorted(REVIEW_STATUSES)},
                "notes": {"type": "string"},
            },
        },
        "level": {
            "allOf": [
                {"$ref": "#/$defs/common"},
                {
                    "type": "object",
                    "required": ["name", "elevation_mm"],
                    "properties": {
                        "name": {"type": "string"},
                        "elevation_mm": {"type": "number"},
                    },
                },
            ]
        },
        "grid": {
            "allOf": [
                {"$ref": "#/$defs/common"},
                {
                    "type": "object",
                    "required": ["name", "start", "end"],
                    "properties": {
                        "name": {"type": "string"},
                        "start": {"$ref": "#/$defs/point"},
                        "end": {"$ref": "#/$defs/point"},
                    },
                },
            ]
        },
        "wall": {
            "allOf": [
                {"$ref": "#/$defs/common"},
                {
                    "type": "object",
                    "required": ["base_level", "start", "end"],
                    "properties": {
                        "base_level": {"type": "string"},
                        "top_level": {"type": ["string", "null"]},
                        "height_mm": {"type": ["number", "null"]},
                        "thickness_mm": {"type": ["number", "null"]},
                        "material": {"type": ["string", "null"]},
                        "start": {"$ref": "#/$defs/point"},
                        "end": {"$ref": "#/$defs/point"},
                    },
                },
            ]
        },
        "column": {
            "allOf": [
                {"$ref": "#/$defs/common"},
                {
                    "type": "object",
                    "required": ["level", "location"],
                    "properties": {
                        "level": {"type": "string"},
                        "top_level": {"type": ["string", "null"]},
                        "location": {"$ref": "#/$defs/point"},
                        "base_z_mm": {"type": ["number", "null"]},
                        "top_z_mm": {"type": ["number", "null"]},
                        "height_mm": {"type": ["number", "null"]},
                        "width_mm": {"type": ["number", "null"]},
                        "depth_mm": {"type": ["number", "null"]},
                        "diameter_mm": {"type": ["number", "null"]},
                        "rotation_angle": {"type": ["number", "null"]},
                        "material": {"type": ["string", "null"]},
                    },
                },
            ]
        },
        "slab": {
            "allOf": [
                {"$ref": "#/$defs/common"},
                {
                    "type": "object",
                    "required": ["level", "boundary"],
                    "properties": {
                        "level": {"type": "string"},
                        "thickness_mm": {"type": ["number", "null"]},
                        "elevation_mm": {"type": ["number", "null"]},
                        "material": {"type": ["string", "null"]},
                        "boundary": {"type": "array", "items": {"$ref": "#/$defs/point"}},
                    },
                },
            ]
        },
        "floor_opening": {
            "allOf": [
                {"$ref": "#/$defs/common"},
                {
                    "type": "object",
                    "required": ["level", "host_floor_id", "boundary"],
                    "properties": {
                        "level": {"type": "string"},
                        "host_floor_id": {"type": ["string", "null"]},
                        "location": {"$ref": "#/$defs/point"},
                        "boundary": {"type": "array", "items": {"$ref": "#/$defs/point"}},
                        "width_mm": {"type": ["number", "null"]},
                        "depth_mm": {"type": ["number", "null"]},
                    },
                },
            ]
        },
        "stair": {
            "allOf": [
                {"$ref": "#/$defs/common"},
                {
                    "type": "object",
                    "required": ["base_level", "top_level", "boundary"],
                    "properties": {
                        "stair_type": {"type": "string"},
                        "base_level": {"type": ["string", "null"]},
                        "top_level": {"type": ["string", "null"]},
                        "matched_floor_opening_id": {"type": ["string", "null"]},
                        "boundary": {"type": "array", "items": {"$ref": "#/$defs/point"}},
                        "start": {"$ref": "#/$defs/point"},
                        "end": {"$ref": "#/$defs/point"},
                        "total_rise_mm": {"type": ["number", "null"]},
                        "total_run_mm": {"type": ["number", "null"]},
                        "width_mm": {"type": ["number", "null"]},
                        "stairwell_width_mm": {"type": ["number", "null"]},
                        "run_count": {"type": ["number", "null"]},
                        "risers_per_run": {"type": ["number", "null"]},
                        "treads_per_run": {"type": ["number", "null"]},
                        "run_length_mm": {"type": ["number", "null"]},
                        "landing_length_mm": {"type": ["number", "null"]},
                        "landing_width_mm": {"type": ["number", "null"]},
                        "riser_height_mm": {"type": ["number", "null"]},
                        "tread_depth_mm": {"type": ["number", "null"]},
                        "number_of_risers": {"type": ["number", "null"]},
                        "number_of_treads": {"type": ["number", "null"]},
                        "direction": {"type": ["string", "null"]},
                        "stair_core_id": {"type": ["string", "null"]},
                        "stair_segment_id": {"type": ["string", "null"]},
                        "stair_segment_number": {"type": ["number", "null"]},
                        "level_span_count": {"type": ["number", "null"]},
                    },
                },
            ]
        },
        "opening": {
            "allOf": [
                {"$ref": "#/$defs/common"},
                {
                    "type": "object",
                    "required": ["level", "host_wall_id", "location", "width_mm", "height_mm"],
                    "properties": {
                        "level": {"type": "string"},
                        "host_wall_id": {"type": ["string", "null"]},
                        "location": {"$ref": "#/$defs/point"},
                        "width_mm": {"type": ["number", "null"]},
                        "height_mm": {"type": ["number", "null"]},
                        "sill_height_mm": {"type": ["number", "null"]},
                        "material": {"type": ["string", "null"]},
                    },
                },
            ]
        },
        "point": {
            "type": "object",
            "required": ["x", "y", "z"],
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "z": {"type": "number"},
            },
        },
    },
}


def schema_copy() -> dict:
    return deepcopy(STANDARD_JSON_SCHEMA)


def empty_standard_model(project_name: str = "Untitled Project") -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "project": {
            "name": project_name,
            "units": "mm",
            "source_excel": "",
            "source_notes": "",
        },
        "components": {name: [] for name in SUPPORTED_COMPONENTS},
        "validation": {
            "requires_human_confirmation": False,
            "issues": [],
            "model_sequence": list(MODEL_SEQUENCE),
        },
    }
