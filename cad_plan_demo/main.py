from __future__ import annotations

import argparse
from pathlib import Path

from .dxf_parser import parse_dxf
from .cross_view import apply_cross_view_opening_enrichment, match_project_openings
from .frames import detect_drawing_frames, filter_entities_to_frame
from .io_utils import ensure_dxf
from .pipeline import analyze_entities
from .railing_recognition import enrich_railings_with_section_height
from .standard_export import write_standard_project_outputs
from .stair_recognition import enrich_stairs_with_project_floor_height


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract walls, doors/windows and axes from a DWG/DXF architectural drawing."
    )
    parser.add_argument("input", help="Input .dxf or .dwg file. DWG requires ODA File Converter.")
    parser.add_argument("--out", default="outputs/cad_plan_demo", help="Output folder for JSON/CSV reports.")
    args = parser.parse_args()

    input_path = Path(args.input)
    dxf_path = ensure_dxf(input_path)
    entities = parse_dxf(dxf_path)
    out_dir = Path(args.out)
    frames = detect_drawing_frames(entities)
    if frames:
        workbook_results: list[tuple[str, dict]] = []
        for index, frame in enumerate(frames, start=1):
            frame_entities = filter_entities_to_frame(entities, frame)
            result = analyze_entities(frame_entities, input_path, dxf_path, frame)
            frame_name = f"{input_path.stem}_{frame.id}"
            workbook_results.append((frame_name, result))
        matches = match_project_openings(workbook_results)
        enriched_openings = apply_cross_view_opening_enrichment(workbook_results, matches)
        enrich_stairs_with_project_floor_height(workbook_results)
        enriched_railings = enrich_railings_with_section_height(workbook_results)
        door_matches = [item for item in matches if item.get("opening_kind") == "door"]
        window_matches = [item for item in matches if item.get("opening_kind") == "window"]
        standard = write_standard_project_outputs(out_dir, workbook_results, input_path)
        print("Extraction finished.")
        print(f"Drawing frames: {len(frames)}")
        print(f"Cross-view opening matches: {len(matches)}")
        print(f"Cross-view door matches: {len(door_matches)}")
        print(f"Cross-view window matches: {len(window_matches)}")
        print(f"Plan openings enriched from elevations: {enriched_openings}")
        print(f"Plan railings enriched from sections: {enriched_railings}")
        print(f"Standard output folder: {standard.output_dir.resolve()}")
        print(f"Output folder: {out_dir.resolve()}")
        return 0

    result = analyze_entities(entities, input_path, dxf_path)
    standard = write_standard_project_outputs(out_dir, [(input_path.stem, result)], input_path)

    counts = result["counts"]
    print("Extraction finished.")
    print(f"Drawing type: {result['notes']['drawing_type']}")
    print("Walls: {walls}, doors/windows: {openings}, axes: {axes}, stairs: {stairs}".format(**counts))
    print(f"Standard output folder: {standard.output_dir.resolve()}")
    print(f"Output folder: {Path(args.out).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
