# CAD Plan Extraction Demo

This demo extracts a small, controlled set of architectural information from DXF/DWG drawings.

Current retained recognition scope:

- axes / grid lines
- walls from paired parallel wall lines, including angled straight walls, short wall pieces between openings, and short T-shaped door-side wall piers
- columns from rectangular closed outlines or circles on column layers
- default floor slabs from plan wall extents, with floor openings from a dedicated hole/opening layer rectangle plus an internal fold line
- doors from door layers, blocks, or quarter-arc swing geometry
- windows from window layers, blocks, five-line sliding-window patterns, or elevation nested rectangles
- annotations and text, including drawing title, drawing type, elevation marks, floor-height candidates, and C/M door-window marks such as `C1225` or `M0921`

Removed from this CAD recognition layer:

- slopes / ramps
- stairs
- elevation wall/profile line export

The deleted component types are no longer called by the pipeline and are no longer written to CSV, JSON summaries, GUI batch summaries, or Excel reports.

## GUI

Simplest Windows use:

```text
Double-click run_cad_gui.bat
Add one or more DXF/DWG drawings
Choose an output folder
Click Generate
```

DWG input requires ODA File Converter. If ODA is not installed, export the DWG to DXF first.

## Command Line

```powershell
python -m cad_plan_demo.main examples\sample_requirements_plan.dxf --out outputs\sample_requirements_plan
```

Or double-click:

```text
run_cad_demo.bat
```

## Outputs

The program creates one project export folder named like:

```text
ProjectName_YYYYMMDD_HHMMSS/
  01_人工快速查看_中文识别报告.xlsx
  02_标准化模型数据/
    AI_Model.json
    AI_Elements.jsonl
    AI_Readme.md
    csv_tables/
      Manifest.csv
      Project_Info.csv
      Drawings.csv
      Levels.csv
      Grids.csv
      Walls.csv
      Wall_Runs.csv
      Doors.csv
      Windows.csv
      Columns.csv
      Floor_Openings.csv
      Beams.csv
      Floors.csv
      Stairs.csv
      Rooms.csv
      Dimensions.csv
      Text_Annotations.csv
      Raw_Geometry.csv
      Element_Geometry_Map.csv
      Opening_Wall_Run_Map.csv
      Materials.csv
      Element_Material_Map.csv
      Uncertain_Elements.csv
  03_人工详细核查_完整报告.md
```

The three parts are:

1. A Chinese Excel report for quick manual review. Each sheet corresponds to one drawing/frame.
2. Standardized model data for later AI/Revit modeling. AI should read `AI_Model.json` first, or stream `AI_Elements.jsonl` when one-element-per-line is easier. Compatibility CSV tables are kept under `csv_tables/`.
3. A detailed Chinese Markdown report for manual checking and traceability.

Empty component categories are still exported with headers.

## Standard Model Data Use

For AI or agent workflows, prefer:

1. `AI_Model.json`: complete nested project model grouped by drawing, with walls, doors, windows, grids, dimensions, text annotations, confidence, and review flags.
2. `AI_Elements.jsonl`: one recognized element or annotation per line, suitable for streaming, indexing, retrieval, or incremental checks.

For Revit wall generation, prefer `wall_runs` over raw `walls`. Raw `walls` are the recognized wall segments; `wall_runs` are logical continuous host walls grouped across door/window openings. Doors and windows expose `host_wall_run_id`, and `Opening_Wall_Run_Map.csv` records the same relationship for CSV-based importers.

Door/window component categories are exported separately from recognition source. `Door_Category` currently supports `single_swing_door`, `double_swing_door`, and `sliding_door`. `Window_Category` currently supports `casement_window` and `sliding_window` when elevation symbols include the required V-fold or arrow features; otherwise the category is `unknown` for review.

Material linking is explicitly reserved but not fabricated. `AI_Model.json` includes `material_catalog`, `material_links`, and a `material` slot on wall, door, and window elements. CSV-compatible material data can be filled later through `csv_tables/Materials.csv` and `csv_tables/Element_Material_Map.csv`.

For Revit importers, spreadsheets, or traditional ETL scripts, read the compatibility CSV files under `csv_tables/` in this order:

1. `csv_tables/Manifest.csv`
2. `csv_tables/Project_Info.csv`
3. `csv_tables/Drawings.csv`
4. `csv_tables/Levels.csv`
5. `csv_tables/Grids.csv`
6. `csv_tables/Walls.csv`
7. `csv_tables/Wall_Runs.csv`
8. `csv_tables/Doors.csv`
9. `csv_tables/Windows.csv`
10. `csv_tables/Opening_Wall_Run_Map.csv`
11. `csv_tables/Materials.csv`
12. `csv_tables/Element_Material_Map.csv`
13. Optional empty or future-scope component tables
14. `csv_tables/Raw_Geometry.csv`
15. `csv_tables/Uncertain_Elements.csv`

`Needs_Review=true` means the row should be checked manually before Revit modeling. The default confidence review threshold is `0.80`. If a value is defaulted or inferred, the reason is written in `Remarks` and the object is listed in `csv_tables/Uncertain_Elements.csv`.

Run the standard export test:

```powershell
python -m unittest discover -s tests
```

## Multi-Frame Drawings

If one CAD file contains several drawings on the same sheet, draw each drawing boundary as a closed rectangular frame on a frame layer such as:

- `frame`
- `sheet`
- `layout`
- `viewport`

When frames are found, the program treats each frame as a separate sub-drawing and writes one folder per frame.

## Requirement-Oriented Fields

The demo adds fields intended for later BIM/Revit work:

- Axis origin: the intersection of the leftmost vertical axis and lowest horizontal axis is used as local `(0, 0)` when both are found.
- Relative coordinates: axes, walls, and openings receive local coordinate fields.
- Walls: straight walls are recognized from paired parallel wall lines; wall height can be filled from detected floor-height candidates.
- Windows: nearby `C1225` style annotations are attached to the nearest window where possible.
- Doors: nearby `M0921` style annotations are attached to the nearest door where possible; quarter-arc doors include an opening direction.
- Elevation heights: elevation marks such as `+0.000` and `+3.600` are used to derive floor-height candidates.
- Cross-view matching: plan doors/windows can be matched to elevation doors/windows using component type, facade direction, annotation, width/height, and projected position.

## Layer Naming

The first version uses practical layer/name rules:

- wall layers contain `wall`, Chinese wall text, or `A-WALL`
- door layers or block names contain `door`, Chinese door text, or `A-DOOR`
- window layers or block names contain `window`, Chinese window text, `win`, or `A-WINDOW`
- axis layers contain `axis`, `grid`, Chinese axis text, `A-GRID`, or `center`

## Current Limits

This is intentionally a demo, not a full CAD engine.

- Binary DWG is converted through ODA; it is not parsed directly.
- Curved walls are not handled yet, but straight angled walls are handled.
- Complex dynamic blocks are not expanded yet.
- Image-based or exploded-line notes still need OCR later.
- Recognition is rule-based and assumes drawings are reasonably layered.

Useful next upgrades:

- replace the lightweight parser with `ezdxf`
- add block expansion
- add OCR for image/exploded-line notes
- add room boundary reconstruction
- add object-level diff between old and new drawings
- add IFC/Revit update operations

Door cross-view sample:

```powershell
python -m cad_plan_demo.main examples\sample_cross_view_door_match.dxf --out outputs\sample_cross_view_door_match
```
