using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace AiRevitModeling
{
    public partial class AiRevitModelingCommand
    {
        private static FamilyAssignmentPlan BuildFamilyAssignmentPlan(Document doc, StandardModel model, string familyLibraryFolder, List<FamilyLoadCandidate> familyCandidates)
        {
            FamilyAssignmentPlan plan = new FamilyAssignmentPlan
            {
                FamilyLibraryFolder = familyLibraryFolder ?? "",
                Rows = new List<FamilyAssignmentRow>()
            };

            AddOpeningFamilyAssignmentRows(plan, doc, "doors", BuiltInCategory.OST_Doors, model.Components.Doors, familyCandidates);
            AddOpeningFamilyAssignmentRows(plan, doc, "windows", BuiltInCategory.OST_Windows, model.Components.Windows, familyCandidates);

            AddColumnFamilyAssignmentRows(plan, doc, model.Components.Columns, familyCandidates);

            int slabCount = Count(model.Components.Slabs);
            if (slabCount > 0)
            {
                int floorTypeCount = new FilteredElementCollector(doc)
                    .OfClass(typeof(FloorType))
                    .GetElementCount();
                plan.Rows.Add(new FamilyAssignmentRow
                {
                    ComponentGroup = "slabs",
                    ComponentId = JoinComponentIds(model.Components.Slabs),
                    RequirementName = "Floor type template",
                    RequiredCount = slabCount,
                    ComponentIds = JoinComponentIds(model.Components.Slabs),
                    SourceFiles = SourceFilesFor(model.Components.Slabs),
                    Evidence = EvidenceFor(model.Components.Slabs),
                    CurrentStatus = floorTypeCount > 0 ? "loaded floor type available" : "missing floor type",
                    SelectedStrategy = floorTypeCount > 0 ? "duplicate loaded floor type and apply JSON thickness/material where possible" : "manual floor type required before modeling",
                    CandidateFiles = "",
                    RiskLevel = floorTypeCount > 0 ? "medium" : "high",
                    Confidence = floorTypeCount > 0 ? 0.72 : 0.25,
                    NeedsReview = floorTypeCount == 0,
                    UnbuildableReason = floorTypeCount > 0 ? "" : "no loaded floor type in current Revit project",
                    Notes = "Floor openings will be integrated into the host floor sketch."
                });
            }

            int wallCount = Count(model.Components.Walls);
            if (wallCount > 0)
            {
                int wallTypeCount = new FilteredElementCollector(doc)
                    .OfClass(typeof(WallType))
                    .Cast<WallType>()
                    .Count(t => t.Kind == WallKind.Basic);
                plan.Rows.Add(new FamilyAssignmentRow
                {
                    ComponentGroup = "walls",
                    ComponentId = JoinComponentIds(model.Components.Walls),
                    RequirementName = "Basic wall type template",
                    RequiredCount = wallCount,
                    ComponentIds = JoinComponentIds(model.Components.Walls),
                    SourceFiles = SourceFilesFor(model.Components.Walls),
                    Evidence = EvidenceFor(model.Components.Walls),
                    CurrentStatus = wallTypeCount > 0 ? "basic wall type available" : "missing basic wall type",
                    SelectedStrategy = wallTypeCount > 0 ? "duplicate/reuse basic wall type and apply thickness/material layers" : "manual wall type required before modeling",
                    CandidateFiles = "",
                    RiskLevel = wallTypeCount > 0 ? "low" : "high",
                    Confidence = wallTypeCount > 0 ? 0.9 : 0.25,
                    NeedsReview = wallTypeCount == 0,
                    UnbuildableReason = wallTypeCount > 0 ? "" : "no basic wall type in current Revit project",
                    Notes = "Wall family loading is not required because basic walls are system families."
                });
            }

            return plan;
        }

        private static void AddColumnFamilyAssignmentRows(FamilyAssignmentPlan plan, Document doc, List<ColumnComponent> items, List<FamilyLoadCandidate> familyCandidates)
        {
            List<FamilySymbol> symbols = new FilteredElementCollector(doc)
                .OfCategory(BuiltInCategory.OST_StructuralColumns)
                .OfClass(typeof(FamilySymbol))
                .Cast<FamilySymbol>()
                .ToList();

            foreach (ColumnComponent column in items ?? new List<ColumnComponent>())
            {
                if (column == null)
                {
                    continue;
                }

                string requirementName = BuildColumnRequirementName(column);
                List<FamilyAssignmentCandidate> assignmentCandidates = BuildAssignmentCandidates((familyCandidates ?? new List<FamilyLoadCandidate>())
                    .Where(c => c.Category == BuiltInCategory.OST_StructuralColumns &&
                        string.Equals(c.RequirementName, requirementName, StringComparison.OrdinalIgnoreCase))
                    .OrderByDescending(c => c.Score)
                    .Take(3)
                    .ToList());
                bool hasExactLoadedType = symbols.Any(s =>
                    string.Equals(s.Name, column.FamilyType, StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(s.Name, column.Type, StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(s.Name, BuildGeneratedColumnTypeName(column), StringComparison.OrdinalIgnoreCase));
                bool hasEditableTemplate = symbols.Any(s => CanSetColumnSize(s, column));
                bool candidateScoresClose = CandidateScoresClose(assignmentCandidates);
                string risk;
                string status;
                string strategy;

                if (hasExactLoadedType)
                {
                    risk = "low";
                    status = "loaded matching column type available";
                    strategy = "use loaded column family type";
                }
                else if (assignmentCandidates.Count > 0)
                {
                    risk = "medium";
                    status = "candidate column family files found";
                    strategy = "load highest-ranked column family candidate, then generate or reuse matching size type";
                }
                else if (hasEditableTemplate)
                {
                    risk = "medium";
                    status = "editable loaded column template available";
                    strategy = "generate " + BuildGeneratedColumnTypeName(column) + " from compatible loaded template";
                }
                else
                {
                    risk = "high";
                    status = "no reliable loaded column type or candidate family file";
                    strategy = "manual column family selection required";
                }

                plan.Rows.Add(new FamilyAssignmentRow
                {
                    ComponentGroup = "columns",
                    ComponentId = column.Id ?? "",
                    RequirementName = requirementName,
                    RequiredCount = 1,
                    ComponentIds = column.Id ?? "",
                    SourceFiles = column.Source ?? "",
                    Evidence = BuildColumnEvidence(column),
                    CurrentStatus = status,
                    SelectedStrategy = candidateScoresClose ? strategy + "; Top 3 candidates are close, manual visual review required" : strategy,
                    CandidateFiles = string.Join(" | ", assignmentCandidates.Select(c => c.FileName + " score=" + c.Score.ToString(CultureInfo.InvariantCulture) + " cache=" + c.SemanticCacheStatus + " reason=" + c.Reason)),
                    VisualReviewCandidates = assignmentCandidates.Count == 0 ? "" : string.Join(" | ", assignmentCandidates.Select(c => "#" + c.VisualReviewRank.ToString(CultureInfo.InvariantCulture) + " " + c.FileName)),
                    Candidates = assignmentCandidates,
                    RiskLevel = risk,
                    Confidence = ConfidenceFromRiskAndCandidates(risk, assignmentCandidates),
                    NeedsReview = !hasExactLoadedType || candidateScoresClose || assignmentCandidates.Any(c => c.NeedsSemanticDescription),
                    UnbuildableReason = symbols.Count == 0 && assignmentCandidates.Count == 0 ? "no loaded structural-column family symbol and no candidate .rfa file" : "",
                    Notes = "shape=" + BuildColumnShapeLabel(column) + "; size=" + BuildColumnSizeLabel(column) +
                        (string.IsNullOrWhiteSpace(column.Material) ? "" : "; material=" + column.Material)
                });
            }
        }

        private static string BuildColumnRequirementName(ColumnComponent column)
        {
            return "Column " + (column == null || string.IsNullOrWhiteSpace(column.Id) ? "unidentified" : column.Id);
        }

        private static string BuildColumnEvidence(ColumnComponent column)
        {
            if (column == null)
            {
                return "";
            }
            return "id=" + (column.Id ?? "") + "; type=" + (column.Type ?? "") + "; shape=" + BuildColumnShapeLabel(column) +
                "; size=" + BuildColumnSizeLabel(column) + "; material=" + (column.Material ?? "") +
                "; source=" + (column.Source ?? "") + "; confidence=" + column.Confidence.ToString("0.##", CultureInfo.InvariantCulture);
        }

        private static string BuildColumnShapeLabel(ColumnComponent column)
        {
            return column != null && column.DiameterMm.HasValue && column.DiameterMm.Value > 0 ? "round" : "rectangular";
        }

        private static string BuildColumnSizeLabel(ColumnComponent column)
        {
            if (column == null)
            {
                return "unknown";
            }
            if (column.DiameterMm.HasValue && column.DiameterMm.Value > 0)
            {
                return "D" + Math.Round(column.DiameterMm.Value).ToString(CultureInfo.InvariantCulture);
            }
            if (column.WidthMm.HasValue)
            {
                double depth = column.DepthMm ?? column.WidthMm.Value;
                return Math.Round(column.WidthMm.Value).ToString(CultureInfo.InvariantCulture) + "x" + Math.Round(depth).ToString(CultureInfo.InvariantCulture);
            }
            return "unknown";
        }

        private static void AddOpeningFamilyAssignmentRows(FamilyAssignmentPlan plan, Document doc, string group, BuiltInCategory category, List<OpeningComponent> items, List<FamilyLoadCandidate> familyCandidates)
        {
            List<OpeningFamilyRequirement> requirements = BuildOpeningFamilyRequirements(category, items);
            List<FamilySymbol> symbols = new FilteredElementCollector(doc)
                .OfCategory(category)
                .OfClass(typeof(FamilySymbol))
                .Cast<FamilySymbol>()
                .ToList();
            bool hasEditableTemplate = symbols.Any(s => CanSetTypeSize(s));

            foreach (OpeningFamilyRequirement requirement in requirements)
            {
                bool loadedSize = symbols.Any(s => SymbolMatchesSize(s, requirement.WidthMm, requirement.HeightMm));
                bool generatedExists = symbols.Any(s => string.Equals(s.Name, requirement.GeneratedName, StringComparison.OrdinalIgnoreCase));
                List<FamilyLoadCandidate> candidates = (familyCandidates ?? new List<FamilyLoadCandidate>())
                    .Where(c => c.Category == category && string.Equals(c.RequirementName, requirement.GeneratedName, StringComparison.OrdinalIgnoreCase))
                    .OrderByDescending(c => c.Score)
                    .Take(3)
                    .ToList();
                List<FamilyAssignmentCandidate> assignmentCandidates = BuildAssignmentCandidates(candidates);
                bool candidateScoresClose = CandidateScoresClose(assignmentCandidates);

                string status;
                string strategy;
                string risk;
                if (loadedSize)
                {
                    status = "loaded matching size available";
                    strategy = "use loaded family type";
                    risk = "low";
                }
                else if (generatedExists)
                {
                    status = "generated type already exists";
                    strategy = "reuse generated type";
                    risk = "low";
                }
                else if (candidates.Count > 0)
                {
                    status = "candidate family files found";
                    strategy = "load candidate family files, then generate or reuse type";
                    risk = "medium";
                }
                else if (hasEditableTemplate)
                {
                    status = "editable loaded template available";
                    strategy = "generate " + requirement.GeneratedName + " from loaded template";
                    risk = "medium";
                }
                else
                {
                    status = "no reliable loaded type or candidate family file";
                    strategy = "manual family selection required";
                    risk = "high";
                }

                plan.Rows.Add(new FamilyAssignmentRow
                {
                    ComponentGroup = group,
                    ComponentId = requirement.ComponentIds,
                    RequirementName = requirement.GeneratedName,
                    RequiredCount = requirement.Count,
                    ComponentIds = requirement.ComponentIds,
                    SourceFiles = string.Join(" | ", requirement.SourceFiles ?? new List<string>()),
                    Evidence = string.Join(" | ", requirement.Evidence ?? new List<string>()),
                    CurrentStatus = status,
                    SelectedStrategy = candidateScoresClose ? strategy + "; Top 3 candidates are close, manual visual review required" : strategy,
                    CandidateFiles = string.Join(" | ", assignmentCandidates.Select(c => c.FileName + " score=" + c.Score.ToString(CultureInfo.InvariantCulture) + " cache=" + c.SemanticCacheStatus + " reason=" + c.Reason)),
                    VisualReviewCandidates = assignmentCandidates.Count == 0 ? "" : string.Join(" | ", assignmentCandidates.Select(c => "#" + c.VisualReviewRank.ToString(CultureInfo.InvariantCulture) + " " + c.FileName)),
                    Candidates = assignmentCandidates,
                    RiskLevel = risk,
                    Confidence = ConfidenceFromRiskAndCandidates(risk, assignmentCandidates),
                    NeedsReview = string.Equals(risk, "high", StringComparison.OrdinalIgnoreCase) || candidateScoresClose || assignmentCandidates.Any(c => c.NeedsSemanticDescription),
                    UnbuildableReason = BuildOpeningUnbuildableReason(symbols, hasEditableTemplate, assignmentCandidates),
                    Notes = requirement.TypeHints == null || requirement.TypeHints.Count == 0 ? "" : "type hints: " + string.Join("|", requirement.TypeHints.Distinct())
                });
            }
        }

        private static bool CandidateScoresClose(List<FamilyAssignmentCandidate> candidates)
        {
            if (candidates == null || candidates.Count < 2)
            {
                return false;
            }
            List<FamilyAssignmentCandidate> ordered = candidates.OrderByDescending(c => c.Score).Take(3).ToList();
            return ordered.Count > 1 && Math.Abs(ordered[0].Score - ordered[1].Score) <= 15;
        }

        private static double ConfidenceFromRiskAndCandidates(string risk, List<FamilyAssignmentCandidate> candidates)
        {
            double baseConfidence = string.Equals(risk, "low", StringComparison.OrdinalIgnoreCase) ? 0.9 :
                string.Equals(risk, "medium", StringComparison.OrdinalIgnoreCase) ? 0.72 : 0.32;
            if (candidates != null && candidates.Count > 0)
            {
                int topScore = candidates.Max(c => c.Score);
                baseConfidence = Math.Max(baseConfidence, Math.Min(0.95, 0.45 + topScore / 200.0));
                if (CandidateScoresClose(candidates))
                {
                    baseConfidence = Math.Min(baseConfidence, 0.74);
                }
            }
            return Math.Round(baseConfidence, 2);
        }

        private static string BuildOpeningUnbuildableReason(List<FamilySymbol> symbols, bool hasEditableTemplate, List<FamilyAssignmentCandidate> candidates)
        {
            List<string> reasons = new List<string>();
            if (symbols == null || symbols.Count == 0)
            {
                reasons.Add("no loaded family symbol in current Revit project");
            }
            if (!hasEditableTemplate && (candidates == null || candidates.Count == 0))
            {
                reasons.Add("no editable loaded template and no candidate .rfa file");
            }
            return string.Join("; ", reasons);
        }

        private static string JoinComponentIds<T>(List<T> items) where T : ComponentBase
        {
            return string.Join(", ", (items ?? new List<T>())
                .Where(item => item != null && !string.IsNullOrWhiteSpace(item.Id))
                .Select(item => item.Id)
                .Distinct());
        }

        private static string SourceFilesFor<T>(List<T> items) where T : ComponentBase
        {
            return string.Join(" | ", (items ?? new List<T>())
                .Where(item => item != null && !string.IsNullOrWhiteSpace(item.Source))
                .Select(item => item.Source)
                .Distinct());
        }

        private static string EvidenceFor<T>(List<T> items) where T : ComponentBase
        {
            return string.Join(" | ", (items ?? new List<T>())
                .Where(item => item != null)
                .Take(12)
                .Select(item => "id=" + (item.Id ?? "") + "; type=" + (item.Type ?? "") + "; source=" + (item.Source ?? "") + "; confidence=" + item.Confidence.ToString("0.##", CultureInfo.InvariantCulture)));
        }

        private static List<FamilyAssignmentCandidate> BuildAssignmentCandidates(List<FamilyLoadCandidate> candidates)
        {
            List<FamilyAssignmentCandidate> result = new List<FamilyAssignmentCandidate>();
            int rank = 1;
            foreach (FamilyLoadCandidate candidate in candidates ?? new List<FamilyLoadCandidate>())
            {
                result.Add(new FamilyAssignmentCandidate
                {
                    VisualReviewRank = rank,
                    FileName = candidate.FileName,
                    FilePath = candidate.FilePath,
                    RelativePath = candidate.RelativePath,
                    Score = candidate.Score,
                    Reason = candidate.Reason,
                    CategoryMatched = candidate.CategoryMatched,
                    SizeMatched = candidate.SizeMatched,
                    SemanticMatched = candidate.SemanticMatched,
                    PathSemanticMatched = candidate.PathSemanticMatched,
                    CachedSemanticMatched = candidate.CachedSemanticMatched,
                    SemanticCacheStatus = string.IsNullOrWhiteSpace(candidate.SemanticCacheStatus) ? "missing" : candidate.SemanticCacheStatus,
                    SemanticSummary = candidate.SemanticSummary,
                    SemanticTags = candidate.SemanticTags,
                    SemanticScore = candidate.SemanticScore,
                    NeedsPreviewImage = false,
                    NeedsSemanticDescription = !string.Equals(candidate.SemanticCacheStatus, "hit", StringComparison.OrdinalIgnoreCase)
                });
                rank++;
            }
            return result;
        }

        private static bool ShowFamilyAssignmentReview(FamilyAssignmentPlan plan)
        {
            int high = plan.Rows.Count(row => string.Equals(row.RiskLevel, "high", StringComparison.OrdinalIgnoreCase));
            int medium = plan.Rows.Count(row => string.Equals(row.RiskLevel, "medium", StringComparison.OrdinalIgnoreCase));
            int low = plan.Rows.Count(row => string.Equals(row.RiskLevel, "low", StringComparison.OrdinalIgnoreCase));

            StringBuilder sb = new StringBuilder();
            sb.AppendLine("Stage 1: Family assignment and resource matching");
            sb.AppendLine();
            sb.AppendLine("Rows: " + plan.Rows.Count.ToString(CultureInfo.InvariantCulture));
            sb.AppendLine("High risk: " + high.ToString(CultureInfo.InvariantCulture));
            sb.AppendLine("Medium risk: " + medium.ToString(CultureInfo.InvariantCulture));
            sb.AppendLine("Low risk: " + low.ToString(CultureInfo.InvariantCulture));
            sb.AppendLine();
            foreach (FamilyAssignmentRow row in plan.Rows.OrderByDescending(r => RiskWeight(r.RiskLevel)).Take(10))
            {
                sb.AppendLine(row.ComponentGroup + " / " + row.RequirementName + " -> " + row.SelectedStrategy + " [" + row.RiskLevel + "]");
            }
            sb.AppendLine();
            sb.AppendLine("Reports written before modeling: family_assignment_plan.csv, family_assignment_plan.json, family_semantic_description_requests.json, and family_semantic_cache_requests.json");

            TaskDialog dialog = new TaskDialog("AI Revit Family Assignment");
            dialog.MainInstruction = "Review family assignment plan";
            dialog.MainContent = sb.ToString();
            dialog.CommonButtons = TaskDialogCommonButtons.Ok | TaskDialogCommonButtons.Cancel;
            dialog.DefaultButton = high > 0 ? TaskDialogResult.Cancel : TaskDialogResult.Ok;
            return dialog.Show() == TaskDialogResult.Ok;
        }

    }

    public class FamilyAssignmentPlan
    {
        public string FamilyLibraryFolder { get; set; }
        public List<FamilyAssignmentRow> Rows { get; set; }

        public void Write(string folder)
        {
            Directory.CreateDirectory(folder);
            JsonSerializerOptions options = new JsonSerializerOptions { WriteIndented = true };
            File.WriteAllText(Path.Combine(folder, "family_assignment_plan.json"), JsonSerializer.Serialize(this, options), Encoding.UTF8);
            File.WriteAllText(Path.Combine(folder, "family_assignment_plan.csv"), BuildCsv(), Encoding.UTF8);
            FamilyVisualReviewPlan semanticRequests = BuildSemanticDescriptionRequests();
            File.WriteAllText(Path.Combine(folder, "family_semantic_description_requests.json"), JsonSerializer.Serialize(semanticRequests, options), Encoding.UTF8);
            File.WriteAllText(Path.Combine(folder, "family_semantic_cache_requests.json"), JsonSerializer.Serialize(semanticRequests, options), Encoding.UTF8);
            File.WriteAllText(Path.Combine(folder, "cross_drawing_matches.json"), JsonSerializer.Serialize(BuildTraceableMatchReport("cross_drawing_matches", BuildCrossDrawingMatches()), options), Encoding.UTF8);
            File.WriteAllText(Path.Combine(folder, "family_semantics_index.json"), JsonSerializer.Serialize(BuildFamilySemanticsCache(), options), Encoding.UTF8);
            File.WriteAllText(Path.Combine(folder, "visual_review_requests.json"), JsonSerializer.Serialize(BuildTraceableMatchReport("visual_review_requests", BuildVisualReviewMatches()), options), Encoding.UTF8);
            File.WriteAllText(Path.Combine(folder, "unresolved_matches.json"), JsonSerializer.Serialize(BuildTraceableMatchReport("unresolved_matches", BuildUnresolvedMatches()), options), Encoding.UTF8);
            File.WriteAllText(Path.Combine(folder, "match_conflicts.json"), JsonSerializer.Serialize(BuildTraceableMatchReport("match_conflicts", BuildMatchConflicts()), options), Encoding.UTF8);
        }

        private string BuildCsv()
        {
            StringBuilder sb = new StringBuilder();
            sb.AppendLine("component_group,component_id,requirement_name,required_count,component_ids,source_files,evidence,current_status,selected_strategy,candidate_files,visual_review_candidates,risk_level,confidence,needs_review,unbuildable_reason,notes");
            foreach (FamilyAssignmentRow row in Rows ?? new List<FamilyAssignmentRow>())
            {
                sb.AppendLine(string.Join(",",
                    Escape(row.ComponentGroup),
                    Escape(row.ComponentId),
                    Escape(row.RequirementName),
                    Escape(row.RequiredCount.ToString(CultureInfo.InvariantCulture)),
                    Escape(row.ComponentIds),
                    Escape(row.SourceFiles),
                    Escape(row.Evidence),
                    Escape(row.CurrentStatus),
                    Escape(row.SelectedStrategy),
                    Escape(row.CandidateFiles),
                    Escape(row.VisualReviewCandidates),
                    Escape(row.RiskLevel),
                    Escape(row.Confidence.ToString("0.##", CultureInfo.InvariantCulture)),
                    Escape(row.NeedsReview ? "true" : "false"),
                    Escape(row.UnbuildableReason),
                    Escape(row.Notes)));
            }
            return sb.ToString();
        }

        private FamilyVisualReviewPlan BuildSemanticDescriptionRequests()
        {
            FamilyVisualReviewPlan plan = new FamilyVisualReviewPlan
            {
                FamilyLibraryFolder = FamilyLibraryFolder ?? "",
                MaxCandidatesPerRequirement = 3,
                Instructions = "Only listed candidates are missing stored text descriptions. Do not use image recognition in the matching agent. Add or update the relevant component_reference_images/<category>/category_index.json semantic_descriptions entry with family_file, optional image_file, visual_summary, category, family_type, features, and confidence. The visual_summary should be about 30-50 Chinese characters or no more than 50 English words.",
                Requests = new List<FamilyVisualReviewRequest>()
            };

            foreach (FamilyAssignmentRow row in Rows ?? new List<FamilyAssignmentRow>())
            {
                List<FamilyAssignmentCandidate> candidates = (row.Candidates ?? new List<FamilyAssignmentCandidate>())
                    .Where(c => c.NeedsSemanticDescription)
                    .OrderBy(c => c.VisualReviewRank)
                    .Take(3)
                    .ToList();
                if (candidates.Count == 0)
                {
                    continue;
                }

                plan.Requests.Add(new FamilyVisualReviewRequest
                {
                    ComponentGroup = row.ComponentGroup,
                    RequirementName = row.RequirementName,
                    RequiredCount = row.RequiredCount,
                    ComponentIds = row.ComponentIds,
                    TypeHints = ExtractTypeHints(row.Notes),
                    Candidates = candidates
                });
            }

            return plan;
        }

        private TraceableMatchReport BuildTraceableMatchReport(string reportName, List<TraceableMatchResult> matches)
        {
            return new TraceableMatchReport
            {
                SchemaVersion = "1.0",
                ReportName = reportName,
                GeneratedAt = DateTime.Now.ToString("o", CultureInfo.InvariantCulture),
                Scope = "architectural BIM only; structural and MEP modeling decisions are out of scope",
                Matches = matches ?? new List<TraceableMatchResult>()
            };
        }

        private List<TraceableMatchResult> BuildCrossDrawingMatches()
        {
            return (Rows ?? new List<FamilyAssignmentRow>())
                .Select(row => BuildTraceableResult(row, "cross_drawing_family_match", row.Evidence))
                .ToList();
        }

        private List<TraceableMatchResult> BuildVisualReviewMatches()
        {
            return (Rows ?? new List<FamilyAssignmentRow>())
                .Where(row => row.NeedsReview || CandidateScoresClose(row.Candidates))
                .Select(row => BuildTraceableResult(row, "manual_visual_review_required", BuildVisualReviewEvidence(row)))
                .ToList();
        }

        private List<TraceableMatchResult> BuildUnresolvedMatches()
        {
            return (Rows ?? new List<FamilyAssignmentRow>())
                .Where(row => string.Equals(row.RiskLevel, "high", StringComparison.OrdinalIgnoreCase) || !string.IsNullOrWhiteSpace(row.UnbuildableReason))
                .Select(row => BuildTraceableResult(row, "unresolved_or_not_modelable", string.IsNullOrWhiteSpace(row.UnbuildableReason) ? row.Evidence : row.UnbuildableReason))
                .ToList();
        }

        private List<TraceableMatchResult> BuildMatchConflicts()
        {
            List<TraceableMatchResult> conflicts = new List<TraceableMatchResult>();
            foreach (FamilyAssignmentRow row in Rows ?? new List<FamilyAssignmentRow>())
            {
                if (CandidateScoresClose(row.Candidates))
                {
                    conflicts.Add(BuildTraceableResult(row, "candidate_score_tie", "Top candidates have close scores; do not auto-select. " + BuildCandidateEvidence(row.Candidates)));
                }
                if (HasSemanticConflict(row))
                {
                    conflicts.Add(BuildTraceableResult(row, "drawing_text_vs_family_semantics", "Drawing/type hints and stored preview semantics do not fully agree. " + BuildCandidateEvidence(row.Candidates)));
                }
                if ((row.Evidence ?? "").IndexOf("conflict", StringComparison.OrdinalIgnoreCase) >= 0 || (row.Notes ?? "").IndexOf("conflict", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    conflicts.Add(BuildTraceableResult(row, "multi_source_conflict", row.Evidence + " | " + row.Notes));
                }
            }
            return conflicts;
        }

        private TraceableMatchResult BuildTraceableResult(FamilyAssignmentRow row, string matchType, string evidence)
        {
            return new TraceableMatchResult
            {
                ComponentId = row.ComponentId ?? row.ComponentIds ?? "",
                ComponentGroup = row.ComponentGroup ?? "",
                RequirementName = row.RequirementName ?? "",
                MatchType = matchType,
                SourceFiles = SplitList(row.SourceFiles),
                Evidence = string.IsNullOrWhiteSpace(evidence) ? row.Evidence ?? "" : evidence,
                Confidence = row.Confidence,
                SelectedStrategy = row.SelectedStrategy ?? "",
                NeedsReview = row.NeedsReview || string.Equals(row.RiskLevel, "high", StringComparison.OrdinalIgnoreCase),
                RiskLevel = row.RiskLevel ?? "",
                UnbuildableReason = row.UnbuildableReason ?? "",
                Candidates = BuildTraceableCandidates(row.Candidates)
            };
        }

        private static bool CandidateScoresClose(List<FamilyAssignmentCandidate> candidates)
        {
            if (candidates == null || candidates.Count < 2)
            {
                return false;
            }
            List<FamilyAssignmentCandidate> ordered = candidates.OrderByDescending(c => c.Score).Take(3).ToList();
            return ordered.Count > 1 && Math.Abs(ordered[0].Score - ordered[1].Score) <= 15;
        }

        private static bool HasSemanticConflict(FamilyAssignmentRow row)
        {
            if (row == null || string.IsNullOrWhiteSpace(row.Notes) || row.Candidates == null)
            {
                return false;
            }
            return row.Candidates.Any(candidate =>
                candidate.CategoryMatched &&
                !candidate.CachedSemanticMatched &&
                !string.IsNullOrWhiteSpace(candidate.SemanticSummary));
        }

        private static string BuildVisualReviewEvidence(FamilyAssignmentRow row)
        {
            List<string> parts = new List<string>();
            if (!string.IsNullOrWhiteSpace(row.Evidence)) parts.Add(row.Evidence);
            if (CandidateScoresClose(row.Candidates)) parts.Add("candidate scores are close");
            if (row.Candidates != null && row.Candidates.Any(c => c.NeedsSemanticDescription)) parts.Add("one or more candidate families are missing stored text descriptions");
            parts.Add(BuildCandidateEvidence(row.Candidates));
            return string.Join(" | ", parts.Where(part => !string.IsNullOrWhiteSpace(part)));
        }

        private static string BuildCandidateEvidence(List<FamilyAssignmentCandidate> candidates)
        {
            return string.Join(" | ", (candidates ?? new List<FamilyAssignmentCandidate>())
                .OrderBy(c => c.VisualReviewRank)
                .Take(3)
                .Select(candidate => "#" + candidate.VisualReviewRank.ToString(CultureInfo.InvariantCulture) + " " + (candidate.FileName ?? "") + " score=" + candidate.Score.ToString(CultureInfo.InvariantCulture) + " summary=" + (candidate.SemanticSummary ?? "")));
        }

        private static List<TraceableFamilyCandidate> BuildTraceableCandidates(List<FamilyAssignmentCandidate> candidates)
        {
            return (candidates ?? new List<FamilyAssignmentCandidate>())
                .OrderBy(c => c.VisualReviewRank)
                .Take(3)
                .Select(candidate => new TraceableFamilyCandidate
                {
                    Rank = candidate.VisualReviewRank,
                    FileName = candidate.FileName ?? "",
                    FilePath = candidate.FilePath ?? "",
                    RelativePath = candidate.RelativePath ?? "",
                    Score = candidate.Score,
                    Reason = candidate.Reason ?? "",
                    SemanticSummary = candidate.SemanticSummary ?? "",
                    SemanticTags = SplitList(candidate.SemanticTags),
                    NeedsSemanticDescription = candidate.NeedsSemanticDescription
                })
                .ToList();
        }

        private FamilySemanticCacheDocument BuildFamilySemanticsCache()
        {
            FamilySemanticCacheDocument document = new FamilySemanticCacheDocument
            {
                SchemaVersion = "1.0",
                FamilyLibraryFolder = FamilyLibraryFolder ?? "",
                GeneratedAt = DateTime.Now.ToString("o", CultureInfo.InvariantCulture),
                Families = new List<FamilySemanticCacheEntry>()
            };

            if (!string.IsNullOrWhiteSpace(FamilyLibraryFolder) && Directory.Exists(FamilyLibraryFolder))
            {
                JsonSerializerOptions options = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
                foreach (string categoryIndexPath in Directory.GetFiles(FamilyLibraryFolder, "category_index.json", SearchOption.AllDirectories))
                {
                    try
                    {
                        ComponentReferenceIndex categoryIndex = JsonSerializer.Deserialize<ComponentReferenceIndex>(File.ReadAllText(categoryIndexPath, Encoding.UTF8), options);
                        string categoryFolder = Path.GetDirectoryName(categoryIndexPath) ?? FamilyLibraryFolder;
                        foreach (ComponentSemanticDescription description in categoryIndex == null ? new List<ComponentSemanticDescription>() : categoryIndex.SemanticDescriptions ?? new List<ComponentSemanticDescription>())
                        {
                            string familyPath = Path.Combine(categoryFolder, (description.FamilyFile ?? "").Replace('/', Path.DirectorySeparatorChar));
                            document.Families.Add(new FamilySemanticCacheEntry
                            {
                                FilePath = MakeRelativePath(FamilyLibraryFolder, familyPath),
                                FileName = Path.GetFileName(familyPath),
                                FileHash = ComputeSha256(familyPath),
                                Category = string.IsNullOrWhiteSpace(description.Category) ? categoryIndex.ComponentGroup ?? "" : description.Category,
                                FamilyType = description.FamilyType ?? "",
                                AvailableTypes = new List<string> { Path.GetFileNameWithoutExtension(familyPath) },
                                SizeRange = InferSizeRangeText(familyPath),
                                PreviewImage = description.ImageFile ?? "",
                                PreviewImageSemanticDescription = description.VisualSummary ?? "",
                                Features = description.Features ?? new List<string>(),
                                Confidence = description.Confidence,
                                ExtractedAt = DateTime.Now.ToString("o", CultureInfo.InvariantCulture)
                            });
                        }
                    }
                    catch
                    {
                        continue;
                    }
                }
            }

            if (document.Families.Count == 0)
            {
                document.Families = (Rows ?? new List<FamilyAssignmentRow>())
                    .SelectMany(row => row.Candidates ?? new List<FamilyAssignmentCandidate>())
                    .Where(candidate => !string.IsNullOrWhiteSpace(candidate.FilePath))
                    .GroupBy(candidate => candidate.FilePath, StringComparer.OrdinalIgnoreCase)
                    .Select(group => group.OrderByDescending(candidate => candidate.Score).First())
                    .Select(candidate => new FamilySemanticCacheEntry
                    {
                        FilePath = candidate.RelativePath ?? candidate.FilePath ?? "",
                        FileName = candidate.FileName ?? "",
                        FileHash = ComputeSha256(candidate.FilePath),
                        Category = "",
                        FamilyType = "",
                        AvailableTypes = new List<string> { Path.GetFileNameWithoutExtension(candidate.FileName ?? "") },
                        SizeRange = InferSizeRangeText(candidate.FileName ?? ""),
                        PreviewImage = "",
                        PreviewImageSemanticDescription = candidate.SemanticSummary ?? "",
                        Features = SplitList(candidate.SemanticTags),
                        Confidence = candidate.SemanticCacheStatus == "hit" ? 0.8 : 0.4,
                        ExtractedAt = DateTime.Now.ToString("o", CultureInfo.InvariantCulture)
                    })
                    .ToList();
            }

            document.Families = document.Families
                .Where(entry => entry != null && !string.IsNullOrWhiteSpace(entry.FilePath))
                .GroupBy(entry => NormalizePathText(entry.FilePath).ToLowerInvariant())
                .Select(group => group.OrderByDescending(entry => entry.Confidence).First())
                .OrderBy(entry => entry.Category)
                .ThenBy(entry => entry.FileName)
                .ToList();
            return document;
        }

        private static string MakeRelativePath(string rootFolder, string filePath)
        {
            try
            {
                string root = Path.GetFullPath(rootFolder).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
                string full = Path.GetFullPath(filePath);
                return full.StartsWith(root, StringComparison.OrdinalIgnoreCase) ? full.Substring(root.Length) : filePath;
            }
            catch
            {
                return filePath ?? "";
            }
        }

        private static string ComputeSha256(string filePath)
        {
            try
            {
                if (string.IsNullOrWhiteSpace(filePath) || !File.Exists(filePath))
                {
                    return "";
                }
                using (SHA256 sha = SHA256.Create())
                using (FileStream stream = File.OpenRead(filePath))
                {
                    return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                }
            }
            catch
            {
                return "";
            }
        }

        private static string InferSizeRangeText(string text)
        {
            text = text ?? "";
            List<string> numbers = new List<string>();
            foreach (System.Text.RegularExpressions.Match match in System.Text.RegularExpressions.Regex.Matches(text, @"\d{2,5}"))
            {
                numbers.Add(match.Value);
            }
            return numbers.Count >= 2 ? numbers[0] + "x" + numbers[1] : "";
        }

        private static string NormalizePathText(string value)
        {
            return (value ?? "").Replace('\\', '/').Trim();
        }

        private static List<string> SplitList(string value)
        {
            return (value ?? "")
                .Split(new[] { '|', ',' }, StringSplitOptions.RemoveEmptyEntries)
                .Select(item => item.Trim())
                .Where(item => !string.IsNullOrWhiteSpace(item))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToList();
        }

        private static string ExtractTypeHints(string notes)
        {
            const string prefix = "type hints: ";
            if (string.IsNullOrWhiteSpace(notes) || !notes.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            {
                return "";
            }
            return notes.Substring(prefix.Length);
        }

        private static string Escape(string value)
        {
            value = value ?? "";
            return "\"" + value.Replace("\"", "\"\"") + "\"";
        }
    }

    public class FamilyAssignmentRow
    {
        public string ComponentGroup { get; set; }
        [JsonPropertyName("component_id")]
        public string ComponentId { get; set; }
        public string RequirementName { get; set; }
        public int RequiredCount { get; set; }
        public string ComponentIds { get; set; }
        [JsonPropertyName("source_files")]
        public string SourceFiles { get; set; }
        [JsonPropertyName("evidence")]
        public string Evidence { get; set; }
        public string CurrentStatus { get; set; }
        [JsonPropertyName("selected_strategy")]
        public string SelectedStrategy { get; set; }
        public string CandidateFiles { get; set; }
        public string VisualReviewCandidates { get; set; }
        public List<FamilyAssignmentCandidate> Candidates { get; set; }
        public string RiskLevel { get; set; }
        [JsonPropertyName("confidence")]
        public double Confidence { get; set; }
        [JsonPropertyName("needs_review")]
        public bool NeedsReview { get; set; }
        [JsonPropertyName("unbuildable_reason")]
        public string UnbuildableReason { get; set; }
        public string Notes { get; set; }
    }

    public class FamilyAssignmentCandidate
    {
        public int VisualReviewRank { get; set; }
        public string FileName { get; set; }
        public string FilePath { get; set; }
        public string RelativePath { get; set; }
        public int Score { get; set; }
        public string Reason { get; set; }
        public bool CategoryMatched { get; set; }
        public bool SizeMatched { get; set; }
        public bool SemanticMatched { get; set; }
        public bool PathSemanticMatched { get; set; }
        public bool CachedSemanticMatched { get; set; }
        public string SemanticCacheStatus { get; set; }
        public string SemanticSummary { get; set; }
        public string SemanticTags { get; set; }
        public int SemanticScore { get; set; }
        public bool NeedsPreviewImage { get; set; }
        public bool NeedsSemanticDescription { get; set; }
    }

    public class FamilyVisualReviewPlan
    {
        public string FamilyLibraryFolder { get; set; }
        public int MaxCandidatesPerRequirement { get; set; }
        public string Instructions { get; set; }
        public List<FamilyVisualReviewRequest> Requests { get; set; }
    }

    public class FamilyVisualReviewRequest
    {
        public string ComponentGroup { get; set; }
        public string RequirementName { get; set; }
        public int RequiredCount { get; set; }
        public string ComponentIds { get; set; }
        public string TypeHints { get; set; }
        public List<FamilyAssignmentCandidate> Candidates { get; set; }
    }

    public class TraceableMatchReport
    {
        [JsonPropertyName("schema_version")]
        public string SchemaVersion { get; set; }
        [JsonPropertyName("report_name")]
        public string ReportName { get; set; }
        [JsonPropertyName("generated_at")]
        public string GeneratedAt { get; set; }
        [JsonPropertyName("scope")]
        public string Scope { get; set; }
        [JsonPropertyName("matches")]
        public List<TraceableMatchResult> Matches { get; set; }
    }

    public class TraceableMatchResult
    {
        [JsonPropertyName("component_id")]
        public string ComponentId { get; set; }
        [JsonPropertyName("component_group")]
        public string ComponentGroup { get; set; }
        [JsonPropertyName("requirement_name")]
        public string RequirementName { get; set; }
        [JsonPropertyName("match_type")]
        public string MatchType { get; set; }
        [JsonPropertyName("source_files")]
        public List<string> SourceFiles { get; set; }
        [JsonPropertyName("evidence")]
        public string Evidence { get; set; }
        [JsonPropertyName("confidence")]
        public double Confidence { get; set; }
        [JsonPropertyName("selected_strategy")]
        public string SelectedStrategy { get; set; }
        [JsonPropertyName("needs_review")]
        public bool NeedsReview { get; set; }
        [JsonPropertyName("risk_level")]
        public string RiskLevel { get; set; }
        [JsonPropertyName("unbuildable_reason")]
        public string UnbuildableReason { get; set; }
        [JsonPropertyName("candidates")]
        public List<TraceableFamilyCandidate> Candidates { get; set; }
    }

    public class TraceableFamilyCandidate
    {
        [JsonPropertyName("rank")]
        public int Rank { get; set; }
        [JsonPropertyName("file_name")]
        public string FileName { get; set; }
        [JsonPropertyName("file_path")]
        public string FilePath { get; set; }
        [JsonPropertyName("relative_path")]
        public string RelativePath { get; set; }
        [JsonPropertyName("score")]
        public int Score { get; set; }
        [JsonPropertyName("reason")]
        public string Reason { get; set; }
        [JsonPropertyName("semantic_summary")]
        public string SemanticSummary { get; set; }
        [JsonPropertyName("semantic_tags")]
        public List<string> SemanticTags { get; set; }
        [JsonPropertyName("needs_semantic_description")]
        public bool NeedsSemanticDescription { get; set; }
    }

    public class FamilySemanticCacheDocument
    {
        [JsonPropertyName("schema_version")]
        public string SchemaVersion { get; set; }
        [JsonPropertyName("family_library_folder")]
        public string FamilyLibraryFolder { get; set; }
        [JsonPropertyName("generated_at")]
        public string GeneratedAt { get; set; }
        [JsonPropertyName("families")]
        public List<FamilySemanticCacheEntry> Families { get; set; }
    }

    public class FamilySemanticCacheEntry
    {
        [JsonPropertyName("file_path")]
        public string FilePath { get; set; }
        [JsonPropertyName("file_name")]
        public string FileName { get; set; }
        [JsonPropertyName("file_hash")]
        public string FileHash { get; set; }
        [JsonPropertyName("category")]
        public string Category { get; set; }
        [JsonPropertyName("family_type")]
        public string FamilyType { get; set; }
        [JsonPropertyName("available_types")]
        public List<string> AvailableTypes { get; set; }
        [JsonPropertyName("size_range")]
        public string SizeRange { get; set; }
        [JsonPropertyName("preview_image")]
        public string PreviewImage { get; set; }
        [JsonPropertyName("preview_image_semantic_description")]
        public string PreviewImageSemanticDescription { get; set; }
        [JsonPropertyName("features")]
        public List<string> Features { get; set; }
        [JsonPropertyName("confidence")]
        public double Confidence { get; set; }
        [JsonPropertyName("extracted_at")]
        public string ExtractedAt { get; set; }
    }

}


