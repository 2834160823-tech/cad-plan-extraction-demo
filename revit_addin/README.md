# AI Revit Modeling Add-in

This folder contains the first-version Revit external tool for reading the
standard BIM JSON produced by:

```powershell
python -m cad_plan_demo.bim_main examples\sample_bim_input.xlsx examples\sample_design_notes.txt --out outputs\bim_modeling_sample
```

The add-in intentionally does not call the LLM. It only runs inside Revit and:

1. Opens `revit_model_input.json`.
2. Validates the schema version and required component data.
3. Shows a preview and confirmation dialog before modeling.
4. Creates elements in this order: levels, grids, walls, slabs, doors/windows.
5. Writes `component_statistics.csv` and `component_details.csv` after modeling.

First version scope:

- Create Revit levels.
- Create straight walls from start/end coordinates.
- Place doors and windows on known host walls using loaded family symbols.
- Skip items marked `needs_review` or `rejected` and write the reason to the report.
- Report skipped floors/slabs as future scope instead of silently fabricating them.

Build and install:

```powershell
cd path\to\new-chat\revit_addin
.\build_revit_addin.ps1 -RevitInstallDir "C:\Program Files\Autodesk\Revit 2026"
.\install_revit_addin.ps1 -RevitVersion 2026
```

After restarting Revit, open:

```text
Add-Ins -> External Tools -> AI Revit Modeling
```

Then select:

```text
outputs\bim_modeling_sample\revit_model_input.json
```

The tool will preview the element counts, ask for confirmation, create the
model, and write reports next to the selected JSON file.

Optional environment variables:

- `AI_REVIT_FAMILY_LIBRARY`: default folder for the Chinese family library.
- `AI_REVIT_ENGLISH_FAMILY_LIBRARY`: matching English family library folder.
- `AI_FAMILY_PREVIEW_OUTPUT`: default output folder for family previews.
