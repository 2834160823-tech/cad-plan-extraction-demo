using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace AiRevitModeling
{
    public partial class AiRevitModelingCommand
    {
        private static ComplianceReview BuildComplianceReview(Document doc, StandardModel model, FamilyAssignmentPlan familyPlan)
        {
            ComplianceEvidenceBundle evidence = BuildComplianceEvidenceBundle(doc, model, familyPlan);
            ComplianceReview review = new ComplianceReview
            {
                Sources = evidence.Sources,
                Rows = new List<ComplianceIssueRow>()
            };

            AddSourceReadinessRows(review, evidence);

            if (evidence.Model.Validation != null && evidence.Model.Validation.Issues != null)
            {
                foreach (ValidationIssue issue in evidence.Model.Validation.Issues)
                {
                    bool autoFixable = IsAutoFixableDataIssue(issue.Message);
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = autoFixable ? "warning" : NormalizeSeverity(issue.Severity),
                        ComponentGroup = issue.ComponentGroup,
                        ComponentId = issue.ComponentId,
                        Rule = autoFixable ? "ai_auto_fix_required" : "json_validation",
                        Message = issue.Message,
                        Recommendation = autoFixable ? "Let the AI repair or infer the numeric field from schedules, design notes, similar components, or project defaults before compliance review. Do not send this item to human review unless a cross-source conflict remains after repair." : "Review the JSON evidence before modeling.",
                        EvidenceSources = "dxf_geometry",
                        EvidenceType = autoFixable ? "ai_auto_fix_required" : "data_validation",
                        NeedsHumanDecision = autoFixable ? false : (bool?)null,
                        AutoFixRecommended = autoFixable
                    });
                }
            }

            foreach (FamilyAssignmentRow row in (evidence.FamilyPlan.Rows ?? new List<FamilyAssignmentRow>()).Where(r => string.Equals(r.RiskLevel, "high", StringComparison.OrdinalIgnoreCase)))
            {
                review.Rows.Add(new ComplianceIssueRow
                {
                    Severity = "error",
                    ComponentGroup = row.ComponentGroup,
                    ComponentId = row.ComponentIds,
                    Rule = "family_assignment",
                    Message = row.RequirementName + ": " + row.CurrentStatus,
                    Recommendation = row.SelectedStrategy,
                    EvidenceSources = "family_assignment"
                });
            }

            List<WallComponent> walls = evidence.Model.Components.Walls ?? new List<WallComponent>();
            Dictionary<string, WallComponent> wallsById = walls
                .Where(w => !string.IsNullOrWhiteSpace(w.Id))
                .GroupBy(w => w.Id, StringComparer.OrdinalIgnoreCase)
                .ToDictionary(g => g.Key, g => g.First(), StringComparer.OrdinalIgnoreCase);
            AddWallComplianceRows(review, walls);
            AddOpeningComplianceRows(review, "doors", evidence.Model.Components.Doors, wallsById, allowDoorHeightDefault: true);
            AddOpeningComplianceRows(review, "windows", evidence.Model.Components.Windows, wallsById, allowDoorHeightDefault: false);

            List<SlabComponent> slabs = evidence.Model.Components.Slabs ?? new List<SlabComponent>();
            Dictionary<string, SlabComponent> slabsById = slabs
                .Where(s => !string.IsNullOrWhiteSpace(s.Id))
                .GroupBy(s => s.Id, StringComparer.OrdinalIgnoreCase)
                .ToDictionary(g => g.Key, g => g.First(), StringComparer.OrdinalIgnoreCase);
            foreach (FloorOpeningComponent opening in evidence.Model.Components.FloorOpenings ?? new List<FloorOpeningComponent>())
            {
                SlabComponent hostSlab = null;
                if (string.IsNullOrWhiteSpace(opening.HostFloorId) || !slabsById.TryGetValue(opening.HostFloorId, out hostSlab))
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "error",
                        ComponentGroup = "floor_openings",
                        ComponentId = opening.Id,
                        Rule = "host_floor",
                        Message = "Floor opening host_floor_id is missing or does not match a slab.",
                        Recommendation = "Assign a valid host floor before creating the opening.",
                        EvidenceSources = "dxf_geometry,spatial_semantics"
                    });
                }
                else
                {
                    AddFloorOpeningGeometryRows(review, opening, hostSlab);
                }
                if (opening.Boundary == null || opening.Boundary.Count < 3)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "error",
                        ComponentGroup = "floor_openings",
                        ComponentId = opening.Id,
                        Rule = "opening_boundary",
                        Message = "Floor opening boundary has fewer than three points.",
                        Recommendation = "Keep this opening for manual review; do not model automatically.",
                        EvidenceSources = "dxf_geometry"
                    });
                }
                if (!opening.WidthMm.HasValue || opening.WidthMm.Value <= 0 || !opening.DepthMm.HasValue || opening.DepthMm.Value <= 0)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = "floor_openings",
                        ComponentId = opening.Id,
                        Rule = "opening_size",
                        Message = "Floor opening width_mm or depth_mm is missing.",
                        Recommendation = "Verify the opening dimensions before relying on the generated floor sketch.",
                        EvidenceSources = "dxf_geometry,spatial_semantics"
                    });
                }
            }

            foreach (GridComponent grid in evidence.Model.Components.Grids ?? new List<GridComponent>())
            {
                string safe = SafeGridName(grid.Name, grid.Id);
                if (!string.Equals(safe, grid.Name, StringComparison.Ordinal))
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = "grids",
                        ComponentId = grid.Id,
                        Rule = "revit_name_safety",
                        Message = "Grid name contains Revit-prohibited characters or CAD control codes: " + grid.Name,
                        Recommendation = "The Revit tool will rename it to: " + safe,
                        EvidenceSources = "dxf_geometry"
                    });
                }
            }

            foreach (SlabComponent slab in slabs)
            {
                if (slab.Boundary == null || slab.Boundary.Count < 3)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "error",
                        ComponentGroup = "slabs",
                        ComponentId = slab.Id,
                        Rule = "slab_boundary",
                        Message = "Slab boundary has fewer than three points.",
                        Recommendation = "Do not model this slab automatically.",
                        EvidenceSources = "dxf_geometry"
                    });
                }
                if (!slab.ThicknessMm.HasValue || slab.ThicknessMm.Value <= 0)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = "slabs",
                        ComponentId = slab.Id,
                        Rule = "slab_thickness",
                        Message = "Slab thickness_mm is missing or invalid.",
                        Recommendation = "Confirm slab thickness before using generated floor types.",
                        EvidenceSources = "spatial_semantics,design_notes"
                    });
                }
            }

            foreach (ColumnComponent column in evidence.Model.Components.Columns ?? new List<ColumnComponent>())
            {
                AddColumnComplianceRows(review, column, slabs);
            }

            AddMaterialComplianceRows(review, evidence.Model);
            AddDesignSpecificationRows(review, evidence);
            AddDesignNoteConsistencyRows(review, evidence);
            AddFinalEvidenceAuditRows(review, evidence.Model);

            foreach (ComponentBase item in AllComponents(evidence.Model).Where(NeedsReview))
            {
                review.Rows.Add(new ComplianceIssueRow
                {
                    Severity = "warning",
                    ComponentGroup = ComponentGroupName(item),
                    ComponentId = item.Id,
                    Rule = "human_review_status",
                    Message = "Component review_status is needs_review.",
                    Recommendation = "Model only after accepting this uncertainty in the final confirmation.",
                    EvidenceSources = "dxf_geometry,material_semantics,spatial_semantics"
                });
            }

            return review;
        }

        private static ComplianceEvidenceBundle BuildComplianceEvidenceBundle(Document doc, StandardModel model, FamilyAssignmentPlan familyPlan)
        {
            model.Components = model.Components ?? new ComponentSet();
            familyPlan = familyPlan ?? new FamilyAssignmentPlan { Rows = new List<FamilyAssignmentRow>() };

            ComplianceEvidenceBundle evidence = new ComplianceEvidenceBundle
            {
                Model = model,
                FamilyPlan = familyPlan,
                Sources = new List<ComplianceEvidenceSource>()
            };

            int componentCount = AllComponents(model).Count();
            evidence.Sources.Add(new ComplianceEvidenceSource
            {
                SourceKey = "dxf_geometry",
                SourceName = "DXF geometry and standardized component model",
                Status = componentCount > 0 ? "available" : "missing",
                Summary = componentCount.ToString(CultureInfo.InvariantCulture) + " model components available for geometry, host, size, and needs_review checks."
            });

            int familyRows = familyPlan.Rows == null ? 0 : familyPlan.Rows.Count;
            evidence.Sources.Add(new ComplianceEvidenceSource
            {
                SourceKey = "family_assignment",
                SourceName = "Family assignment plan",
                Status = familyRows > 0 ? "available" : "missing",
                Summary = familyRows.ToString(CultureInfo.InvariantCulture) + " family assignment rows available."
            });

            int materialEvidenceCount = AllComponents(model).Count(HasMaterialEvidence);
            evidence.Sources.Add(new ComplianceEvidenceSource
            {
                SourceKey = "material_semantics",
                SourceName = "Material recognition agent output",
                Status = materialEvidenceCount > 0 ? "available" : "pending",
                Summary = materialEvidenceCount.ToString(CultureInfo.InvariantCulture) + " components carry material source, confidence, or review metadata."
            });

            int spatialEvidenceCount = CountSpatialEvidence(model);
            evidence.Sources.Add(new ComplianceEvidenceSource
            {
                SourceKey = "spatial_semantics",
                SourceName = "Spatial position and height agent output",
                Status = spatialEvidenceCount > 0 ? "available" : "pending",
                Summary = spatialEvidenceCount.ToString(CultureInfo.InvariantCulture) + " components carry usable level, height, thickness, sill, or elevation data."
            });

            string designNotes = model.Project == null ? "" : model.Project.DesignNoteSummary;
            evidence.Sources.Add(new ComplianceEvidenceSource
            {
                SourceKey = "design_notes",
                SourceName = "Design general notes",
                Status = HasText(designNotes) || AllComponents(model).Any(item => string.Equals(item.MaterialSource, "design_notes", StringComparison.OrdinalIgnoreCase)) ? "available" : "pending",
                Summary = HasText(designNotes) ? designNotes : "No design_note_summary found in project metadata; material_source=design_notes still counts as component-level evidence."
            });

            string specificationSummary = model.Project == null ? "" : model.Project.SpecificationSummary;
            evidence.Sources.Add(new ComplianceEvidenceSource
            {
                SourceKey = "specification_documents",
                SourceName = "Specification documents and code requirements",
                Status = HasText(specificationSummary) ? "available" : "pending",
                Summary = HasText(specificationSummary) ? specificationSummary : "No specification_summary found yet; reserved for future code/spec parser output."
            });

            return evidence;
        }

        private static void AddSourceReadinessRows(ComplianceReview review, ComplianceEvidenceBundle evidence)
        {
            foreach (ComplianceEvidenceSource source in evidence.Sources.Where(s => string.Equals(s.Status, "pending", StringComparison.OrdinalIgnoreCase)))
            {
                review.Rows.Add(new ComplianceIssueRow
                {
                    Severity = "info",
                    ComponentGroup = "compliance_sources",
                    ComponentId = source.SourceKey,
                    Rule = "source_pending",
                    Message = source.SourceName + " is not fully connected for this run.",
                    Recommendation = "Connect this source when its upstream agent output is available.",
                    EvidenceSources = source.SourceKey
                });
            }
        }

        private static void AddWallComplianceRows(ComplianceReview review, List<WallComponent> walls)
        {
            foreach (WallComponent wall in walls)
            {
                if (wall.Start == null || wall.End == null || IsSamePoint2D(wall.Start, wall.End))
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "error",
                        ComponentGroup = "walls",
                        ComponentId = wall.Id,
                        Rule = "wall_geometry",
                        Message = "Wall start/end points are missing or identical.",
                        Recommendation = "Fix the wall baseline before automatic modeling."
                    });
                }
                if (!wall.HeightMm.HasValue || wall.HeightMm.Value <= 0)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = "walls",
                        ComponentId = wall.Id,
                        Rule = "wall_height",
                        Message = "Wall height_mm is missing or invalid.",
                        Recommendation = "The Revit tool may default wall height; confirm against level relationship."
                    });
                }
                if (!wall.ThicknessMm.HasValue || wall.ThicknessMm.Value <= 0)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = "walls",
                        ComponentId = wall.Id,
                        Rule = "wall_thickness",
                        Message = "Wall thickness_mm is missing or invalid.",
                        Recommendation = "Confirm wall thickness and material before generated wall type creation."
                    });
                }
            }
        }

        private static void AddOpeningComplianceRows(ComplianceReview review, string group, List<OpeningComponent> items, Dictionary<string, WallComponent> wallsById, bool allowDoorHeightDefault)
        {
            foreach (OpeningComponent item in items ?? new List<OpeningComponent>())
            {
                WallComponent hostWall = null;
                if (string.IsNullOrWhiteSpace(item.HostWallId) || !wallsById.TryGetValue(item.HostWallId, out hostWall))
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "error",
                        ComponentGroup = group,
                        ComponentId = item.Id,
                        Rule = "host_wall",
                        Message = "Opening host_wall_id is missing or does not match a wall.",
                        Recommendation = "Assign a valid host wall before modeling."
                    });
                }
                else if (item.Location == null)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "error",
                        ComponentGroup = group,
                        ComponentId = item.Id,
                        Rule = "opening_location",
                        Message = "Opening location is missing.",
                        Recommendation = "Confirm opening placement before modeling."
                    });
                }
                else
                {
                    double distance = DistancePointToSegment2D(item.Location, hostWall.Start, hostWall.End);
                    double tolerance = Math.Max(250.0, (hostWall.ThicknessMm ?? 0) * 0.75);
                    if (double.IsNaN(distance) || distance > tolerance)
                    {
                        review.Rows.Add(new ComplianceIssueRow
                        {
                            Severity = "warning",
                            ComponentGroup = group,
                            ComponentId = item.Id,
                            Rule = "host_wall_proximity",
                            Message = "Opening location is not close to its host wall baseline.",
                            Recommendation = "Verify host_wall_id and opening placement; distance is " + Math.Round(distance).ToString(CultureInfo.InvariantCulture) + " mm."
                        });
                    }
                }
                if (!item.WidthMm.HasValue || item.WidthMm.Value <= 0)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = group,
                        ComponentId = item.Id,
                        Rule = "ai_auto_fix_required:opening_width",
                        Message = "Opening width_mm is missing or invalid.",
                        Recommendation = "AI should infer or repair width_mm from door/window schedule, nearby annotation, family requirement, or similar confirmed openings before final execution.",
                        EvidenceType = "ai_auto_fix_required",
                        NeedsHumanDecision = false,
                        AutoFixRecommended = true
                    });
                }
                if (!item.HeightMm.HasValue || item.HeightMm.Value <= 0)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = group,
                        ComponentId = item.Id,
                        Rule = "ai_auto_fix_required:opening_height",
                        Message = "Opening height_mm is missing.",
                        Recommendation = allowDoorHeightDefault ? "AI may repair door height from schedule/similar doors or use the configured project default, then re-run conflict checks." : "AI should infer window height from schedule/elevation/similar windows, then re-run conflict checks.",
                        EvidenceType = "ai_auto_fix_required",
                        NeedsHumanDecision = false,
                        AutoFixRecommended = true
                    });
                }
            }
        }

        private static void AddFloorOpeningGeometryRows(ComplianceReview review, FloorOpeningComponent opening, SlabComponent hostSlab)
        {
            List<Point3> slabBoundary = NormalizeComplianceBoundary(hostSlab.Boundary);
            if (slabBoundary.Count < 3)
            {
                return;
            }

            List<Point3> openingBoundary = NormalizeComplianceBoundary(opening.Boundary);
            if (openingBoundary.Count >= 3 && openingBoundary.Any(point => !PointInPolygon2D(point, slabBoundary)))
            {
                review.Rows.Add(new ComplianceIssueRow
                {
                    Severity = "error",
                    ComponentGroup = "floor_openings",
                    ComponentId = opening.Id,
                    Rule = "opening_inside_slab",
                    Message = "Floor opening boundary is not fully inside its host slab boundary.",
                    Recommendation = "Check host_floor_id and opening boundary before creating a floor sketch void."
                });
            }
            else if (opening.Location != null && !PointInPolygon2D(opening.Location, slabBoundary))
            {
                review.Rows.Add(new ComplianceIssueRow
                {
                    Severity = "error",
                    ComponentGroup = "floor_openings",
                    ComponentId = opening.Id,
                    Rule = "opening_inside_slab",
                    Message = "Floor opening location is outside its host slab boundary.",
                    Recommendation = "Check host_floor_id and opening location before creating the opening."
                });
            }
        }

        private static void AddColumnComplianceRows(ComplianceReview review, ColumnComponent column, List<SlabComponent> slabs)
        {
            if (column.Location == null)
            {
                review.Rows.Add(new ComplianceIssueRow
                {
                    Severity = "error",
                    ComponentGroup = "columns",
                    ComponentId = column.Id,
                    Rule = "column_location",
                    Message = "Column location is missing.",
                    Recommendation = "Confirm column placement before automatic modeling.",
                    EvidenceSources = "dxf_geometry,spatial_semantics"
                });
            }
            else
            {
                bool insideAnySlab = slabs
                    .Select(slab => NormalizeComplianceBoundary(slab.Boundary))
                    .Any(boundary => boundary.Count >= 3 && PointInPolygon2D(column.Location, boundary));
                if (slabs.Count > 0 && !insideAnySlab)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = "columns",
                        ComponentId = column.Id,
                        Rule = "column_slab_relation",
                        Message = "Column location is outside all known slab boundaries.",
                        Recommendation = "Verify grid/structural intent before modeling this column.",
                        EvidenceSources = "dxf_geometry,spatial_semantics"
                    });
                }
            }

            bool hasRoundSize = column.DiameterMm.HasValue && column.DiameterMm.Value > 0;
            bool hasRectSize = column.WidthMm.HasValue && column.WidthMm.Value > 0 && column.DepthMm.HasValue && column.DepthMm.Value > 0;
            if (!hasRoundSize && !hasRectSize)
            {
                review.Rows.Add(new ComplianceIssueRow
                {
                    Severity = "warning",
                    ComponentGroup = "columns",
                    ComponentId = column.Id,
                    Rule = "column_size",
                    Message = "Column size is incomplete; diameter_mm or width_mm/depth_mm is required for a reliable family type.",
                    Recommendation = "Confirm column section dimensions before modeling.",
                    EvidenceSources = "dxf_geometry,spatial_semantics,design_notes"
                });
            }
            if ((!column.HeightMm.HasValue || column.HeightMm.Value <= 0) && string.IsNullOrWhiteSpace(column.TopLevel) && !column.TopZMm.HasValue)
            {
                review.Rows.Add(new ComplianceIssueRow
                {
                    Severity = "warning",
                    ComponentGroup = "columns",
                    ComponentId = column.Id,
                    Rule = "column_height",
                    Message = "Column height/top constraint is missing.",
                    Recommendation = "Confirm top_level, top_z_mm, or height_mm before relying on generated column geometry.",
                    EvidenceSources = "spatial_semantics,design_notes"
                });
            }
        }

        private static void AddMaterialComplianceRows(ComplianceReview review, StandardModel model)
        {
            foreach (ComponentBase item in AllComponents(model))
            {
                string group = ComponentGroupName(item);
                string material = GetComplianceMaterial(item);
                bool materialRelevant = item is WallComponent || item is ColumnComponent || item is SlabComponent || item is OpeningComponent;

                if (materialRelevant && string.IsNullOrWhiteSpace(material))
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = group,
                        ComponentId = item.Id,
                        Rule = "material_missing",
                        Message = "Component has no material after material-recognition stage.",
                        Recommendation = "Use design notes, specification documents, or manual review to confirm material before final modeling.",
                        EvidenceSources = "material_semantics,design_notes,specification_documents"
                    });
                }

                if (item.MaterialNeedsReview.HasValue && item.MaterialNeedsReview.Value)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = group,
                        ComponentId = item.Id,
                        Rule = "material_needs_review",
                        Message = "Material recognition marked this component for review.",
                        Recommendation = HasText(item.MaterialReason) ? item.MaterialReason : "Confirm material evidence before final modeling.",
                        EvidenceSources = "material_semantics,design_notes"
                    });
                }

                if (string.Equals(item.MaterialSource, "ai_suggested_default", StringComparison.OrdinalIgnoreCase))
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = group,
                        ComponentId = item.Id,
                        Rule = "material_defaulted",
                        Message = "Material was filled by AI default rather than explicit design notes or specification evidence.",
                        Recommendation = "Confirm the material manually or connect design-note/specification evidence.",
                        EvidenceSources = "material_semantics,design_notes,specification_documents"
                    });
                }

                if (item.MaterialConfidence.HasValue && item.MaterialConfidence.Value < 0.80)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = group,
                        ComponentId = item.Id,
                        Rule = "material_low_confidence",
                        Message = "Material recognition confidence is below 0.80.",
                        Recommendation = "Check material evidence before creating or assigning Revit materials.",
                        EvidenceSources = "material_semantics"
                    });
                }
            }
        }

        private static void AddDesignSpecificationRows(ComplianceReview review, ComplianceEvidenceBundle evidence)
        {
            ComplianceEvidenceSource designNotes = evidence.Sources.FirstOrDefault(source => source.SourceKey == "design_notes");
            ComplianceEvidenceSource specifications = evidence.Sources.FirstOrDefault(source => source.SourceKey == "specification_documents");

            if (designNotes != null && !string.Equals(designNotes.Status, "available", StringComparison.OrdinalIgnoreCase))
            {
                review.Rows.Add(new ComplianceIssueRow
                {
                    Severity = "info",
                    ComponentGroup = "design_notes",
                    ComponentId = "project",
                    Rule = "design_notes_missing",
                    Message = "Design general notes are not connected as project-level evidence.",
                    Recommendation = "When available, provide design_note_summary so material, height, thickness, and type checks can cite the notes directly.",
                    EvidenceSources = "design_notes"
                });
            }

            if (specifications != null && !string.Equals(specifications.Status, "available", StringComparison.OrdinalIgnoreCase))
            {
                review.Rows.Add(new ComplianceIssueRow
                {
                    Severity = "info",
                    ComponentGroup = "specification_documents",
                    ComponentId = "project",
                    Rule = "specification_missing",
                    Message = "Specification/code documents are not connected as project-level evidence.",
                    Recommendation = "When available, provide specification_summary or parsed rule rows for code-level compliance checks.",
                    EvidenceSources = "specification_documents"
                });
            }
        }

        private static void AddDesignNoteConsistencyRows(ComplianceReview review, ComplianceEvidenceBundle evidence)
        {
            StandardModel model = evidence.Model;
            string designText = BuildDesignRequirementText(model);
            if (!HasText(designText))
            {
                return;
            }

            List<DesignRequirement> requirements = ExtractDesignRequirements(designText);
            foreach (DesignRequirement requirement in requirements)
            {
                switch (requirement.Rule)
                {
                    case "door_width":
                        AddNumericRequirementConflicts(review, requirement, "doors", model.Components.Doors, opening => opening.WidthMm);
                        break;
                    case "door_height":
                        AddNumericRequirementConflicts(review, requirement, "doors", model.Components.Doors, opening => opening.HeightMm);
                        break;
                    case "window_width":
                        AddNumericRequirementConflicts(review, requirement, "windows", model.Components.Windows, opening => opening.WidthMm);
                        break;
                    case "window_height":
                        AddNumericRequirementConflicts(review, requirement, "windows", model.Components.Windows, opening => opening.HeightMm);
                        break;
                    case "sill_height":
                        AddNumericRequirementConflicts(review, requirement, "windows", model.Components.Windows, opening => opening.SillHeightMm);
                        break;
                    case "wall_thickness":
                        AddNumericRequirementConflicts(review, requirement, "walls", model.Components.Walls, wall => wall.ThicknessMm);
                        break;
                    case "slab_thickness":
                        AddNumericRequirementConflicts(review, requirement, "slabs", model.Components.Slabs, slab => slab.ThicknessMm);
                        break;
                    case "floor_height":
                        AddFloorHeightRequirementRows(review, requirement, model);
                        break;
                    case "railing_height":
                    case "handrail_height":
                        AddMissingSemanticEvidenceRow(review, requirement, "stairs_or_railings", "Design notes specify railing/handrail height, but the current Revit input schema has no railing or handrail component evidence to compare.");
                        break;
                    case "stair_riser_height":
                    case "stair_tread_depth":
                        AddMissingSemanticEvidenceRow(review, requirement, "stairs", "Design notes specify stair dimensions, but no stair component dimensions are available in the current Revit modeling input.");
                        break;
                    case "roof_slope":
                        AddMissingSemanticEvidenceRow(review, requirement, "roofs", "Design notes specify roof slope, but roof slope evidence is not available in the current Revit modeling input.");
                        break;
                    case "ramp_slope":
                        AddMissingSemanticEvidenceRow(review, requirement, "ramps", "Design notes specify ramp slope, but ramp slope evidence is not available in the current Revit modeling input.");
                        break;
                }
            }

            AddMaterialDesignNoteConflictRows(review, model, designText);
        }

        private static string BuildDesignRequirementText(StandardModel model)
        {
            List<string> parts = new List<string>();
            if (model != null && model.Project != null)
            {
                if (HasText(model.Project.DesignNoteSummary)) parts.Add(model.Project.DesignNoteSummary);
                if (HasText(model.Project.SpecificationSummary)) parts.Add(model.Project.SpecificationSummary);
            }
            return string.Join("\n", parts);
        }

        private static List<DesignRequirement> ExtractDesignRequirements(string text)
        {
            List<DesignRequirement> requirements = new List<DesignRequirement>();
            if (!HasText(text))
            {
                return requirements;
            }

            AddDimensionPairRequirements(requirements, text, "door_width", "door_height", "door", "门", "doors");
            AddDimensionPairRequirements(requirements, text, "window_width", "window_height", "window", "窗", "windows");
            AddKeywordNumberRequirements(requirements, text, "sill_height", "windows", "窗台", "sill", 30);
            AddKeywordNumberRequirements(requirements, text, "wall_thickness", "walls", "墙厚", "wall thickness", 20);
            AddKeywordNumberRequirements(requirements, text, "wall_thickness", "walls", "外墙", "exterior wall", 20);
            AddKeywordNumberRequirements(requirements, text, "wall_thickness", "walls", "内墙", "interior wall", 20);
            AddKeywordNumberRequirements(requirements, text, "slab_thickness", "slabs", "楼板", "slab", 15);
            AddKeywordNumberRequirements(requirements, text, "slab_thickness", "slabs", "板厚", "slab thickness", 15);
            AddKeywordNumberRequirements(requirements, text, "floor_height", "levels", "层高", "floor height", 50);
            AddKeywordNumberRequirements(requirements, text, "railing_height", "stairs_or_railings", "栏杆", "railing", 30);
            AddKeywordNumberRequirements(requirements, text, "handrail_height", "stairs_or_railings", "扶手", "handrail", 30);
            AddKeywordNumberRequirements(requirements, text, "stair_riser_height", "stairs", "踏步高", "stair riser", 10);
            AddKeywordNumberRequirements(requirements, text, "stair_tread_depth", "stairs", "踏步宽", "stair tread", 10);
            AddKeywordNumberRequirements(requirements, text, "roof_slope", "roofs", "屋面坡度", "roof slope", 0.5);
            AddKeywordNumberRequirements(requirements, text, "ramp_slope", "ramps", "坡道坡度", "ramp slope", 0.5);

            return requirements
                .GroupBy(item => item.Rule + "|" + item.ComponentGroup + "|" + item.DesignValue + "|" + item.Unit, StringComparer.OrdinalIgnoreCase)
                .Select(group => group.First())
                .ToList();
        }

        private static void AddDimensionPairRequirements(List<DesignRequirement> requirements, string text, string widthRule, string heightRule, string englishName, string chineseKeyword, string componentGroup)
        {
            string pattern = Regex.Escape(chineseKeyword) + @"[^。；;\n]{0,30}?(\d+(?:\.\d+)?)\s*(?:mm|毫米|m|米)?\s*[xX×*]\s*(\d+(?:\.\d+)?)\s*(mm|毫米|m|米)?";
            foreach (Match match in Regex.Matches(text, pattern, RegexOptions.IgnoreCase))
            {
                double? width = ParseRequirementNumber(match.Groups[1].Value, match.Groups[3].Value);
                double? height = ParseRequirementNumber(match.Groups[2].Value, match.Groups[3].Value);
                if (width.HasValue)
                {
                    requirements.Add(new DesignRequirement
                    {
                        Rule = widthRule,
                        ComponentGroup = componentGroup,
                        DesignValue = width.Value,
                        Unit = "mm",
                        Tolerance = 30,
                        EvidenceText = ShortEvidence(match.Value),
                        Description = englishName + " width from design notes"
                    });
                }
                if (height.HasValue)
                {
                    requirements.Add(new DesignRequirement
                    {
                        Rule = heightRule,
                        ComponentGroup = componentGroup,
                        DesignValue = height.Value,
                        Unit = "mm",
                        Tolerance = 30,
                        EvidenceText = ShortEvidence(match.Value),
                        Description = englishName + " height from design notes"
                    });
                }
            }
        }

        private static void AddKeywordNumberRequirements(List<DesignRequirement> requirements, string text, string rule, string componentGroup, string keyword, string description, double tolerance)
        {
            string pattern = Regex.Escape(keyword) + @"[^。；;\n]{0,24}?(\d+(?:\.\d+)?)\s*(mm|毫米|m|米|%|％)?";
            foreach (Match match in Regex.Matches(text, pattern, RegexOptions.IgnoreCase))
            {
                string unit = match.Groups[2].Value;
                double? value = ParseRequirementNumber(match.Groups[1].Value, unit);
                if (!value.HasValue)
                {
                    continue;
                }
                if ((rule == "roof_slope" || rule == "ramp_slope") && (unit == "%" || unit == "％"))
                {
                    requirements.Add(new DesignRequirement
                    {
                        Rule = rule,
                        ComponentGroup = componentGroup,
                        DesignValue = value.Value,
                        Unit = "%",
                        Tolerance = tolerance,
                        EvidenceText = ShortEvidence(match.Value),
                        Description = description
                    });
                    continue;
                }
                requirements.Add(new DesignRequirement
                {
                    Rule = rule,
                    ComponentGroup = componentGroup,
                    DesignValue = value.Value,
                    Unit = "mm",
                    Tolerance = tolerance,
                    EvidenceText = ShortEvidence(match.Value),
                    Description = description
                });
            }
        }

        private static double? ParseRequirementNumber(string raw, string unit)
        {
            double value;
            if (!double.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out value))
            {
                return null;
            }
            string normalizedUnit = (unit ?? "").Trim().ToLowerInvariant();
            if (normalizedUnit == "m" || normalizedUnit == "米")
            {
                return value * 1000.0;
            }
            return value;
        }

        private static void AddNumericRequirementConflicts<T>(ComplianceReview review, DesignRequirement requirement, string group, List<T> items, Func<T, double?> valueSelector) where T : ComponentBase
        {
            int compared = 0;
            foreach (T item in items ?? new List<T>())
            {
                double? actual = valueSelector(item);
                if (!actual.HasValue || actual.Value <= 0)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = group,
                        ComponentId = item.Id,
                        Rule = "missing_required_evidence:" + requirement.Rule,
                        Message = "Design notes contain a requirement, but the matching drawing/model value is missing.",
                        Recommendation = "Send this item to manual review; do not treat default values as design evidence.",
                        EvidenceSources = "design_notes,dxf_geometry,spatial_semantics",
                        EvidenceType = "missing_required_evidence",
                        DesignNoteValue = FormatRequirementValue(requirement),
                        Tolerance = FormatNumber(requirement.Tolerance),
                        NeedsHumanDecision = true
                    });
                    compared++;
                    continue;
                }

                double difference = Math.Abs(actual.Value - requirement.DesignValue);
                if (difference > requirement.Tolerance)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = group,
                        ComponentId = item.Id,
                        Rule = "multi_source_conflict:" + requirement.Rule,
                        Message = "Design notes and drawing/model value are inconsistent: " + requirement.Description + ".",
                        Recommendation = "Manual reviewer should decide whether to follow the design notes or the drawing/model value before modeling.",
                        EvidenceSources = "design_notes,dxf_geometry,spatial_semantics",
                        EvidenceType = "multi_source_conflict",
                        DesignNoteValue = FormatRequirementValue(requirement),
                        DrawingValue = FormatNumber(actual.Value) + " mm",
                        Difference = FormatNumber(difference) + " mm",
                        Tolerance = FormatNumber(requirement.Tolerance) + " mm",
                        NeedsHumanDecision = true
                    });
                    compared++;
                    if (compared >= 25)
                    {
                        return;
                    }
                }
            }
        }

        private static void AddFloorHeightRequirementRows(ComplianceReview review, DesignRequirement requirement, StandardModel model)
        {
            List<LevelComponent> levels = (model.Components.Levels ?? new List<LevelComponent>())
                .Where(level => level != null)
                .OrderBy(level => level.ElevationMm)
                .ToList();
            if (levels.Count < 2)
            {
                AddMissingSemanticEvidenceRow(review, requirement, "levels", "Design notes specify floor height, but fewer than two usable levels are available for comparison.");
                return;
            }

            for (int i = 0; i < levels.Count - 1; i++)
            {
                double actual = levels[i + 1].ElevationMm - levels[i].ElevationMm;
                double difference = Math.Abs(actual - requirement.DesignValue);
                if (difference > requirement.Tolerance)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = "levels",
                        ComponentId = levels[i].Id + "->" + levels[i + 1].Id,
                        Rule = "multi_source_conflict:floor_height",
                        Message = "Design notes floor height conflicts with level elevation interval.",
                        Recommendation = "Confirm the project level system before creating level-hosted elements.",
                        EvidenceSources = "design_notes,dxf_geometry,spatial_semantics",
                        EvidenceType = "multi_source_conflict",
                        DesignNoteValue = FormatRequirementValue(requirement),
                        DrawingValue = FormatNumber(actual) + " mm",
                        Difference = FormatNumber(difference) + " mm",
                        Tolerance = FormatNumber(requirement.Tolerance) + " mm",
                        NeedsHumanDecision = true
                    });
                }
            }
        }

        private static void AddMissingSemanticEvidenceRow(ComplianceReview review, DesignRequirement requirement, string componentGroup, string message)
        {
            review.Rows.Add(new ComplianceIssueRow
            {
                Severity = "warning",
                ComponentGroup = componentGroup,
                ComponentId = "project",
                Rule = "missing_required_evidence:" + requirement.Rule,
                Message = message,
                Recommendation = "Keep this item in the manual report until a drawing/model source can confirm or reject the design-note requirement.",
                EvidenceSources = "design_notes,dxf_geometry,spatial_semantics",
                EvidenceType = "missing_required_evidence",
                DesignNoteValue = FormatRequirementValue(requirement),
                Tolerance = requirement.Unit == "%" ? FormatNumber(requirement.Tolerance) + "%" : FormatNumber(requirement.Tolerance) + " mm",
                NeedsHumanDecision = true
            });
        }

        private static void AddMaterialDesignNoteConflictRows(ComplianceReview review, StandardModel model, string designText)
        {
            string expectedDoorMaterial = FindExpectedMaterial(designText, "门");
            if (HasText(expectedDoorMaterial))
            {
                AddMaterialConflictRows(review, "doors", model.Components.Doors, expectedDoorMaterial);
            }

            string expectedWindowMaterial = FindExpectedMaterial(designText, "窗");
            if (HasText(expectedWindowMaterial))
            {
                AddMaterialConflictRows(review, "windows", model.Components.Windows, expectedWindowMaterial);
            }

            string expectedWallMaterial = FindExpectedMaterial(designText, "墙");
            if (HasText(expectedWallMaterial))
            {
                AddMaterialConflictRows(review, "walls", model.Components.Walls, expectedWallMaterial);
            }
        }

        private static void AddMaterialConflictRows<T>(ComplianceReview review, string group, List<T> items, string expectedMaterial) where T : ComponentBase
        {
            foreach (T item in items ?? new List<T>())
            {
                string actual = GetComplianceMaterial(item);
                if (!HasText(actual))
                {
                    continue;
                }
                if (!MaterialsMatch(expectedMaterial, actual))
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = group,
                        ComponentId = item.Id,
                        Rule = "multi_source_conflict:material",
                        Message = "Design notes material expectation conflicts with model material.",
                        Recommendation = "Manual reviewer should confirm whether the material-recognition result or the design notes should control.",
                        EvidenceSources = "design_notes,material_semantics",
                        EvidenceType = "multi_source_conflict",
                        DesignNoteValue = expectedMaterial,
                        DrawingValue = actual,
                        NeedsHumanDecision = true
                    });
                }
            }
        }

        private static string FindExpectedMaterial(string text, string componentKeyword)
        {
            string pattern = Regex.Escape(componentKeyword) + @"[^。；;\n]{0,40}?(混凝土|砼|砖|木|木质|铝合金|铝|钢|玻璃|concrete|brick|masonry|wood|aluminum|aluminium|steel|glass)";
            Match match = Regex.Match(text ?? "", pattern, RegexOptions.IgnoreCase);
            return match.Success ? NormalizeMaterialKeyword(match.Groups[1].Value) : "";
        }

        private static string NormalizeMaterialKeyword(string value)
        {
            string text = (value ?? "").Trim().ToLowerInvariant();
            if (text == "砼" || text.Contains("混凝土") || text.Contains("concrete")) return "Concrete";
            if (text.Contains("砖") || text.Contains("brick") || text.Contains("masonry")) return "Brick/Masonry";
            if (text.Contains("木") || text.Contains("wood")) return "Wood";
            if (text.Contains("铝") || text.Contains("aluminum") || text.Contains("aluminium")) return "Aluminum";
            if (text.Contains("钢") || text.Contains("steel")) return "Steel";
            if (text.Contains("玻璃") || text.Contains("glass")) return "Glass";
            return value;
        }

        private static bool MaterialsMatch(string expected, string actual)
        {
            string left = NormalizeMaterialKeyword(expected).ToLowerInvariant();
            string right = NormalizeMaterialKeyword(actual).ToLowerInvariant();
            if (left == right) return true;
            if (left.Contains("brick") && (right.Contains("brick") || right.Contains("masonry"))) return true;
            return right.Contains(left) || left.Contains(right);
        }

        private static void AddFinalEvidenceAuditRows(ComplianceReview review, StandardModel model)
        {
            foreach (ComponentBase item in AllComponents(model))
            {
                string group = ComponentGroupName(item);
                if (IsDefaultOrInferred(item))
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = "warning",
                        ComponentGroup = group,
                        ComponentId = item.Id,
                        Rule = "low_trust_default_or_inferred",
                        Message = "Component contains defaulted or inferred evidence rather than explicit drawing/design-note evidence.",
                        Recommendation = "Keep this component in the manual report if it controls geometry, material, or code compliance.",
                        EvidenceSources = "dxf_geometry,material_semantics,spatial_semantics",
                        EvidenceType = "low_trust_default_or_low_confidence",
                        DrawingValue = BuildEvidenceAuditValue(item),
                        NeedsHumanDecision = true
                    });
                }

                if (item.Confidence > 0 && item.Confidence < 0.75)
                {
                    review.Rows.Add(new ComplianceIssueRow
                    {
                        Severity = IsCriticalComponent(item) ? "warning" : "info",
                        ComponentGroup = group,
                        ComponentId = item.Id,
                        Rule = "low_confidence_evidence",
                        Message = "Component recognition confidence is below 0.75.",
                        Recommendation = IsCriticalComponent(item) ? "Manual review is required before using this component for final modeling or compliance decisions." : "Review if this component affects final model quality.",
                        EvidenceSources = "dxf_geometry,spatial_semantics",
                        EvidenceType = "low_trust_default_or_low_confidence",
                        DrawingValue = "confidence=" + item.Confidence.ToString("0.##", CultureInfo.InvariantCulture),
                        NeedsHumanDecision = IsCriticalComponent(item)
                    });
                }
            }
        }

        private static bool IsDefaultOrInferred(ComponentBase item)
        {
            string text = ((item.Source ?? "") + " " + (item.Type ?? "") + " " + (item.Notes ?? "") + " " + (item.MaterialSource ?? "") + " " + (item.MaterialReason ?? "")).ToLowerInvariant();
            return text.Contains("default") || text.Contains("inferred") || text.Contains("fallback") || text.Contains("ai_suggested_default") || text.Contains("默认") || text.Contains("推断");
        }

        private static bool IsCriticalComponent(ComponentBase item)
        {
            return item is WallComponent || item is ColumnComponent || item is SlabComponent || item is FloorOpeningComponent || item is OpeningComponent;
        }

        private static string BuildEvidenceAuditValue(ComponentBase item)
        {
            List<string> parts = new List<string>();
            if (HasText(item.Source)) parts.Add("source=" + item.Source);
            if (HasText(item.Type)) parts.Add("type=" + item.Type);
            if (HasText(item.Notes)) parts.Add("notes=" + item.Notes);
            if (HasText(item.MaterialSource)) parts.Add("material_source=" + item.MaterialSource);
            if (HasText(item.MaterialReason)) parts.Add("material_reason=" + item.MaterialReason);
            return string.Join("; ", parts);
        }

        private static string FormatRequirementValue(DesignRequirement requirement)
        {
            return FormatNumber(requirement.DesignValue) + (requirement.Unit == "%" ? "%" : " mm") + (HasText(requirement.EvidenceText) ? " | " + requirement.EvidenceText : "");
        }

        private static string FormatNumber(double value)
        {
            return Math.Round(value, 2).ToString("0.##", CultureInfo.InvariantCulture);
        }

        private static string ShortEvidence(string value)
        {
            value = Regex.Replace(value ?? "", @"\s+", " ").Trim();
            return value.Length <= 120 ? value : value.Substring(0, 120);
        }

        private static List<Point3> NormalizeComplianceBoundary(List<Point3> boundary)
        {
            if (boundary == null)
            {
                return new List<Point3>();
            }
            List<Point3> points = boundary.Where(point => point != null).ToList();
            if (points.Count > 1 && IsSamePoint2D(points.First(), points.Last()))
            {
                points.RemoveAt(points.Count - 1);
            }
            return points;
        }

        private static bool IsSamePoint2D(Point3 a, Point3 b)
        {
            if (a == null || b == null)
            {
                return false;
            }
            return Math.Abs(a.X - b.X) <= 1.0 && Math.Abs(a.Y - b.Y) <= 1.0;
        }

        private static double DistancePointToSegment2D(Point3 point, Point3 start, Point3 end)
        {
            if (point == null || start == null || end == null)
            {
                return double.NaN;
            }
            double dx = end.X - start.X;
            double dy = end.Y - start.Y;
            double lengthSquared = dx * dx + dy * dy;
            if (lengthSquared <= 0.0001)
            {
                return double.NaN;
            }
            double t = ((point.X - start.X) * dx + (point.Y - start.Y) * dy) / lengthSquared;
            t = Math.Max(0.0, Math.Min(1.0, t));
            double projectionX = start.X + t * dx;
            double projectionY = start.Y + t * dy;
            double offsetX = point.X - projectionX;
            double offsetY = point.Y - projectionY;
            return Math.Sqrt(offsetX * offsetX + offsetY * offsetY);
        }

        private static bool PointInPolygon2D(Point3 point, List<Point3> polygon)
        {
            if (point == null || polygon == null || polygon.Count < 3)
            {
                return false;
            }

            bool inside = false;
            for (int i = 0, j = polygon.Count - 1; i < polygon.Count; j = i++)
            {
                Point3 pi = polygon[i];
                Point3 pj = polygon[j];
                if (PointOnSegment2D(point, pj, pi))
                {
                    return true;
                }
                bool intersects = ((pi.Y > point.Y) != (pj.Y > point.Y)) &&
                    (point.X < (pj.X - pi.X) * (point.Y - pi.Y) / (pj.Y - pi.Y) + pi.X);
                if (intersects)
                {
                    inside = !inside;
                }
            }
            return inside;
        }

        private static bool PointOnSegment2D(Point3 point, Point3 start, Point3 end)
        {
            double cross = (point.Y - start.Y) * (end.X - start.X) - (point.X - start.X) * (end.Y - start.Y);
            if (Math.Abs(cross) > 1.0)
            {
                return false;
            }
            double dot = (point.X - start.X) * (end.X - start.X) + (point.Y - start.Y) * (end.Y - start.Y);
            if (dot < 0)
            {
                return false;
            }
            double lengthSquared = (end.X - start.X) * (end.X - start.X) + (end.Y - start.Y) * (end.Y - start.Y);
            return dot <= lengthSquared;
        }

        private static int CountSpatialEvidence(StandardModel model)
        {
            int count = 0;
            count += (model.Components.Levels ?? new List<LevelComponent>()).Count(level => HasText(level.Name));
            count += (model.Components.Walls ?? new List<WallComponent>()).Count(wall => HasText(wall.BaseLevel) || HasText(wall.TopLevel) || wall.HeightMm.HasValue || wall.ThicknessMm.HasValue);
            count += (model.Components.Columns ?? new List<ColumnComponent>()).Count(column => HasText(column.Level) || HasText(column.TopLevel) || column.BaseZMm.HasValue || column.TopZMm.HasValue || column.HeightMm.HasValue);
            count += (model.Components.Slabs ?? new List<SlabComponent>()).Count(slab => HasText(slab.Level) || slab.ThicknessMm.HasValue || slab.ElevationMm.HasValue);
            count += (model.Components.FloorOpenings ?? new List<FloorOpeningComponent>()).Count(opening => HasText(opening.Level) || HasText(opening.HostFloorId) || opening.WidthMm.HasValue || opening.DepthMm.HasValue);
            count += (model.Components.Doors ?? new List<OpeningComponent>()).Count(opening => HasText(opening.Level) || HasText(opening.HostWallId) || opening.WidthMm.HasValue || opening.HeightMm.HasValue);
            count += (model.Components.Windows ?? new List<OpeningComponent>()).Count(opening => HasText(opening.Level) || HasText(opening.HostWallId) || opening.WidthMm.HasValue || opening.HeightMm.HasValue || opening.SillHeightMm.HasValue);
            return count;
        }

        private static bool HasMaterialEvidence(ComponentBase item)
        {
            return HasText(GetComplianceMaterial(item)) ||
                HasText(item.MaterialSource) ||
                HasText(item.MaterialEvidence) ||
                item.MaterialConfidence.HasValue ||
                item.MaterialNeedsReview.HasValue ||
                HasText(item.MaterialReason);
        }

        private static string GetComplianceMaterial(ComponentBase item)
        {
            if (item is WallComponent wall)
            {
                if (!string.IsNullOrWhiteSpace(wall.MaterialName)) return wall.MaterialName;
                if (!string.IsNullOrWhiteSpace(wall.Material)) return wall.Material;
                if (wall.FinishMaterials != null)
                {
                    return wall.FinishMaterials.FirstOrDefault(value => !string.IsNullOrWhiteSpace(value)) ?? "";
                }
                return "";
            }
            if (item is ColumnComponent column) return column.Material;
            if (item is SlabComponent slab) return slab.Material;
            if (item is OpeningComponent opening) return opening.Material;
            return "";
        }

        private static bool HasText(string value)
        {
            return !string.IsNullOrWhiteSpace(value);
        }

        private static bool IsAutoFixableDataIssue(string message)
        {
            string text = (message ?? "").Trim().ToLowerInvariant();
            if (string.IsNullOrWhiteSpace(text))
            {
                return false;
            }
            return text.Contains("invalid numeric field") ||
                text.Contains("missing numeric field") ||
                text.Contains("height_mm") ||
                text.Contains("width_mm") ||
                text.Contains("sill_height_mm") ||
                text.Contains("thickness_mm") ||
                text.Contains("depth_mm");
        }

        private static bool ShowComplianceReview(ComplianceReview review)
        {
            int errors = review.Rows.Count(row => string.Equals(row.Severity, "error", StringComparison.OrdinalIgnoreCase));
            int warnings = review.Rows.Count(row => string.Equals(row.Severity, "warning", StringComparison.OrdinalIgnoreCase));
            int info = review.Rows.Count(row => string.Equals(row.Severity, "info", StringComparison.OrdinalIgnoreCase));

            StringBuilder sb = new StringBuilder();
            sb.AppendLine("Stage 2: Compliance check before modeling");
            sb.AppendLine();
            sb.AppendLine("Errors: " + errors.ToString(CultureInfo.InvariantCulture));
            sb.AppendLine("Warnings: " + warnings.ToString(CultureInfo.InvariantCulture));
            sb.AppendLine("Info: " + info.ToString(CultureInfo.InvariantCulture));
            sb.AppendLine();
            foreach (ComplianceIssueRow row in review.Rows.OrderByDescending(r => RiskWeight(r.Severity)).Take(12))
            {
                sb.AppendLine(row.Severity.ToUpperInvariant() + " " + row.ComponentGroup + "/" + row.ComponentId + ": " + row.Message);
            }
            sb.AppendLine();
            sb.AppendLine("Reports written before modeling: compliance_review_report.csv and compliance_review_report.json");
            sb.AppendLine("Choose how this compliance result should gate modeling.");

            TaskDialog dialog = new TaskDialog("AI Revit Compliance Check");
            dialog.MainInstruction = "Choose how to handle this compliance review";
            dialog.MainContent = sb.ToString();
            dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "Do not model", "Prevent the affected components from entering automatic Revit modelling.");
            dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink2, "Let AI decide", "Let AI evaluate the evidence and rules, then continue or attempt a correction.");
            dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink3, "Decide manually", "Open the natural-language review flow and apply the reviewer’s instructions.");
            dialog.CommonButtons = TaskDialogCommonButtons.Cancel;
            dialog.DefaultButton = errors > 0 ? TaskDialogResult.CommandLink1 : TaskDialogResult.CommandLink2;

            TaskDialogResult result = dialog.Show();
            if (result == TaskDialogResult.CommandLink1 || result == TaskDialogResult.Cancel)
            {
                review.UserDecision = "do_not_model";
                return false;
            }
            if (result == TaskDialogResult.CommandLink3)
            {
                review.UserDecision = "human_decide";
                return true;
            }
            review.UserDecision = "ai_decide";
            return true;
        }

        private static string NormalizeSeverity(string severity)
        {
            string value = (severity ?? "").Trim().ToLowerInvariant();
            if (value == "error") return "error";
            if (value == "warning" || value == "needs_review") return "warning";
            return "info";
        }

        private static int RiskWeight(string value)
        {
            string normalized = (value ?? "").Trim().ToLowerInvariant();
            if (normalized == "error" || normalized == "high") return 3;
            if (normalized == "warning" || normalized == "medium") return 2;
            return 1;
        }

        private static string ComponentGroupName(ComponentBase item)
        {
            if (item is LevelComponent) return "levels";
            if (item is GridComponent) return "grids";
            if (item is ColumnComponent) return "columns";
            if (item is WallComponent) return "walls";
            if (item is SlabComponent) return "slabs";
            if (item is FloorOpeningComponent) return "floor_openings";
            if (item is RoomComponent) return "rooms";
            if (item is GenericModelComponent generic)
            {
                string type = (generic.Type ?? "").ToLowerInvariant();
                if (type.Contains("stair")) return "stairs";
                if (type.Contains("railing") || type.Contains("handrail")) return "railings";
                if (type.Contains("roof")) return "roofs";
            }
            if (item is OpeningComponent opening)
            {
                return string.Equals(opening.Type, "window", StringComparison.OrdinalIgnoreCase) ? "windows" : "openings";
            }
            return "components";
        }

    }

    public class ComplianceReview
    {
        public List<ComplianceEvidenceSource> Sources { get; set; }
        public List<ComplianceIssueRow> Rows { get; set; }
        public string UserDecision { get; set; }

        public void Write(string folder)
        {
            Directory.CreateDirectory(folder);
            JsonSerializerOptions options = new JsonSerializerOptions { WriteIndented = true };
            File.WriteAllText(Path.Combine(folder, "compliance_review_report.json"), JsonSerializer.Serialize(this, options), Encoding.UTF8);
            File.WriteAllText(Path.Combine(folder, "compliance_review_report.csv"), BuildCsv(), Encoding.UTF8);
        }

        private string BuildCsv()
        {
            StringBuilder sb = new StringBuilder();
            sb.AppendLine("severity,component_group,component_id,rule,evidence_type,message,recommendation,design_note_value,drawing_value,difference,tolerance,needs_human_decision,auto_fix_recommended,evidence_sources");
            foreach (ComplianceIssueRow row in Rows ?? new List<ComplianceIssueRow>())
            {
                sb.AppendLine(string.Join(",",
                    Escape(row.Severity),
                    Escape(row.ComponentGroup),
                    Escape(row.ComponentId),
                    Escape(row.Rule),
                    Escape(row.EvidenceType),
                    Escape(row.Message),
                    Escape(row.Recommendation),
                    Escape(row.DesignNoteValue),
                    Escape(row.DrawingValue),
                    Escape(row.Difference),
                    Escape(row.Tolerance),
                    Escape(row.NeedsHumanDecision.HasValue ? (row.NeedsHumanDecision.Value ? "true" : "false") : ""),
                    Escape(row.AutoFixRecommended.HasValue ? (row.AutoFixRecommended.Value ? "true" : "false") : ""),
                    Escape(row.EvidenceSources)));
            }
            return sb.ToString();
        }

        private static string Escape(string value)
        {
            value = value ?? "";
            return "\"" + value.Replace("\"", "\"\"") + "\"";
        }
    }

    public class ComplianceIssueRow
    {
        public string Severity { get; set; }
        public string ComponentGroup { get; set; }
        public string ComponentId { get; set; }
        public string Rule { get; set; }
        public string Message { get; set; }
        public string Recommendation { get; set; }
        public string EvidenceSources { get; set; }
        public string EvidenceType { get; set; }
        public string DesignNoteValue { get; set; }
        public string DrawingValue { get; set; }
        public string Difference { get; set; }
        public string Tolerance { get; set; }
        public bool? NeedsHumanDecision { get; set; }
        public bool? AutoFixRecommended { get; set; }
    }

    public class DesignRequirement
    {
        public string Rule { get; set; }
        public string ComponentGroup { get; set; }
        public double DesignValue { get; set; }
        public string Unit { get; set; }
        public double Tolerance { get; set; }
        public string EvidenceText { get; set; }
        public string Description { get; set; }
    }

    public class ComplianceEvidenceBundle
    {
        public StandardModel Model { get; set; }
        public FamilyAssignmentPlan FamilyPlan { get; set; }
        public List<ComplianceEvidenceSource> Sources { get; set; }
    }

    public class ComplianceEvidenceSource
    {
        public string SourceKey { get; set; }
        public string SourceName { get; set; }
        public string Status { get; set; }
        public string Summary { get; set; }
    }

}


