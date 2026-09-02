using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Architecture;
using Autodesk.Revit.UI;
using Microsoft.Win32;

namespace AiRevitModeling
{
    [Transaction(TransactionMode.Manual)]
    public partial class AiRevitModelingCommand : IExternalCommand
    {
        private const string SupportedSchemaVersion = "1.0";
        // The English library mirrors the original Chinese library.  The original
        // remains available; this is the default for the English Revit workflow.
        private static readonly string DefaultFamilyLibraryFolder = Environment.GetEnvironmentVariable("AI_REVIT_FAMILY_LIBRARY") ?? "";

        private static void AppendColumnPreviewGroup(StringBuilder sb, Document doc, List<ColumnComponent> items)
        {
            int count = Count(items);
            if (count == 0)
            {
                return;
            }
            int symbolCount = new FilteredElementCollector(doc)
                .OfCategory(BuiltInCategory.OST_StructuralColumns)
                .OfClass(typeof(FamilySymbol))
                .GetElementCount();
            sb.AppendLine("Columns:");
            if (symbolCount == 0)
            {
                sb.AppendLine("  MISSING loaded column family: " + count.ToString(CultureInfo.InvariantCulture) + " column(s) need at least one column type.");
            }
            else
            {
                sb.AppendLine("  ready: " + count.ToString(CultureInfo.InvariantCulture) + " column(s); loaded column type(s): " + symbolCount.ToString(CultureInfo.InvariantCulture));
            }
        }

        private static void AppendSlabPreviewGroup(StringBuilder sb, Document doc, List<SlabComponent> slabs, List<FloorOpeningComponent> openings)
        {
            int slabCount = Count(slabs);
            int openingCount = Count(openings);
            if (slabCount == 0 && openingCount == 0)
            {
                return;
            }
            int floorTypeCount = new FilteredElementCollector(doc)
                .OfClass(typeof(FloorType))
                .GetElementCount();
            sb.AppendLine("Floors and floor openings:");
            if (floorTypeCount == 0)
            {
                sb.AppendLine("  MISSING floor type: floor slabs cannot be created until a floor type exists in the project.");
            }
            else
            {
                sb.AppendLine("  ready: " + slabCount.ToString(CultureInfo.InvariantCulture) + " slab(s), " + openingCount.ToString(CultureInfo.InvariantCulture) + " floor opening(s); loaded floor type(s): " + floorTypeCount.ToString(CultureInfo.InvariantCulture));
            }
        }

        private static void AppendFamilyPreviewGroup(StringBuilder sb, Document doc, string label, BuiltInCategory category, List<OpeningComponent> items, List<FamilyLoadCandidate> familyCandidates)
        {
            List<OpeningFamilyRequirement> requirements = BuildOpeningFamilyRequirements(category, items);
            List<FamilySymbol> symbols = new FilteredElementCollector(doc)
                .OfCategory(category)
                .OfClass(typeof(FamilySymbol))
                .Cast<FamilySymbol>()
                .ToList();

            sb.AppendLine(label + ":");
            if (requirements.Count == 0)
            {
                sb.AppendLine("  no modelable items");
                return;
            }

            if (symbols.Count == 0)
            {
                sb.AppendLine("  MISSING loaded family: " + requirements.Count.ToString(CultureInfo.InvariantCulture) + " required type(s) cannot be created until at least one " + label.ToLowerInvariant() + " family is loaded.");
                AppendFamilyCandidates(sb, category, familyCandidates);
                return;
            }

            bool hasEditableTemplate = symbols.Any(s => CanSetTypeSize(s));
            foreach (OpeningFamilyRequirement requirement in requirements)
            {
                string status;
                if (symbols.Any(s => SymbolMatchesSize(s, requirement.WidthMm, requirement.HeightMm)))
                {
                    status = "loaded size available";
                }
                else if (symbols.Any(s => string.Equals(s.Name, requirement.GeneratedName, StringComparison.OrdinalIgnoreCase)))
                {
                    status = "generated type already exists";
                }
                else if (hasEditableTemplate)
                {
                    status = "will generate " + requirement.GeneratedName + " from loaded template";
                }
                else
                {
                    status = "MISSING editable template; size may fall back to the first loaded family type";
                }

                sb.AppendLine("  " + requirement.GeneratedName + " - " + status + " (" + requirement.Count.ToString(CultureInfo.InvariantCulture) + " item(s): " + requirement.ComponentIds + ")");
                AppendFamilyCandidates(sb, category, requirement.GeneratedName, familyCandidates);
            }
        }

        private static void AppendFamilyCandidates(StringBuilder sb, BuiltInCategory category, List<FamilyLoadCandidate> familyCandidates)
        {
            foreach (FamilyLoadCandidate candidate in familyCandidates.Where(c => c.Category == category).Take(6))
            {
                sb.AppendLine("    candidate family file: " + candidate.FileName);
            }
        }

        private static void AppendFamilyCandidates(StringBuilder sb, BuiltInCategory category, string requirementName, List<FamilyLoadCandidate> familyCandidates)
        {
            foreach (FamilyLoadCandidate candidate in familyCandidates.Where(c => c.Category == category && c.RequirementName == requirementName).Take(3))
            {
                sb.AppendLine("    candidate family file: " + candidate.FileName);
            }
        }

        private static List<OpeningFamilyRequirement> BuildOpeningFamilyRequirements(BuiltInCategory category, List<OpeningComponent> items)
        {
            Dictionary<string, OpeningFamilyRequirement> requirements = new Dictionary<string, OpeningFamilyRequirement>();
            foreach (OpeningComponent item in items ?? new List<OpeningComponent>())
            {
                if (IsRejected(item) || !item.WidthMm.HasValue || item.WidthMm.Value <= 0)
                {
                    continue;
                }

                double? heightMm = item.HeightMm;
                if ((!heightMm.HasValue || heightMm.Value <= 0) && category == BuiltInCategory.OST_Doors && NeedsReview(item))
                {
                    heightMm = 2100;
                }
                if (!heightMm.HasValue || heightMm.Value <= 0)
                {
                    continue;
                }

                string prefix = category == BuiltInCategory.OST_Doors ? "AI Door " : "AI Window ";
                string generatedName = prefix +
                    Math.Round(item.WidthMm.Value).ToString(CultureInfo.InvariantCulture) + "x" +
                    Math.Round(heightMm.Value).ToString(CultureInfo.InvariantCulture);
                string key = generatedName.ToLowerInvariant();
                if (!requirements.TryGetValue(key, out OpeningFamilyRequirement requirement))
                {
                    requirement = new OpeningFamilyRequirement
                    {
                        GeneratedName = generatedName,
                        WidthMm = item.WidthMm.Value,
                        HeightMm = heightMm.Value,
                        ComponentIds = "",
                        TypeHints = new List<string>(),
                        SourceFiles = new List<string>(),
                        Evidence = new List<string>()
                    };
                    requirements[key] = requirement;
                }

                requirement.Count++;
                requirement.ComponentIds = string.IsNullOrWhiteSpace(requirement.ComponentIds) ? item.Id : requirement.ComponentIds + ", " + item.Id;
                AddDistinct(requirement.SourceFiles, item.Source);
                AddDistinct(requirement.Evidence, BuildOpeningEvidence(item, item.WidthMm.Value, heightMm.Value));
                if (!string.IsNullOrWhiteSpace(item.Type))
                {
                    requirement.TypeHints.Add(item.Type);
                }
            }

            return requirements.Values.OrderBy(r => r.GeneratedName).ToList();
        }

        private static void AddDistinct(List<string> values, string value)
        {
            if (values == null || string.IsNullOrWhiteSpace(value))
            {
                return;
            }
            if (!values.Any(existing => string.Equals(existing, value, StringComparison.OrdinalIgnoreCase)))
            {
                values.Add(value);
            }
        }

        private static string BuildOpeningEvidence(OpeningComponent item, double widthMm, double heightMm)
        {
            List<string> parts = new List<string>
            {
                "id=" + (item == null ? "" : item.Id ?? ""),
                "size=" + Math.Round(widthMm).ToString(CultureInfo.InvariantCulture) + "x" + Math.Round(heightMm).ToString(CultureInfo.InvariantCulture)
            };
            if (!string.IsNullOrWhiteSpace(item == null ? "" : item.Type)) parts.Add("type=" + item.Type);
            if (!string.IsNullOrWhiteSpace(item == null ? "" : item.Level)) parts.Add("level=" + item.Level);
            if (!string.IsNullOrWhiteSpace(item == null ? "" : item.HostWallId)) parts.Add("host_wall_id=" + item.HostWallId);
            if (!string.IsNullOrWhiteSpace(item == null ? "" : item.Material)) parts.Add("material=" + item.Material);
            if (!string.IsNullOrWhiteSpace(item == null ? "" : item.Source)) parts.Add("source=" + item.Source);
            if (!string.IsNullOrWhiteSpace(item == null ? "" : item.Notes)) parts.Add("notes=" + item.Notes);
            return string.Join("; ", parts);
        }

        private static void ClearExistingLevels(Document doc)
        {
            List<ElementId> levelIds = new FilteredElementCollector(doc)
                .OfClass(typeof(Level))
                .Cast<Level>()
                .Select(level => level.Id)
                .ToList();

            foreach (ElementId levelId in levelIds)
            {
                try
                {
                    if (doc.GetElement(levelId) != null)
                    {
                        doc.Delete(levelId);
                    }
                }
                catch
                {
                    // Some template levels can be protected by dependent views/elements.
                    // Continue so the JSON levels can still be created where possible.
                }
            }
        }

        private static void PrepareExistingLevelsForJson(Document doc, StandardModel model)
        {
            List<LevelComponent> desiredLevels = (model.Components.Levels ?? new List<LevelComponent>())
                .Where(level => !IsRejected(level) && !string.IsNullOrWhiteSpace(level.Name))
                .OrderBy(level => level.ElevationMm)
                .ToList();
            if (desiredLevels.Count == 0)
            {
                return;
            }

            List<Level> existingLevels = new FilteredElementCollector(doc)
                .OfClass(typeof(Level))
                .Cast<Level>()
                .OrderBy(level => level.ProjectElevation)
                .ToList();
            if (existingLevels.Count == 0)
            {
                return;
            }

            HashSet<long> usedLevelIds = new HashSet<long>();
            HashSet<string> assignedNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            foreach (LevelComponent desired in desiredLevels)
            {
                Level sameName = existingLevels.FirstOrDefault(level =>
                    !usedLevelIds.Contains(ElementIdValue(level.Id)) &&
                    string.Equals(level.Name, desired.Name, StringComparison.OrdinalIgnoreCase));
                if (sameName != null)
                {
                    SetLevelElevation(sameName, desired.ElevationMm);
                    usedLevelIds.Add(ElementIdValue(sameName.Id));
                    assignedNames.Add(desired.Name);
                }
            }

            foreach (LevelComponent desired in desiredLevels.Where(level => !assignedNames.Contains(level.Name)))
            {
                Level reusable = existingLevels.FirstOrDefault(level =>
                    !usedLevelIds.Contains(ElementIdValue(level.Id)) &&
                    doc.GetElement(level.Id) != null);
                if (reusable == null)
                {
                    break;
                }

                SetLevelElevation(reusable, desired.ElevationMm);
                TryRenameLevel(reusable, desired.Name);
                usedLevelIds.Add(ElementIdValue(reusable.Id));
                assignedNames.Add(desired.Name);
            }

            double highestDesiredElevationMm = desiredLevels.Max(level => level.ElevationMm);
            int parkedIndex = 1;
            foreach (Level level in existingLevels.Where(level =>
                !usedLevelIds.Contains(ElementIdValue(level.Id)) &&
                doc.GetElement(level.Id) != null).ToList())
            {
                if (TryDeleteElement(doc, level.Id))
                {
                    continue;
                }
                TryRenameLevel(level, "__AI_UNUSED_LEVEL_" + ElementIdValue(level.Id).ToString(CultureInfo.InvariantCulture));
                SetLevelElevation(level, highestDesiredElevationMm + 3300 * parkedIndex);
                parkedIndex++;
            }
        }

        private static void SetLevelElevation(Level level, double elevationMm)
        {
            Parameter parameter = level.get_Parameter(BuiltInParameter.LEVEL_ELEV);
            if (parameter != null && !parameter.IsReadOnly)
            {
                double desiredProjectElevation = MmToFeet(elevationMm);
                double correction = desiredProjectElevation - level.ProjectElevation;
                parameter.Set(parameter.AsDouble() + correction);
            }
        }

        private static bool TryRenameLevel(Level level, string name)
        {
            try
            {
                if (!string.IsNullOrWhiteSpace(name) && !string.Equals(level.Name, name, StringComparison.OrdinalIgnoreCase))
                {
                    level.Name = name;
                }
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static List<FamilyLoadCandidate> FindFamilyLoadCandidates(Document doc, StandardModel model, string familyLibraryFolder)
        {
            List<FamilyLoadCandidate> candidates = new List<FamilyLoadCandidate>();
            if (string.IsNullOrWhiteSpace(familyLibraryFolder) || !Directory.Exists(familyLibraryFolder))
            {
                return candidates;
            }

            List<string> familyFiles = Directory.GetFiles(familyLibraryFolder, "*.rfa", SearchOption.AllDirectories).ToList();
            FamilySemanticIndex semanticIndex = LoadFamilySemanticIndex(familyLibraryFolder);
            AddColumnFamilyLoadCandidates(candidates, doc, model.Components.Columns, familyFiles, familyLibraryFolder, semanticIndex);
            AddFamilyLoadCandidatesForGroup(candidates, doc, BuiltInCategory.OST_Doors, model.Components.Doors, familyFiles, familyLibraryFolder, semanticIndex);
            AddFamilyLoadCandidatesForGroup(candidates, doc, BuiltInCategory.OST_Windows, model.Components.Windows, familyFiles, familyLibraryFolder, semanticIndex);
            return candidates
                .GroupBy(c => ((c.RequirementName ?? "") + "|" + (c.FilePath ?? "")).ToLowerInvariant())
                .Select(g => g.OrderByDescending(c => c.Score).First())
                .OrderByDescending(c => c.Score)
                .ThenBy(c => c.FileName)
                .ToList();
        }

        private static void AddColumnFamilyLoadCandidates(List<FamilyLoadCandidate> candidates, Document doc, List<ColumnComponent> items, List<string> familyFiles, string familyLibraryFolder, FamilySemanticIndex semanticIndex)
        {
            if (Count(items) == 0)
            {
                return;
            }

            foreach (ColumnComponent column in items ?? new List<ColumnComponent>())
            {
                foreach (string file in familyFiles)
                {
                    FamilyFileEvaluation evaluation = EvaluateColumnFamilyFile(file, familyLibraryFolder, column, semanticIndex);
                    if (evaluation.Score > 0)
                    {
                        candidates.Add(new FamilyLoadCandidate
                        {
                            Category = BuiltInCategory.OST_StructuralColumns,
                            RequirementName = BuildColumnRequirementName(column),
                            FilePath = file,
                            FileName = Path.GetFileName(file),
                            RelativePath = evaluation.RelativePath,
                            Score = evaluation.Score,
                            Reason = evaluation.Reason,
                            CategoryMatched = evaluation.CategoryMatched,
                            SizeMatched = evaluation.SizeMatched,
                            SemanticMatched = evaluation.SemanticMatched,
                            PathSemanticMatched = evaluation.PathSemanticMatched,
                            CachedSemanticMatched = evaluation.CachedSemanticMatched,
                            SemanticCacheStatus = evaluation.SemanticCacheStatus,
                            SemanticSummary = evaluation.SemanticSummary,
                            SemanticTags = evaluation.SemanticTags,
                            SemanticScore = evaluation.SemanticScore
                        });
                    }
                }
            }
        }

        private static void AddFamilyLoadCandidatesForGroup(List<FamilyLoadCandidate> candidates, Document doc, BuiltInCategory category, List<OpeningComponent> items, List<string> familyFiles, string familyLibraryFolder, FamilySemanticIndex semanticIndex)
        {
            List<OpeningFamilyRequirement> requirements = BuildOpeningFamilyRequirements(category, items);
            List<FamilySymbol> symbols = new FilteredElementCollector(doc)
                .OfCategory(category)
                .OfClass(typeof(FamilySymbol))
                .Cast<FamilySymbol>()
                .ToList();

            foreach (OpeningFamilyRequirement requirement in requirements)
            {
                foreach (string file in familyFiles)
                {
                    FamilyFileEvaluation evaluation = EvaluateFamilyFile(file, familyLibraryFolder, category, requirement, semanticIndex);
                    if (evaluation.Score > 0)
                    {
                        candidates.Add(new FamilyLoadCandidate
                        {
                            Category = category,
                            RequirementName = requirement.GeneratedName,
                            FilePath = file,
                            FileName = Path.GetFileName(file),
                            RelativePath = evaluation.RelativePath,
                            Score = evaluation.Score,
                            Reason = evaluation.Reason,
                            CategoryMatched = evaluation.CategoryMatched,
                            SizeMatched = evaluation.SizeMatched,
                            SemanticMatched = evaluation.SemanticMatched,
                            PathSemanticMatched = evaluation.PathSemanticMatched,
                            CachedSemanticMatched = evaluation.CachedSemanticMatched,
                            SemanticCacheStatus = evaluation.SemanticCacheStatus,
                            SemanticSummary = evaluation.SemanticSummary,
                            SemanticTags = evaluation.SemanticTags,
                            SemanticScore = evaluation.SemanticScore
                        });
                    }
                }
            }
        }

        private static FamilyFileEvaluation EvaluateFamilyFile(string filePath, string familyLibraryFolder, BuiltInCategory category, OpeningFamilyRequirement requirement, FamilySemanticIndex semanticIndex)
        {
            string name = Path.GetFileNameWithoutExtension(filePath).ToLowerInvariant();
            string relativePath = MakeRelativePath(familyLibraryFolder, filePath);
            string relativeText = relativePath.ToLowerInvariant().Replace('\\', ' ').Replace('/', ' ');
            string combinedText = name + " " + relativeText;
            FamilySemanticEntry semanticEntry = FindFamilySemanticEntry(semanticIndex, filePath, relativePath);
            string semanticText = BuildSemanticText(semanticEntry);
            string width = Math.Round(requirement.WidthMm).ToString(CultureInfo.InvariantCulture);
            string height = Math.Round(requirement.HeightMm).ToString(CultureInfo.InvariantCulture);
            bool categoryMatch = category == BuiltInCategory.OST_Doors
                ? ContainsAny(combinedText, "door", "doors")
                : ContainsAny(combinedText, "window", "windows");
            bool sizeMatch = ContainsSizeText(combinedText, width, height);
            bool nameSemanticMatch = category == BuiltInCategory.OST_Doors && DoorSemanticMatches(name, requirement);
            bool pathSemanticMatch = category == BuiltInCategory.OST_Doors && DoorSemanticMatches(relativeText, requirement);
            bool cachedSemanticMatch = SemanticDescriptionMatches(category, requirement, semanticText);
            bool doorSemanticMatch = nameSemanticMatch || pathSemanticMatch;
            int requestedLeafCount = category == BuiltInCategory.OST_Doors ? RequestedDoorLeafCount(requirement, width) : 0;
            int familyLeafCount = category == BuiltInCategory.OST_Doors ? DoorLeafCount(combinedText + " " + semanticText) : 0;
            bool leafMatched = requestedLeafCount > 0 && familyLeafCount > 0 && requestedLeafCount == familyLeafCount;
            bool leafConflicted = requestedLeafCount > 0 && familyLeafCount > 0 && requestedLeafCount != familyLeafCount;

            int score = 0;
            if (!leafConflicted)
            {
                if (categoryMatch && sizeMatch && doorSemanticMatch) score = 130;
                else if (categoryMatch && sizeMatch) score = 100;
                else if (sizeMatch && doorSemanticMatch) score = 90;
                else if (sizeMatch) score = 70;
                else if (categoryMatch && doorSemanticMatch) score = 65;
                else if (doorSemanticMatch) score = 45;
                else if (categoryMatch) score = 40;
                if (leafMatched)
                {
                    score += 45;
                }
            }

            int semanticScore = 0;
            if (semanticEntry != null && !leafConflicted)
            {
                semanticScore += 10;
                if (CachedCategoryMatches(category, semanticEntry))
                {
                    semanticScore += 15;
                }
                if (cachedSemanticMatch)
                {
                    semanticScore += 25;
                }
                score += semanticScore;
            }

            List<string> reasons = new List<string>();
            if (categoryMatch)
            {
                reasons.Add("category matched by file name or folder path");
            }
            if (sizeMatch)
            {
                reasons.Add("size " + width + "x" + height + " found in file name or folder path");
            }
            if (nameSemanticMatch)
            {
                reasons.Add("door semantic matched by file name");
            }
            if (pathSemanticMatch)
            {
                reasons.Add("door semantic matched by folder path");
            }
            if (semanticEntry != null)
            {
                reasons.Add("semantic cache found");
            }
            if (cachedSemanticMatch)
            {
                reasons.Add("cached visual description matched requirement");
            }
            if (leafMatched)
            {
                reasons.Add("door leaf count matched requirement");
            }
            if (leafConflicted)
            {
                reasons.Add("door leaf count conflicts with requirement");
            }
            if (score > 0 && reasons.Count == 0)
            {
                reasons.Add("fallback candidate");
            }

            return new FamilyFileEvaluation
            {
                RelativePath = relativePath,
                Score = score,
                Reason = string.Join("; ", reasons),
                CategoryMatched = categoryMatch,
                SizeMatched = sizeMatch,
                SemanticMatched = nameSemanticMatch,
                PathSemanticMatched = pathSemanticMatch,
                CachedSemanticMatched = cachedSemanticMatch,
                SemanticCacheStatus = semanticEntry == null ? "missing" : "hit",
                SemanticSummary = semanticEntry == null ? "" : (semanticEntry.VisualSummary ?? ""),
                SemanticTags = semanticEntry == null ? "" : string.Join("|", semanticEntry.Features ?? new List<string>()),
                SemanticScore = semanticScore
            };
        }

        private static FamilyFileEvaluation EvaluateColumnFamilyFile(string filePath, string familyLibraryFolder, ColumnComponent column, FamilySemanticIndex semanticIndex)
        {
            string name = Path.GetFileNameWithoutExtension(filePath).ToLowerInvariant();
            string relativePath = MakeRelativePath(familyLibraryFolder, filePath);
            string relativeText = relativePath.ToLowerInvariant().Replace('\\', ' ').Replace('/', ' ');
            string combinedText = name + " " + relativeText;
            FamilySemanticEntry semanticEntry = FindFamilySemanticEntry(semanticIndex, filePath, relativePath);
            string semanticText = BuildSemanticText(semanticEntry);
            bool categoryMatch = ComponentCategoryMatches(BuiltInCategory.OST_StructuralColumns, combinedText);
            bool cachedCategoryMatch = CachedCategoryMatches(BuiltInCategory.OST_StructuralColumns, semanticEntry);
            bool shapeMatch = ColumnShapeMatches(column, combinedText + " " + semanticText);
            bool sizeMatch = ColumnSizeMatches(column, combinedText);
            bool cachedSemanticMatch = shapeMatch && !string.IsNullOrWhiteSpace(semanticText);

            int score = 0;
            if (categoryMatch) score += 40;
            if (cachedCategoryMatch) score += 25;
            if (shapeMatch) score += 35;
            if (sizeMatch) score += 25;

            int semanticScore = 0;
            if (semanticEntry != null)
            {
                semanticScore += 10;
                if (cachedCategoryMatch) semanticScore += 15;
                if (cachedSemanticMatch) semanticScore += 20;
                score += semanticScore;
            }

            List<string> reasons = new List<string>();
            if (categoryMatch) reasons.Add("column category matched by file name or folder path");
            if (cachedCategoryMatch) reasons.Add("column category matched by stored description");
            if (shapeMatch) reasons.Add("column shape matched by stored description or path");
            if (sizeMatch) reasons.Add("column size found in file name or folder path");
            if (semanticEntry != null) reasons.Add("semantic description found");

            return new FamilyFileEvaluation
            {
                RelativePath = relativePath,
                Score = score,
                Reason = string.Join("; ", reasons),
                CategoryMatched = categoryMatch || cachedCategoryMatch,
                SizeMatched = sizeMatch,
                SemanticMatched = shapeMatch,
                PathSemanticMatched = shapeMatch && string.IsNullOrWhiteSpace(semanticText),
                CachedSemanticMatched = cachedSemanticMatch,
                SemanticCacheStatus = semanticEntry == null ? "missing" : "hit",
                SemanticSummary = semanticEntry == null ? "" : (semanticEntry.VisualSummary ?? ""),
                SemanticTags = semanticEntry == null ? "" : string.Join("|", semanticEntry.Features ?? new List<string>()),
                SemanticScore = semanticScore
            };
        }

        private static FamilySemanticIndex LoadFamilySemanticIndex(string familyLibraryFolder)
        {
            if (string.IsNullOrWhiteSpace(familyLibraryFolder))
            {
                return new FamilySemanticIndex { Families = new List<FamilySemanticEntry>() };
            }

            FamilySemanticIndex index = new FamilySemanticIndex { Families = new List<FamilySemanticEntry>() };
            JsonSerializerOptions options = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
            string indexPath = Path.Combine(familyLibraryFolder, "family_semantics_index.json");
            if (File.Exists(indexPath))
            {
                try
                {
                    FamilySemanticIndex legacyIndex = JsonSerializer.Deserialize<FamilySemanticIndex>(File.ReadAllText(indexPath, Encoding.UTF8), options);
                    if (legacyIndex != null && legacyIndex.Families != null)
                    {
                        index.Families.AddRange(legacyIndex.Families);
                    }
                }
                catch
                {
                    // Ignore malformed optional semantic cache files; category_index.json files may still be usable.
                }
            }

            foreach (string categoryIndexPath in Directory.GetFiles(familyLibraryFolder, "category_index.json", SearchOption.AllDirectories))
            {
                try
                {
                    ComponentReferenceIndex categoryIndex = JsonSerializer.Deserialize<ComponentReferenceIndex>(File.ReadAllText(categoryIndexPath, Encoding.UTF8), options);
                    if (categoryIndex == null || categoryIndex.SemanticDescriptions == null)
                    {
                        continue;
                    }

                    string categoryFolder = Path.GetDirectoryName(categoryIndexPath) ?? familyLibraryFolder;
                    foreach (ComponentSemanticDescription description in categoryIndex.SemanticDescriptions)
                    {
                        if (description == null || string.IsNullOrWhiteSpace(description.FamilyFile))
                        {
                            continue;
                        }

                        string familyFullPath = Path.Combine(categoryFolder, description.FamilyFile.Replace('/', Path.DirectorySeparatorChar));
                        string relativeFamilyPath = MakeRelativePath(familyLibraryFolder, familyFullPath);
                        index.Families.Add(new FamilySemanticEntry
                        {
                            FilePath = relativeFamilyPath,
                            FileName = Path.GetFileName(description.FamilyFile),
                            VisualSummary = description.VisualSummary ?? "",
                            Category = string.IsNullOrWhiteSpace(description.Category) ? categoryIndex.ComponentGroup : description.Category,
                            FamilyType = description.FamilyType ?? "",
                            Features = description.Features ?? new List<string>(),
                            Confidence = description.Confidence,
                            Source = "component_reference_images/category_index.json"
                        });
                    }
                }
                catch
                {
                    // A single broken category should not block the rest of the family library.
                }
            }

            index.Families = index.Families
                .Where(entry => entry != null)
                .GroupBy(entry => (NormalizePathText(entry.FilePath) + "|" + (entry.FileName ?? "")).ToLowerInvariant())
                .Select(group => group.OrderByDescending(entry => entry.Confidence).First())
                .ToList();
            return index;
        }

        private static FamilySemanticEntry FindFamilySemanticEntry(FamilySemanticIndex index, string filePath, string relativePath)
        {
            foreach (FamilySemanticEntry entry in (index == null ? new List<FamilySemanticEntry>() : index.Families ?? new List<FamilySemanticEntry>()))
            {
                if (string.Equals(NormalizePathText(entry.FilePath), NormalizePathText(filePath), StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(NormalizePathText(entry.FilePath), NormalizePathText(relativePath), StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(entry.FileName ?? "", Path.GetFileName(filePath), StringComparison.OrdinalIgnoreCase))
                {
                    return entry;
                }
            }
            return null;
        }

        private static string BuildSemanticText(FamilySemanticEntry entry)
        {
            if (entry == null)
            {
                return "";
            }
            return ((entry.Category ?? "") + " " +
                (entry.FamilyType ?? "") + " " +
                (entry.VisualSummary ?? "") + " " +
                string.Join(" ", entry.Features ?? new List<string>())).ToLowerInvariant();
        }

        private static bool CachedCategoryMatches(BuiltInCategory category, FamilySemanticEntry entry)
        {
            string text = ((entry == null ? "" : entry.Category ?? "") + " " + (entry == null ? "" : entry.FamilyType ?? "")).ToLowerInvariant();
            return ComponentCategoryMatches(category, text);
        }

        private static bool ComponentCategoryMatches(BuiltInCategory category, string text)
        {
            text = (text ?? "").ToLowerInvariant();
            if (category == BuiltInCategory.OST_Doors) return ContainsAny(text, "door", "doors", "门");
            if (category == BuiltInCategory.OST_Windows) return ContainsAny(text, "window", "windows", "窗");
            if (category == BuiltInCategory.OST_StructuralColumns) return ContainsAny(text, "column", "columns", "柱");
            return false;
        }

        private static bool TryDeleteElement(Document doc, ElementId id)
        {
            try
            {
                if (doc != null && id != null && doc.GetElement(id) != null)
                {
                    doc.Delete(id);
                    return true;
                }
            }
            catch
            {
                // Some levels are protected by dependent views or active document state.
            }
            return false;
        }

        private static bool ColumnShapeMatches(ColumnComponent column, string text)
        {
            text = (text ?? "").ToLowerInvariant();
            string requested = ((column == null ? "" : column.Type ?? "") + " " + (column == null ? "" : column.Material ?? "")).ToLowerInvariant();
            if (column != null && column.DiameterMm.HasValue && column.DiameterMm.Value > 0)
            {
                return ContainsAny(text, "round", "circular", "circle", "圆柱", "圆形", "圆");
            }
            if ((column != null && column.WidthMm.HasValue && column.DepthMm.HasValue) || ContainsAny(requested, "rectangular", "square", "矩形", "方"))
            {
                return ContainsAny(text, "rectangular", "square", "矩形", "方形", "方柱", "直角");
            }
            if (ContainsAny(requested, "chamfer", "倒角"))
            {
                return ContainsAny(text, "chamfer", "bevel", "倒角");
            }
            return ContainsAny(text, "column", "columns", "柱");
        }

        private static bool ColumnSizeMatches(ColumnComponent column, string text)
        {
            if (column == null)
            {
                return false;
            }
            text = text ?? "";
            if (column.DiameterMm.HasValue)
            {
                string diameter = Math.Round(column.DiameterMm.Value).ToString(CultureInfo.InvariantCulture);
                return text.Contains(diameter);
            }
            if (column.WidthMm.HasValue && column.DepthMm.HasValue)
            {
                string width = Math.Round(column.WidthMm.Value).ToString(CultureInfo.InvariantCulture);
                string depth = Math.Round(column.DepthMm.Value).ToString(CultureInfo.InvariantCulture);
                return ContainsSizeText(text, width, depth);
            }
            return false;
        }

        private static bool SemanticDescriptionMatches(BuiltInCategory category, OpeningFamilyRequirement requirement, string semanticText)
        {
            if (string.IsNullOrWhiteSpace(semanticText))
            {
                return false;
            }
            if (category == BuiltInCategory.OST_Doors)
            {
                return DoorSemanticMatches(semanticText, requirement);
            }
            string requestedText = string.Join(" ", requirement.TypeHints ?? new List<string>()).ToLowerInvariant();
            return !string.IsNullOrWhiteSpace(requestedText) && requestedText.Split(new[] { ' ', '-', '_', '/', '\\' }, StringSplitOptions.RemoveEmptyEntries).Any(token => token.Length > 2 && semanticText.Contains(token));
        }

        private static string NormalizePathText(string value)
        {
            return (value ?? "").Replace('\\', '/').Trim();
        }

        private static bool ContainsSizeText(string text, string width, string height)
        {
            return text.Contains(width + "x" + height) ||
                text.Contains(width + "_" + height) ||
                text.Contains(width + "-" + height) ||
                text.Contains(width + " " + height) ||
                (text.Contains(width) && text.Contains(height));
        }

        private static string MakeRelativePath(string rootFolder, string filePath)
        {
            if (string.IsNullOrWhiteSpace(rootFolder) || string.IsNullOrWhiteSpace(filePath))
            {
                return filePath ?? "";
            }

            try
            {
                string root = Path.GetFullPath(rootFolder).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
                string full = Path.GetFullPath(filePath);
                if (full.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                {
                    return full.Substring(root.Length);
                }
            }
            catch
            {
                return filePath;
            }

            return filePath;
        }

        private static bool DoorSemanticMatches(string familyName, OpeningFamilyRequirement requirement)
        {
            string requestedText = (requirement.GeneratedName + " " + string.Join(" ", requirement.TypeHints ?? new List<string>())).ToLowerInvariant();
            List<string[]> aliasGroups = new List<string[]>
            {
                new[] { "flush", "flush panel", "plain" },
                new[] { "glazed", "glass" },
                new[] { "vision", "lite", "light", "view panel" },
                new[] { "louvered", "louvre", "louver" },
                new[] { "w side panel", "with side panel", "side panel", "sidelite", "side lite" },
                new[] { "transom" },
                new[] { "dbl acting", "double acting" },
                new[] { "sliding", "slider" },
                new[] { "pocket" },
                new[] { "bifold", "bi-fold", "bi fold" },
                new[] { "overhead" },
                new[] { "rolling", "roll up", "roll-up" },
                new[] { "garage" },
                new[] { "cased opening" },
                new[] { "opening" }
            };

            foreach (string[] aliases in aliasGroups)
            {
                bool requestHasAlias = aliases.Any(alias => requestedText.Contains(alias));
                if (requestHasAlias && aliases.Any(alias => familyName.Contains(alias)))
                {
                    return true;
                }
            }

            return false;
        }

        private static int RequestedDoorLeafCount(OpeningFamilyRequirement requirement, string widthText)
        {
            string requestedText = ((requirement == null ? "" : requirement.GeneratedName ?? "") + " " +
                string.Join(" ", requirement == null ? new List<string>() : requirement.TypeHints ?? new List<string>()) + " " +
                (requirement == null ? "" : requirement.Evidence == null ? "" : string.Join(" ", requirement.Evidence))).ToLowerInvariant();
            if (string.Equals(DoorMotion(requestedText), "rolling_shutter", StringComparison.OrdinalIgnoreCase))
            {
                return 0;
            }
            int explicitCount = DoorLeafCount(requestedText);
            if (explicitCount > 0)
            {
                return explicitCount;
            }
            double widthMm;
            if (double.TryParse(widthText, NumberStyles.Float, CultureInfo.InvariantCulture, out widthMm))
            {
                if (widthMm >= 3200) return 4;
                if (widthMm >= 1200) return 2;
                if (widthMm > 0) return 1;
            }
            return 0;
        }

        private static int RequestedDoorLeafCount(string typeName, double? widthMm)
        {
            if (string.Equals(DoorMotion(typeName), "rolling_shutter", StringComparison.OrdinalIgnoreCase))
            {
                return 0;
            }
            int explicitCount = DoorLeafCount(typeName ?? "");
            if (explicitCount > 0)
            {
                return explicitCount;
            }
            if (widthMm.HasValue && widthMm.Value > 0)
            {
                if (widthMm.Value >= 3200) return 4;
                if (widthMm.Value >= 1200) return 2;
                return 1;
            }
            return 0;
        }

        private static List<FamilySymbol> FilterDoorSymbolsByLeafIntent(
            List<FamilySymbol> symbols,
            BuiltInCategory category,
            string typeName,
            double? widthMm,
            bool allowUnknownLeafIntent = false)
        {
            symbols = symbols ?? new List<FamilySymbol>();
            if (category != BuiltInCategory.OST_Doors)
            {
                return symbols;
            }

            int requestedLeafCount = RequestedDoorLeafCount(typeName, widthMm);
            string requestedMotion = DoorMotion(typeName);
            return symbols
                .Where(symbol =>
                {
                    string symbolText = (symbol.FamilyName ?? "") + " " + (symbol.Name ?? "");
                    int symbolLeafCount = DoorLeafCount(symbolText);
                    string symbolMotion = DoorMotion(symbolText);
                    bool leafCompatible = requestedLeafCount <= 0 || (requestedLeafCount >= 2
                        ? symbolLeafCount == requestedLeafCount || (allowUnknownLeafIntent && symbolLeafCount == 0)
                        : symbolLeafCount == 0 || symbolLeafCount == requestedLeafCount);
                    bool motionCompatible = string.IsNullOrWhiteSpace(requestedMotion) ||
                        string.IsNullOrWhiteSpace(symbolMotion) ||
                        string.Equals(requestedMotion, symbolMotion, StringComparison.OrdinalIgnoreCase);
                    return leafCompatible && motionCompatible;
                })
                .ToList();
        }

        private static string DoorMotion(string text)
        {
            text = (text ?? "").ToLowerInvariant();
            if (ContainsAny(text, "rolling shutter", "roller shutter", "roll-up", "roll up", "overhead door", "garage door", "卷帘", "卷闸"))
            {
                return "rolling_shutter";
            }
            if (ContainsAny(text, "sliding", "slider", "pocket", "推拉", "移门", "墙中"))
            {
                return "sliding";
            }
            if (ContainsAny(text, "swing", "hinged", "平开", "对开"))
            {
                return "swing";
            }
            if (DoorLeafCount(text) > 0 && ContainsAny(text, "door", "门"))
            {
                return "swing";
            }
            return "";
        }

        private static int DoorLeafCount(string text)
        {
            text = (text ?? "").ToLowerInvariant();
            if (ContainsAny(text, "four leaf", "four panel", "four sliding", "4 leaf", "4-leaf", "四扇", "四开"))
            {
                return 4;
            }
            if (ContainsAny(text, "double leaf", "double-leaf", "double door", "double swing", "double sliding", "two leaf", "two-leaf", "2 leaf", "2-leaf", "dbl", "双扇", "双开", "对开", "双面"))
            {
                return 2;
            }
            if (ContainsAny(text, "single leaf", "single-leaf", "single door", "single swing", "single sliding", "one leaf", "one-leaf", "1 leaf", "1-leaf", "单扇", "单开", "单嵌板"))
            {
                return 1;
            }
            return 0;
        }

        private static bool ContainsAny(string text, params string[] values)
        {
            return values.Any(value => text.Contains(value));
        }

        private static bool LoadCandidateFamiliesIfConfirmed(Document doc, List<FamilyLoadCandidate> candidates)
        {
            if (candidates == null || candidates.Count == 0)
            {
                return false;
            }

            List<FamilyLoadCandidate> loadList = candidates
                .GroupBy(c => c.FilePath, StringComparer.OrdinalIgnoreCase)
                .Select(g => g.OrderByDescending(c => c.Score).First())
                .Take(12)
                .ToList();

            StringBuilder sb = new StringBuilder();
            sb.AppendLine("Candidate family files were found in the selected family library.");
            sb.AppendLine();
            foreach (FamilyLoadCandidate candidate in loadList)
            {
                sb.AppendLine(candidate.FileName + " -> " + candidate.RequirementName);
            }
            sb.AppendLine();
            sb.AppendLine("Load these family files before modeling?");

            TaskDialog dialog = new TaskDialog("Load Candidate Families");
            dialog.MainInstruction = "Load candidate families?";
            dialog.MainContent = sb.ToString();
            dialog.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No;
            dialog.DefaultButton = TaskDialogResult.Yes;
            if (dialog.Show() != TaskDialogResult.Yes)
            {
                return false;
            }

            using (Transaction tx = new Transaction(doc, "Load candidate AI families"))
            {
                tx.Start();
                foreach (FamilyLoadCandidate candidate in loadList)
                {
                    try
                    {
                        Family loadedFamily;
                        doc.LoadFamily(candidate.FilePath, out loadedFamily);
                        RenameLoadedFamilyForEnglishWorkflow(loadedFamily, candidate.FilePath);
                    }
                    catch
                    {
                        // Loading failures are reported indirectly by later missing-family checks.
                    }
                }
                tx.Commit();
            }
            return true;
        }

        private static void ApplyAcceptedColumnFamilyCandidates(StandardModel model, List<FamilyLoadCandidate> candidates)
        {
            if (model == null || model.Components == null)
            {
                return;
            }

            foreach (ColumnComponent column in model.Components.Columns ?? new List<ColumnComponent>())
            {
                if (column == null || !string.IsNullOrWhiteSpace(column.FamilyFile))
                {
                    continue;
                }

                FamilyLoadCandidate candidate = (candidates ?? new List<FamilyLoadCandidate>())
                    .Where(c => c.Category == BuiltInCategory.OST_StructuralColumns &&
                        string.Equals(c.RequirementName, BuildColumnRequirementName(column), StringComparison.OrdinalIgnoreCase) &&
                        !string.IsNullOrWhiteSpace(c.FilePath))
                    .OrderByDescending(c => c.Score)
                    .ThenBy(c => c.FileName)
                    .FirstOrDefault();
                if (candidate == null)
                {
                    continue;
                }

                column.FamilyFile = candidate.FilePath;
                if (string.IsNullOrWhiteSpace(column.FamilyName))
                {
                    column.FamilyName = Path.GetFileNameWithoutExtension(candidate.FilePath);
                }
            }
        }

        private static int Count<T>(List<T> items)
        {
            return items == null ? 0 : items.Count;
        }

        private static string PlanCountText(StandardModel model)
        {
            if (model.LlmRevitExecutionPlan == null || model.LlmRevitExecutionPlan.Operations == null)
            {
                return "not provided; modeling from components";
            }
            return model.LlmRevitExecutionPlan.Operations.Count.ToString(CultureInfo.InvariantCulture);
        }

        private static int CountModelableItems(StandardModel model)
        {
            return AllComponents(model).Count(item => !IsRejected(item));
        }

        private static int CountReviewItems(StandardModel model)
        {
            return AllComponents(model).Count(item => NeedsReview(item));
        }

        private static IEnumerable<ComponentBase> AllComponents(StandardModel model)
        {
            ComponentSet components = model.Components ?? new ComponentSet();
            foreach (ComponentBase item in components.Levels ?? new List<LevelComponent>()) yield return item;
            foreach (ComponentBase item in components.Grids ?? new List<GridComponent>()) yield return item;
            foreach (ComponentBase item in components.Columns ?? new List<ColumnComponent>()) yield return item;
            foreach (ComponentBase item in components.Walls ?? new List<WallComponent>()) yield return item;
            foreach (ComponentBase item in components.Slabs ?? new List<SlabComponent>()) yield return item;
            foreach (ComponentBase item in components.FloorOpenings ?? new List<FloorOpeningComponent>()) yield return item;
            foreach (ComponentBase item in components.Doors ?? new List<OpeningComponent>()) yield return item;
            foreach (ComponentBase item in components.Windows ?? new List<OpeningComponent>()) yield return item;
            foreach (ComponentBase item in components.Rooms ?? new List<RoomComponent>()) yield return item;
            foreach (ComponentBase item in model.Rooms ?? new List<RoomComponent>()) yield return item;
            foreach (ComponentBase item in components.Stairs ?? new List<GenericModelComponent>()) yield return item;
            foreach (ComponentBase item in components.Railings ?? new List<GenericModelComponent>()) yield return item;
            foreach (ComponentBase item in components.Roofs ?? new List<GenericModelComponent>()) yield return item;
            foreach (ComponentBase item in components.Parapets ?? new List<ParapetComponent>()) yield return item;
        }

        private static bool IsReadyForModeling(ComponentBase item)
        {
            string status = (item.ReviewStatus ?? "").Trim().ToLowerInvariant();
            return status == "ready" || status == "confirmed";
        }

        private static bool NeedsReview(ComponentBase item)
        {
            string status = (item.ReviewStatus ?? "").Trim().ToLowerInvariant();
            return status == "needs_review";
        }

        private static bool IsRejected(ComponentBase item)
        {
            return GetSkipReason(item) != null;
        }

        private static string GetSkipReason(ComponentBase item)
        {
            if (item == null)
            {
                return "Skipped because the component is empty.";
            }
            string reviewStatus = (item.ReviewStatus ?? "").Trim().ToLowerInvariant();
            if (reviewStatus == "rejected")
            {
                return "Skipped because review_status is rejected.";
            }
            string modelingStatus = (item.ModelingStatus ?? "").Trim().ToLowerInvariant();
            if (modelingStatus == "blocked" || modelingStatus == "not_modelable")
            {
                return "Skipped because modeling_status is " + item.ModelingStatus + ".";
            }
            string executionScope = (item.RevitExecutionScope ?? "").Trim().ToLowerInvariant();
            if (executionScope == "review_only" || executionScope == "blocked" || executionScope == "exclude")
            {
                return "Skipped because revit_execution_scope is " + item.RevitExecutionScope + ".";
            }
            return null;
        }

        private static Dictionary<string, Level> CreateLevels(Document doc, StandardModel model, ModelingReport report)
        {
            Dictionary<string, Level> created = new Dictionary<string, Level>();
            foreach (LevelComponent item in model.Components.Levels ?? new List<LevelComponent>())
            {
                try
                {
                    if (IsRejected(item))
                    {
                        throw new InvalidOperationException(GetSkipReason(item));
                    }
                    if (string.IsNullOrWhiteSpace(item.Name))
                    {
                        throw new InvalidOperationException("Missing level name.");
                    }
                    Level level = FindLevel(doc, item.Name);
                    if (level == null)
                    {
                        level = Level.Create(doc, MmToFeet(item.ElevationMm));
                        level.Name = item.Name;
                    }
                    else
                    {
                        SetLevelElevation(level, item.ElevationMm);
                    }
                    RegisterLevelAlias(created, item.Name, level);
                    RegisterLevelAlias(created, item.Id, level);
                    report.Success("levels", item.Id, ElementIdValue(level.Id), item.ReviewStatus, "");
                }
                catch (Exception ex)
                {
                    report.Failure("levels", item.Id, item.ReviewStatus, ex.Message);
                }
            }
            return created;
        }

        private static void RegisterLevelAlias(Dictionary<string, Level> levels, string key, Level level)
        {
            if (levels == null || level == null || string.IsNullOrWhiteSpace(key))
            {
                return;
            }
            levels[key] = level;
        }

        private static Dictionary<string, Grid> CreateGrids(Document doc, StandardModel model, ModelingReport report)
        {
            Dictionary<string, Grid> created = new Dictionary<string, Grid>();
            foreach (GridComponent item in model.Components.Grids ?? new List<GridComponent>())
            {
                try
                {
                    if (IsRejected(item))
                    {
                        throw new InvalidOperationException(GetSkipReason(item));
                    }
                    if (item.Start == null || item.End == null)
                    {
                        throw new InvalidOperationException("Missing grid start or end point.");
                    }
                    if (IsSamePoint(item.Start, item.End))
                    {
                        throw new InvalidOperationException("Grid start and end points are identical.");
                    }
                    Line line = Line.CreateBound(ToXyz(item.Start), ToXyz(item.End));
                    string gridName = SafeGridName(item.Name, item.Id);
                    Grid grid = FindGrid(doc, gridName);
                    string defaults = "";
                    if (grid == null)
                    {
                        grid = Grid.Create(doc, line);
                        if (!string.IsNullOrWhiteSpace(gridName))
                        {
                            grid.Name = gridName;
                        }
                        if (!string.Equals(gridName, item.Name, StringComparison.Ordinal))
                        {
                            defaults = AppendNote(defaults, "grid name sanitized from " + item.Name + " to " + gridName);
                        }
                    }
                    else
                    {
                        defaults = "existing grid with the same name was reused";
                    }
                    created[item.Id] = grid;
                    if (NeedsReview(item))
                    {
                        defaults = AppendNote(defaults, "created although review_status=needs_review; verify manually");
                    }
                    report.Success("grids", item.Id, ElementIdValue(grid.Id), item.ReviewStatus, defaults);
                }
                catch (Exception ex)
                {
                    report.Failure("grids", item.Id, item.ReviewStatus, ex.Message);
                }
            }
            return created;
        }

        private static Dictionary<string, Wall> CreateWalls(Document doc, StandardModel model, Dictionary<string, Level> levels, ModelingReport report)
        {
            Dictionary<string, Wall> created = new Dictionary<string, Wall>();

            List<WallComponent> inputWalls = model.Components.Walls ?? new List<WallComponent>();
            HashSet<string> parapetSourceWallIds = FindWallsSupersededByParapets(
                inputWalls,
                model.Components.Parapets ?? new List<ParapetComponent>());
            Dictionary<string, int> removedSupersededWalls = RemoveExistingWallsWithMarks(doc, parapetSourceWallIds);
            foreach (WallComponent superseded in inputWalls.Where(wall =>
                wall != null &&
                !string.IsNullOrWhiteSpace(wall.Id) &&
                parapetSourceWallIds.Contains(wall.Id)))
            {
                int removedCount = removedSupersededWalls.TryGetValue(superseded.Id, out int count) ? count : 0;
                report.Skip(
                    "walls",
                    superseded.Id,
                    superseded.ReviewStatus,
                    "roof-plan wall line is superseded by a dedicated parapet component and was not created as a full-height wall"
                    + "; removed_existing_full_height_wall_count="
                    + removedCount.ToString(CultureInfo.InvariantCulture));
            }

            List<OpeningComponent> wallOpenings = (model.Components.Doors ?? new List<OpeningComponent>())
                .Concat(model.Components.Windows ?? new List<OpeningComponent>())
                .ToList();
            List<WallRun> wallRuns = BuildWallRuns(
                inputWalls.Where(wall =>
                    wall == null ||
                    string.IsNullOrWhiteSpace(wall.Id) ||
                    !parapetSourceWallIds.Contains(wall.Id)).ToList(),
                wallOpenings,
                report);
            Dictionary<string, Point3> exteriorCentersByLevel = BuildExteriorWallCentersByLevel(wallRuns);
            foreach (WallRun run in wallRuns)
            {
                WallComponent item = run.Primary;
                try
                {
                    if (!levels.TryGetValue(item.BaseLevel, out Level baseLevel))
                    {
                        throw new InvalidOperationException("Base level not found: " + item.BaseLevel);
                    }
                    string wallTypeNote;
                    WallType wallType = FindWallType(doc, item, out wallTypeNote);
                    if (wallType == null)
                    {
                        throw new InvalidOperationException("No basic wall type was found in this Revit project.");
                    }
                    double height = item.HeightMm.HasValue ? MmToFeet(item.HeightMm.Value) : MmToFeet(3000);
                    Line line = Line.CreateBound(ToXyz(run.Start), ToXyz(run.End));
                    bool flipExteriorAtCreation = ResolveExteriorWallFlipAtCreation(run, exteriorCentersByLevel, out string orientationNote);
                    Wall wall = Wall.Create(doc, line, wallType.Id, baseLevel.Id, height, 0, flipExteriorAtCreation, false);
                    Parameter roomBounding = wall.get_Parameter(BuiltInParameter.WALL_ATTR_ROOM_BOUNDING);
                    if (roomBounding != null && !roomBounding.IsReadOnly)
                    {
                        roomBounding.Set(1);
                    }
                    if (!string.IsNullOrWhiteSpace(item.TopLevel) && levels.TryGetValue(item.TopLevel, out Level topLevel))
                    {
                        SetElementIdParameter(wall, BuiltInParameter.WALL_HEIGHT_TYPE, topLevel.Id);
                        SetDoubleParameter(wall, BuiltInParameter.WALL_TOP_OFFSET, 0);
                    }
                    SetStringParameter(wall, BuiltInParameter.ALL_MODEL_MARK, run.Mark);
                    SetStringParameter(wall, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, BuildMetadataNote(item) + "; merged_wall_ids=" + string.Join("|", run.Items.Select(w => w.Id)));
                    string defaults = item.HeightMm.HasValue ? "" : "height_mm defaulted to 3000 for Revit API call";
                    defaults = AppendNote(defaults, wallTypeNote);
                    defaults = AppendNote(defaults, orientationNote);
                    if (run.Items.Count > 1)
                    {
                        defaults = AppendNote(defaults, "merged " + run.Items.Count.ToString(CultureInfo.InvariantCulture) + " broken wall segments into one continuous Revit host wall");
                    }
                    if (run.RecheckDuplicateRunCount > 0)
                    {
                        defaults = AppendNote(
                            defaults,
                            "final wall recheck removed "
                            + run.RecheckDuplicateRunCount.ToString(CultureInfo.InvariantCulture)
                            + " same-position duplicate wall run(s) before Revit creation"
                        );
                    }
                    if (run.RecheckPropertyConflictCount > 0)
                    {
                        defaults = AppendNote(
                            defaults,
                            "same-position duplicates had "
                            + run.RecheckPropertyConflictCount.ToString(CultureInfo.InvariantCulture)
                            + " thickness/material conflict(s); retained the longest best-described wall and flagged review"
                        );
                    }
                    if (NeedsReview(item))
                    {
                        defaults = AppendNote(defaults, "created although review_status=needs_review; verify manually");
                    }
                    foreach (WallComponent segment in run.Items)
                    {
                        created[segment.Id] = wall;
                        report.Success("walls", segment.Id, ElementIdValue(wall.Id), segment.ReviewStatus, defaults);
                    }
                }
                catch (Exception ex)
                {
                    foreach (WallComponent segment in run.Items)
                    {
                        report.Failure("walls", segment.Id, segment.ReviewStatus, ex.Message);
                    }
                }
            }
            return created;
        }

        private static HashSet<string> FindWallsSupersededByParapets(
            List<WallComponent> walls,
            List<ParapetComponent> parapets)
        {
            Dictionary<string, WallComponent> wallsById = (walls ?? new List<WallComponent>())
                .Where(wall => wall != null && !string.IsNullOrWhiteSpace(wall.Id))
                .GroupBy(wall => wall.Id, StringComparer.OrdinalIgnoreCase)
                .ToDictionary(group => group.Key, group => group.First(), StringComparer.OrdinalIgnoreCase);
            HashSet<string> superseded = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (ParapetComponent parapet in parapets ?? new List<ParapetComponent>())
            {
                if (parapet == null ||
                    string.IsNullOrWhiteSpace(parapet.ExteriorMaterialSourceWallId) ||
                    !wallsById.TryGetValue(parapet.ExteriorMaterialSourceWallId, out WallComponent sourceWall))
                {
                    continue;
                }
                if (IsSameRoofEdgeAsParapet(sourceWall, parapet))
                {
                    superseded.Add(sourceWall.Id);
                }
            }
            return superseded;
        }

        private static bool IsSameRoofEdgeAsParapet(WallComponent wall, ParapetComponent parapet)
        {
            if (wall == null || parapet == null ||
                wall.Start == null || wall.End == null ||
                parapet.Start == null || parapet.End == null)
            {
                return false;
            }
            if (!string.IsNullOrWhiteSpace(wall.DrawingId) &&
                !string.IsNullOrWhiteSpace(parapet.DrawingId) &&
                !string.Equals(wall.DrawingId, parapet.DrawingId, StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            string parapetLevel = FirstNonEmpty(parapet.RevitBaseLevel, parapet.BaseLevel);
            if (!string.IsNullOrWhiteSpace(wall.BaseLevel) &&
                !string.IsNullOrWhiteSpace(parapetLevel) &&
                !string.Equals(wall.BaseLevel, parapetLevel, StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }

            double wallDx = wall.End.X - wall.Start.X;
            double wallDy = wall.End.Y - wall.Start.Y;
            double parapetDx = parapet.End.X - parapet.Start.X;
            double parapetDy = parapet.End.Y - parapet.Start.Y;
            double wallLength = Math.Sqrt(wallDx * wallDx + wallDy * wallDy);
            double parapetLength = Math.Sqrt(parapetDx * parapetDx + parapetDy * parapetDy);
            if (wallLength <= 1 || parapetLength <= 1)
            {
                return false;
            }
            double normalizedCross = Math.Abs(wallDx * parapetDy - wallDy * parapetDx) / (wallLength * parapetLength);
            if (normalizedCross > Math.Sin(Math.PI / 180.0 * 3.0))
            {
                return false;
            }

            double offset = Math.Abs(
                (wall.Start.X - parapet.Start.X) * parapetDy -
                (wall.Start.Y - parapet.Start.Y) * parapetDx) / parapetLength;
            double offsetTolerance = Math.Max(
                500.0,
                (wall.ThicknessMm ?? 200.0) + (parapet.ThicknessMm ?? 200.0));
            if (offset > offsetTolerance)
            {
                return false;
            }

            double ux = parapetDx / parapetLength;
            double uy = parapetDy / parapetLength;
            double wallStartProjection = (wall.Start.X - parapet.Start.X) * ux + (wall.Start.Y - parapet.Start.Y) * uy;
            double wallEndProjection = (wall.End.X - parapet.Start.X) * ux + (wall.End.Y - parapet.Start.Y) * uy;
            double overlap = Math.Max(
                0,
                Math.Min(Math.Max(wallStartProjection, wallEndProjection), parapetLength) -
                Math.Max(Math.Min(wallStartProjection, wallEndProjection), 0));
            return overlap / Math.Min(wallLength, parapetLength) >= 0.80;
        }

        private static Dictionary<string, int> RemoveExistingWallsWithMarks(Document doc, HashSet<string> marks)
        {
            Dictionary<string, int> removed = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            if (marks == null || marks.Count == 0)
            {
                return removed;
            }
            List<Wall> existingWalls = new FilteredElementCollector(doc)
                .OfCategory(BuiltInCategory.OST_Walls)
                .WhereElementIsNotElementType()
                .Cast<Wall>()
                .ToList();
            foreach (Wall existing in existingWalls)
            {
                Parameter markParameter = existing.get_Parameter(BuiltInParameter.ALL_MODEL_MARK);
                string mark = markParameter == null ? "" : markParameter.AsString() ?? "";
                if (!marks.Contains(mark))
                {
                    continue;
                }
                doc.Delete(existing.Id);
                removed[mark] = removed.TryGetValue(mark, out int count) ? count + 1 : 1;
            }
            return removed;
        }

        private static Dictionary<string, Point3> BuildExteriorWallCentersByLevel(List<WallRun> wallRuns)
        {
            Dictionary<string, Point3> centers = new Dictionary<string, Point3>(StringComparer.OrdinalIgnoreCase);
            foreach (IGrouping<string, WallRun> group in (wallRuns ?? new List<WallRun>())
                .Where(run => run != null && run.Items != null && run.Items.Any(IsExteriorWall))
                .GroupBy(run => run.Primary == null ? "" : (run.Primary.BaseLevel ?? ""), StringComparer.OrdinalIgnoreCase))
            {
                double weightedX = 0;
                double weightedY = 0;
                double totalLength = 0;
                foreach (WallRun run in group)
                {
                    double dx = run.End.X - run.Start.X;
                    double dy = run.End.Y - run.Start.Y;
                    double length = Math.Sqrt(dx * dx + dy * dy);
                    if (length <= 1)
                    {
                        continue;
                    }
                    weightedX += ((run.Start.X + run.End.X) / 2.0) * length;
                    weightedY += ((run.Start.Y + run.End.Y) / 2.0) * length;
                    totalLength += length;
                }
                if (totalLength > 0)
                {
                    centers[group.Key] = new Point3 { X = weightedX / totalLength, Y = weightedY / totalLength, Z = 0 };
                }
            }
            return centers;
        }

        private static bool ResolveExteriorWallFlipAtCreation(
            WallRun run,
            Dictionary<string, Point3> exteriorCentersByLevel,
            out string note)
        {
            note = "";
            if (run == null || run.Items == null || !run.Items.Any(IsExteriorWall))
            {
                return false;
            }

            string levelKey = run.Primary == null ? "" : (run.Primary.BaseLevel ?? "");
            if (!exteriorCentersByLevel.TryGetValue(levelKey, out Point3 center))
            {
                note = "exterior wall detected; creation orientation unchanged because the exterior-wall center could not be resolved";
                return false;
            }

            double midpointX = (run.Start.X + run.End.X) / 2.0;
            double midpointY = (run.Start.Y + run.End.Y) / 2.0;
            XYZ desiredOutward = new XYZ(midpointX - center.X, midpointY - center.Y, 0);
            if (desiredOutward.GetLength() <= 0.001)
            {
                note = "exterior wall detected; creation orientation unchanged because its outward direction was ambiguous";
                return false;
            }

            XYZ tangent = new XYZ(run.End.X - run.Start.X, run.End.Y - run.Start.Y, 0);
            if (tangent.GetLength() <= 0.001)
            {
                note = "exterior wall detected; creation orientation unchanged because its direction was invalid";
                return false;
            }

            // Revit's default exterior face is on the left side of the wall's drawing direction.
            XYZ defaultExterior = XYZ.BasisZ.CrossProduct(tangent.Normalize());
            bool flip = defaultExterior.DotProduct(desiredOutward.Normalize()) < 0;
            note = flip
                ? "exterior compound wall created with flip=true so its finish face points away from the building center"
                : "exterior compound wall created with flip=false; its finish face already points away from the building center";
            return flip;
        }

        private static bool IsExteriorWall(WallComponent wall)
        {
            if (wall == null)
            {
                return false;
            }

            List<WallMaterialLayer> layers = wall.MaterialLayers ?? new List<WallMaterialLayer>();
            List<string> scopes = layers
                .Where(layer => layer != null && !string.IsNullOrWhiteSpace(layer.Scope))
                .Select(layer => layer.Scope.Trim().ToLowerInvariant())
                .ToList();
            if (scopes.Count > 0)
            {
                return scopes.Any(scope => scope == "exterior" || scope.Contains("外墙"));
            }

            string evidence = string.Join(" ", new[] { wall.Type, wall.Notes, wall.MaterialReason }
                .Where(value => !string.IsNullOrWhiteSpace(value))).ToLowerInvariant();
            return ContainsAny(evidence, "exterior wall", "external wall", "外墙");
        }

        private static void CreateNativeRooms(Document doc, StandardModel model, Dictionary<string, Level> levels, ModelingReport report)
        {
            List<RoomComponent> rooms = (model.Rooms ?? new List<RoomComponent>())
                .Concat(model.Components.Rooms ?? new List<RoomComponent>())
                .Where(item => item != null)
                .GroupBy(item => string.IsNullOrWhiteSpace(item.Id) ? Guid.NewGuid().ToString() : item.Id, StringComparer.OrdinalIgnoreCase)
                .Select(group => group.First())
                .ToList();
            if (rooms.Count == 0)
            {
                return;
            }

            using (Transaction tx = new Transaction(doc, "Create AI native rooms"))
            {
                tx.Start();
                doc.Regenerate();
                foreach (RoomComponent item in rooms)
                {
                    Room room = null;
                    try
                    {
                        if (IsRejected(item))
                        {
                            throw new InvalidOperationException(GetSkipReason(item));
                        }
                        if (item.Location == null)
                        {
                            throw new InvalidOperationException("Room location/space_seed_point is missing.");
                        }
                        Level level = ResolveGenericLevel(item.Level, levels);
                        if (level == null)
                        {
                            throw new InvalidOperationException("Room level not found: " + item.Level);
                        }

                        UV seed = new UV(MmToFeet(item.Location.X), MmToFeet(item.Location.Y));
                        room = doc.Create.NewRoom(level, seed);
                        if (room == null)
                        {
                            throw new InvalidOperationException("Revit could not create a native Room at the supplied seed point.");
                        }
                        SetStringParameter(room, BuiltInParameter.ROOM_NAME, item.Name);
                        SetStringParameter(room, BuiltInParameter.ROOM_NUMBER, FirstNonEmpty(item.Number, item.RoomNumber));
                        SetStringParameter(room, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, BuildMetadataNote(item));
                        doc.Regenerate();

                        IList<IList<BoundarySegment>> boundaryLoops = room.GetBoundarySegments(new SpatialElementBoundaryOptions());
                        if (room.Area <= 0 || boundaryLoops == null || boundaryLoops.Count == 0)
                        {
                            doc.Delete(room.Id);
                            room = null;
                            throw new InvalidOperationException(
                                "The room seed point is not inside a closed Room Bounding enclosure on level " + level.Name + "."
                            );
                        }
                        int boundarySegmentCount = boundaryLoops.Sum(loop => loop == null ? 0 : loop.Count);
                        string note = "native Revit Room; boundary resolved from Room Bounding elements; boundary_segments=" +
                            boundarySegmentCount.ToString(CultureInfo.InvariantCulture);
                        report.Success("rooms", item.Id, ElementIdValue(room.Id), item.ReviewStatus, note);
                    }
                    catch (Exception ex)
                    {
                        if (room != null && room.Document != null)
                        {
                            try
                            {
                                doc.Delete(room.Id);
                            }
                            catch
                            {
                            }
                        }
                        report.Failure("rooms", item.Id, item.ReviewStatus, ex.Message);
                    }
                }
                tx.Commit();
            }
        }

        private static Dictionary<string, Wall> CreateParapets(
            Document doc,
            StandardModel model,
            Dictionary<string, Level> levels,
            Dictionary<string, Wall> sourceWalls,
            ModelingReport report)
        {
            Dictionary<string, Wall> created = new Dictionary<string, Wall>();
            List<ParapetComponent> parapets = model.Components.Parapets ?? new List<ParapetComponent>();
            Dictionary<string, WallComponent> sourceWallComponents = (model.Components.Walls ?? new List<WallComponent>())
                .Where(wall => wall != null && !string.IsNullOrWhiteSpace(wall.Id))
                .GroupBy(wall => wall.Id, StringComparer.OrdinalIgnoreCase)
                .ToDictionary(group => group.Key, group => group.First(), StringComparer.OrdinalIgnoreCase);
            Dictionary<string, int> removedExistingByMark = RemoveExistingParapetsWithSameMarks(doc, parapets);
            Dictionary<string, WallRun> parapetRunsById = parapets
                .Where(item => item != null && item.Start != null && item.End != null && !string.IsNullOrWhiteSpace(item.Id))
                .ToDictionary(
                    item => item.Id,
                    item => new WallRun
                    {
                        Items = new List<WallComponent> { item },
                        Primary = item,
                        Start = item.Start,
                        End = item.End,
                        Mark = item.Id
                    },
                    StringComparer.OrdinalIgnoreCase);
            Dictionary<string, Point3> exteriorCentersByLevel = BuildExteriorWallCentersByLevel(parapetRunsById.Values.ToList());
            foreach (ParapetComponent item in parapets)
            {
                try
                {
                    if (item.Start == null || item.End == null || !item.HeightMm.HasValue || item.HeightMm.Value <= 0 || !item.BottomRelativeElevationMm.HasValue)
                    {
                        throw new InvalidOperationException("Parapet requires start/end, height_mm and bottom_relative_elevation_mm.");
                    }
                    string baseLevelName = FirstNonEmpty(item.RevitBaseLevel, item.BaseLevel);
                    if (!levels.TryGetValue(baseLevelName, out Level baseLevel))
                    {
                        throw new InvalidOperationException("Parapet base level not found: " + baseLevelName);
                    }
                    double resolvedBottomMm = FeetToMm(baseLevel.Elevation) + item.BottomRelativeElevationMm.Value;
                    if (item.RevitBottomElevationMm.HasValue && Math.Abs(resolvedBottomMm - item.RevitBottomElevationMm.Value) > 10)
                    {
                        throw new InvalidOperationException("Parapet vertical contract mismatch: Revit base level plus relative offset does not match revit_bottom_elevation_mm.");
                    }
                    if (Math.Abs(resolvedBottomMm - item.Start.Z) > 10)
                    {
                        throw new InvalidOperationException("Parapet CAD Start_Z conflicts with the Revit vertical contract; Start_Z must never be used as Base Offset.");
                    }
                    string wallTypeNote;
                    WallType wallType = FindOrCreateParapetWallType(
                        doc,
                        item,
                        sourceWalls,
                        sourceWallComponents,
                        out wallTypeNote);
                    if (wallType == null)
                    {
                        throw new InvalidOperationException("No basic wall type was found in this Revit project.");
                    }
                    Line line = Line.CreateBound(
                        new XYZ(MmToFeet(item.Start.X), MmToFeet(item.Start.Y), 0),
                        new XYZ(MmToFeet(item.End.X), MmToFeet(item.End.Y), 0));
                    WallRun parapetRun = parapetRunsById.TryGetValue(item.Id ?? "", out WallRun resolvedRun) ? resolvedRun : null;
                    bool flipExteriorAtCreation = ResolveExteriorWallFlipAtCreation(parapetRun, exteriorCentersByLevel, out string orientationNote);
                    Wall wall = Wall.Create(
                        doc,
                        line,
                        wallType.Id,
                        baseLevel.Id,
                        MmToFeet(item.HeightMm.Value),
                        MmToFeet(item.BottomRelativeElevationMm.Value),
                        flipExteriorAtCreation,
                        false);
                    Parameter topConstraint = wall.get_Parameter(BuiltInParameter.WALL_HEIGHT_TYPE);
                    if (topConstraint != null && !topConstraint.IsReadOnly)
                    {
                        topConstraint.Set(ElementId.InvalidElementId);
                    }
                    Parameter baseOffset = wall.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET);
                    if (baseOffset != null && !baseOffset.IsReadOnly)
                    {
                        baseOffset.Set(MmToFeet(item.BottomRelativeElevationMm.Value));
                    }
                    Parameter bottomExtension = wall.get_Parameter(BuiltInParameter.WALL_BOTTOM_EXTENSION_DIST_PARAM);
                    if (bottomExtension != null && !bottomExtension.IsReadOnly)
                    {
                        bottomExtension.Set(0.0);
                    }
                    Parameter topExtension = wall.get_Parameter(BuiltInParameter.WALL_TOP_EXTENSION_DIST_PARAM);
                    if (topExtension != null && !topExtension.IsReadOnly)
                    {
                        topExtension.Set(0.0);
                    }
                    Parameter unconnectedHeight = wall.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM);
                    if (unconnectedHeight == null || unconnectedHeight.IsReadOnly)
                    {
                        throw new InvalidOperationException("Parapet unconnected-height parameter is unavailable or read-only.");
                    }
                    unconnectedHeight.Set(MmToFeet(item.HeightMm.Value));
                    doc.Regenerate();
                    double actualHeightMm = FeetToMm(unconnectedHeight.AsDouble());
                    double actualBaseOffsetMm = baseOffset == null ? item.BottomRelativeElevationMm.Value : FeetToMm(baseOffset.AsDouble());
                    double actualBottomExtensionMm = bottomExtension == null ? 0.0 : FeetToMm(bottomExtension.AsDouble());
                    double actualTopExtensionMm = topExtension == null ? 0.0 : FeetToMm(topExtension.AsDouble());
                    ElementId actualTopConstraintId = topConstraint == null ? ElementId.InvalidElementId : topConstraint.AsElementId();
                    if (Math.Abs(actualBaseOffsetMm - item.BottomRelativeElevationMm.Value) > 1.0 ||
                        actualTopConstraintId != ElementId.InvalidElementId ||
                        Math.Abs(actualBottomExtensionMm) > 1.0 ||
                        Math.Abs(actualTopExtensionMm) > 1.0)
                    {
                        doc.Delete(wall.Id);
                        throw new InvalidOperationException(
                            "Parapet constraint verification failed: expected unconnected top and base_offset_mm=" +
                            item.BottomRelativeElevationMm.Value.ToString("0.###", CultureInfo.InvariantCulture) +
                            "; actual top_constraint_id=" + ElementIdValue(actualTopConstraintId).ToString(CultureInfo.InvariantCulture) +
                            "; base_offset_mm=" + actualBaseOffsetMm.ToString("0.###", CultureInfo.InvariantCulture) +
                            "; bottom_extension_mm=" + actualBottomExtensionMm.ToString("0.###", CultureInfo.InvariantCulture) +
                            "; top_extension_mm=" + actualTopExtensionMm.ToString("0.###", CultureInfo.InvariantCulture) + "."
                        );
                    }
                    if (Math.Abs(actualHeightMm - item.HeightMm.Value) > 1.0)
                    {
                        doc.Delete(wall.Id);
                        throw new InvalidOperationException(
                            "Parapet height verification failed: requested="
                            + item.HeightMm.Value.ToString("0.###", CultureInfo.InvariantCulture)
                            + " mm; actual=" + actualHeightMm.ToString("0.###", CultureInfo.InvariantCulture) + " mm."
                        );
                    }
                    BoundingBoxXYZ geometryBox = wall.get_BoundingBox(null);
                    if (geometryBox == null)
                    {
                        doc.Delete(wall.Id);
                        throw new InvalidOperationException("Parapet geometry verification failed: no bounding box was available.");
                    }
                    double actualBottomMm = FeetToMm(geometryBox.Min.Z);
                    double actualTopMm = FeetToMm(geometryBox.Max.Z);
                    double actualGeometryHeightMm = actualTopMm - actualBottomMm;
                    double expectedTopMm = resolvedBottomMm + item.HeightMm.Value;
                    if (Math.Abs(actualBottomMm - resolvedBottomMm) > 2.0 ||
                        Math.Abs(actualTopMm - expectedTopMm) > 2.0 ||
                        Math.Abs(actualGeometryHeightMm - item.HeightMm.Value) > 2.0)
                    {
                        doc.Delete(wall.Id);
                        throw new InvalidOperationException(
                            "Parapet geometry verification failed: expected bottom/top/height=" +
                            resolvedBottomMm.ToString("0.###", CultureInfo.InvariantCulture) + "/" +
                            expectedTopMm.ToString("0.###", CultureInfo.InvariantCulture) + "/" +
                            item.HeightMm.Value.ToString("0.###", CultureInfo.InvariantCulture) +
                            " mm; actual=" + actualBottomMm.ToString("0.###", CultureInfo.InvariantCulture) + "/" +
                            actualTopMm.ToString("0.###", CultureInfo.InvariantCulture) + "/" +
                            actualGeometryHeightMm.ToString("0.###", CultureInfo.InvariantCulture) + " mm."
                        );
                    }
                    SetStringParameter(wall, BuiltInParameter.ALL_MODEL_MARK, item.Id);
                    SetStringParameter(
                        wall,
                        BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS,
                        BuildMetadataNote(item)
                        + "; host_roof_id=" + (item.HostRoofId ?? "")
                        + "; exterior_material_source_wall_id=" + (item.ExteriorMaterialSourceWallId ?? "")
                        + "; exterior_material_inheritance_status=" + (item.ExteriorMaterialInheritanceStatus ?? "")
                        + "; cad_start_z_is_evidence_only=true"
                    );
                    created[item.Id] = wall;
                    string heightNote = "requested_height_mm="
                        + item.HeightMm.Value.ToString("0.###", CultureInfo.InvariantCulture)
                        + "; actual_unconnected_height_mm="
                        + actualHeightMm.ToString("0.###", CultureInfo.InvariantCulture)
                        + "; actual_geometry_bottom_mm="
                        + actualBottomMm.ToString("0.###", CultureInfo.InvariantCulture)
                        + "; actual_geometry_top_mm="
                        + actualTopMm.ToString("0.###", CultureInfo.InvariantCulture)
                        + "; actual_geometry_height_mm="
                        + actualGeometryHeightMm.ToString("0.###", CultureInfo.InvariantCulture)
                        + "; top_constraint=unconnected"
                        + "; base_offset_mm="
                        + actualBaseOffsetMm.ToString("0.###", CultureInfo.InvariantCulture)
                        + "; bottom_extension_mm="
                        + actualBottomExtensionMm.ToString("0.###", CultureInfo.InvariantCulture)
                        + "; top_extension_mm="
                        + actualTopExtensionMm.ToString("0.###", CultureInfo.InvariantCulture)
                        + "; removed_existing_same_mark_count="
                        + (removedExistingByMark.TryGetValue(item.Id ?? "", out int removedCount) ? removedCount : 0).ToString(CultureInfo.InvariantCulture)
                        + "; height_verified=true";
                    report.Success(
                        "parapets",
                        item.Id,
                        ElementIdValue(wall.Id),
                        item.ReviewStatus,
                        AppendNote(AppendNote(wallTypeNote, orientationNote), heightNote));
                }
                catch (Exception ex)
                {
                    report.Failure("parapets", item.Id, item.ReviewStatus, ex.Message);
                }
            }
            return created;
        }

        private static Dictionary<string, int> RemoveExistingParapetsWithSameMarks(
            Document doc,
            List<ParapetComponent> parapets)
        {
            HashSet<string> marks = new HashSet<string>(
                (parapets ?? new List<ParapetComponent>())
                    .Where(item => item != null && !string.IsNullOrWhiteSpace(item.Id))
                    .Select(item => item.Id),
                StringComparer.OrdinalIgnoreCase);
            Dictionary<string, int> removed = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            if (marks.Count == 0)
            {
                return removed;
            }

            List<Wall> existingWalls = new FilteredElementCollector(doc)
                .OfCategory(BuiltInCategory.OST_Walls)
                .WhereElementIsNotElementType()
                .Cast<Wall>()
                .ToList();
            foreach (Wall existing in existingWalls)
            {
                Parameter markParameter = existing.get_Parameter(BuiltInParameter.ALL_MODEL_MARK);
                string mark = markParameter == null ? "" : markParameter.AsString() ?? "";
                if (!marks.Contains(mark))
                {
                    continue;
                }
                if (TryDeleteElement(doc, existing.Id))
                {
                    removed[mark] = removed.TryGetValue(mark, out int count) ? count + 1 : 1;
                }
            }
            if (removed.Count > 0)
            {
                doc.Regenerate();
            }
            return removed;
        }

        private static List<WallRun> BuildWallRuns(
            List<WallComponent> walls,
            List<OpeningComponent> openings,
            ModelingReport report)
        {
            const double maxMergeGapMm = 6000;
            // Gaps below the minimum modeled opening width are drafting breaks,
            // not intentional door/window voids. Preserve the Python wall-run contract.
            const double draftingGapToleranceMm = 300;
            const double lineToleranceMm = 50;
            List<WallSegmentProjection> candidates = new List<WallSegmentProjection>();

            foreach (WallComponent wall in walls ?? new List<WallComponent>())
            {
                try
                {
                    if (IsRejected(wall))
                    {
                        throw new InvalidOperationException(GetSkipReason(wall));
                    }
                    if (wall.Start == null || wall.End == null)
                    {
                        throw new InvalidOperationException("Missing wall start or end point.");
                    }
                    if (IsSamePoint(wall.Start, wall.End))
                    {
                        throw new InvalidOperationException("Wall start and end points are identical.");
                    }
                    candidates.Add(WallSegmentProjection.FromWall(wall));
                }
                catch (Exception ex)
                {
                    report.Failure("walls", wall.Id, wall.ReviewStatus, ex.Message);
                }
            }

            List<WallRun> runs = new List<WallRun>();
            foreach (IGrouping<string, WallSegmentProjection> group in candidates.GroupBy(s => s.GroupKey))
            {
                List<WallSegmentProjection> ordered = group.OrderBy(s => s.MinAlong).ToList();
                List<WallSegmentProjection> current = new List<WallSegmentProjection>();
                double currentMax = 0;

                foreach (WallSegmentProjection segment in ordered)
                {
                    if (current.Count == 0)
                    {
                        current.Add(segment);
                        currentMax = segment.MaxAlong;
                        continue;
                    }

                    double gap = segment.MinAlong - currentMax;
                    bool continuousDraftingGap = gap <= draftingGapToleranceMm;
                    bool hostedOpeningGap =
                        gap <= maxMergeGapMm &&
                        HasHostedOpeningInWallGap(current, segment, currentMax, segment.MinAlong, openings);
                    if (continuousDraftingGap || hostedOpeningGap)
                    {
                        current.Add(segment);
                        currentMax = Math.Max(currentMax, segment.MaxAlong);
                    }
                    else
                    {
                        runs.Add(WallRun.FromSegments(current, lineToleranceMm));
                        current = new List<WallSegmentProjection> { segment };
                        currentMax = segment.MaxAlong;
                    }
                }

                if (current.Count > 0)
                {
                    runs.Add(WallRun.FromSegments(current, lineToleranceMm));
                }
            }

            return RecheckWallRuns(runs);
        }

        private static List<WallRun> RecheckWallRuns(List<WallRun> runs)
        {
            List<WallRun> kept = new List<WallRun>();
            foreach (WallRun candidate in (runs ?? new List<WallRun>())
                .OrderByDescending(WallRunLengthMm))
            {
                WallRun duplicateOf = kept.FirstOrDefault(existing => WallRunsShareModelingPosition(existing, candidate));
                if (duplicateOf == null)
                {
                    kept.Add(candidate);
                    continue;
                }

                bool propertyConflict = WallRunsHavePropertyConflict(duplicateOf, candidate);
                foreach (WallComponent item in candidate.Items ?? new List<WallComponent>())
                {
                    if (!duplicateOf.Items.Any(existing => string.Equals(existing.Id, item.Id, StringComparison.OrdinalIgnoreCase)))
                    {
                        duplicateOf.Items.Add(item);
                    }
                }
                duplicateOf.RecheckDuplicateRunCount++;
                if (propertyConflict)
                {
                    duplicateOf.RecheckPropertyConflictCount++;
                }
                duplicateOf.Mark = duplicateOf.Items.First().Id + "_RECHECKED";
            }
            return kept;
        }

        private static double WallRunLengthMm(WallRun run)
        {
            if (run == null || run.Start == null || run.End == null)
            {
                return 0;
            }
            double dx = run.End.X - run.Start.X;
            double dy = run.End.Y - run.Start.Y;
            return Math.Sqrt(dx * dx + dy * dy);
        }

        private static bool WallRunsShareModelingPosition(WallRun a, WallRun b)
        {
            if (a == null || b == null || a.Start == null || a.End == null || b.Start == null || b.End == null)
            {
                return false;
            }
            string levelA = a.Primary == null ? "" : a.Primary.BaseLevel ?? "";
            string levelB = b.Primary == null ? "" : b.Primary.BaseLevel ?? "";
            if (!string.IsNullOrWhiteSpace(levelA) && !string.IsNullOrWhiteSpace(levelB) &&
                !string.Equals(levelA, levelB, StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            string drawingA = a.Primary == null ? "" : a.Primary.DrawingId ?? "";
            string drawingB = b.Primary == null ? "" : b.Primary.DrawingId ?? "";
            if (!string.IsNullOrWhiteSpace(drawingA) && !string.IsNullOrWhiteSpace(drawingB) &&
                !string.Equals(drawingA, drawingB, StringComparison.OrdinalIgnoreCase))
            {
                string floorA = a.Primary == null ? "" : JsonScalarText(a.Primary.FloorNumber);
                string floorB = b.Primary == null ? "" : JsonScalarText(b.Primary.FloorNumber);
                if (string.IsNullOrWhiteSpace(floorA) || string.IsNullOrWhiteSpace(floorB) ||
                    !string.Equals(floorA, floorB, StringComparison.OrdinalIgnoreCase))
                {
                    return false;
                }
            }

            double adx = a.End.X - a.Start.X;
            double ady = a.End.Y - a.Start.Y;
            double bdx = b.End.X - b.Start.X;
            double bdy = b.End.Y - b.Start.Y;
            double aLength = Math.Sqrt(adx * adx + ady * ady);
            double bLength = Math.Sqrt(bdx * bdx + bdy * bdy);
            if (aLength <= 1 || bLength <= 1)
            {
                return false;
            }
            double alignment = Math.Abs((adx * bdx + ady * bdy) / (aLength * bLength));
            if (alignment < 0.99985)
            {
                return false;
            }
            double distance1 = Math.Abs((b.Start.X - a.Start.X) * ady - (b.Start.Y - a.Start.Y) * adx) / aLength;
            double distance2 = Math.Abs((b.End.X - a.Start.X) * ady - (b.End.Y - a.Start.Y) * adx) / aLength;
            if (Math.Max(distance1, distance2) > 20)
            {
                return false;
            }
            double ux = adx / aLength;
            double uy = ady / aLength;
            double b0 = (b.Start.X - a.Start.X) * ux + (b.Start.Y - a.Start.Y) * uy;
            double b1 = (b.End.X - a.Start.X) * ux + (b.End.Y - a.Start.Y) * uy;
            double overlap = Math.Max(0, Math.Min(aLength, Math.Max(b0, b1)) - Math.Max(0, Math.Min(b0, b1)));
            return overlap >= Math.Min(aLength, bLength) * 0.98 - 20;
        }

        private static bool WallRunsHavePropertyConflict(WallRun a, WallRun b)
        {
            WallComponent first = a == null ? null : a.Primary;
            WallComponent second = b == null ? null : b.Primary;
            if (first == null || second == null)
            {
                return false;
            }
            if (first.ThicknessMm.HasValue && second.ThicknessMm.HasValue &&
                Math.Abs(first.ThicknessMm.Value - second.ThicknessMm.Value) > 5)
            {
                return true;
            }
            string materialA = (first.Material ?? "").Trim();
            string materialB = (second.Material ?? "").Trim();
            return !string.IsNullOrWhiteSpace(materialA) &&
                !string.IsNullOrWhiteSpace(materialB) &&
                !string.Equals(materialA, materialB, StringComparison.OrdinalIgnoreCase);
        }

        private static string JsonScalarText(JsonElement value)
        {
            if (value.ValueKind == JsonValueKind.String)
            {
                return value.GetString() ?? "";
            }
            if (value.ValueKind == JsonValueKind.Number ||
                value.ValueKind == JsonValueKind.True ||
                value.ValueKind == JsonValueKind.False)
            {
                return value.ToString();
            }
            return "";
        }

        private static bool HasHostedOpeningInWallGap(
            List<WallSegmentProjection> current,
            WallSegmentProjection next,
            double gapStart,
            double gapEnd,
            List<OpeningComponent> openings)
        {
            if (current == null || current.Count == 0 || next == null || gapEnd <= gapStart)
            {
                return false;
            }

            WallSegmentProjection reference = current.First();
            HashSet<string> adjacentWallIds = new HashSet<string>(
                current.Select(item => item.Wall == null ? "" : item.Wall.Id)
                    .Concat(new[] { next.Wall == null ? "" : next.Wall.Id })
                    .Where(id => !string.IsNullOrWhiteSpace(id)),
                StringComparer.OrdinalIgnoreCase);
            HashSet<string> adjacentLevels = new HashSet<string>(
                current.Select(item => item.Wall == null ? "" : item.Wall.BaseLevel)
                    .Concat(new[] { next.Wall == null ? "" : next.Wall.BaseLevel })
                    .Where(level => !string.IsNullOrWhiteSpace(level)),
                StringComparer.OrdinalIgnoreCase);

            foreach (OpeningComponent opening in openings ?? new List<OpeningComponent>())
            {
                if (opening == null || !opening.WidthMm.HasValue || opening.WidthMm.Value <= 0)
                {
                    continue;
                }
                if (!adjacentWallIds.Contains(opening.HostWallId ?? ""))
                {
                    continue;
                }
                if (adjacentLevels.Count > 0 &&
                    !string.IsNullOrWhiteSpace(opening.Level) &&
                    !adjacentLevels.Contains(opening.Level))
                {
                    continue;
                }

                Point3 axisPoint = ResolveOpeningAxisPoint(opening, reference);
                if (axisPoint == null)
                {
                    continue;
                }
                double along =
                    axisPoint.X * reference.DirectionX +
                    axisPoint.Y * reference.DirectionY;
                double normalOffset =
                    axisPoint.X * reference.NormalX +
                    axisPoint.Y * reference.NormalY;
                double lineDistance = Math.Abs(normalOffset - reference.Offset);
                double lineTolerance = Math.Max(
                    300,
                    (reference.Wall == null ? 0 : reference.Wall.ThicknessMm ?? 0) / 2.0 + 150);
                if (lineDistance > lineTolerance)
                {
                    continue;
                }
                if (along < gapStart - 250 || along > gapEnd + 250)
                {
                    continue;
                }

                double gap = gapEnd - gapStart;
                if (gap <= opening.WidthMm.Value + 700)
                {
                    return true;
                }
            }
            return false;
        }

        private static Point3 ResolveOpeningAxisPoint(
            OpeningComponent opening,
            WallSegmentProjection reference)
        {
            if (opening == null || reference == null)
            {
                return null;
            }
            return new[] { opening.PanelStart, opening.PanelEnd, opening.Location }
                .Where(point => point != null)
                .OrderBy(point => Math.Abs(
                    point.X * reference.NormalX +
                    point.Y * reference.NormalY -
                    reference.Offset))
                .FirstOrDefault();
        }

        private class WallSegmentProjection
        {
            public WallComponent Wall { get; set; }
            public string GroupKey { get; set; }
            public Point3 Origin { get; set; }
            public double DirectionX { get; set; }
            public double DirectionY { get; set; }
            public double NormalX { get; set; }
            public double NormalY { get; set; }
            public double MinAlong { get; set; }
            public double MaxAlong { get; set; }
            public double Offset { get; set; }

            public static WallSegmentProjection FromWall(WallComponent wall)
            {
                double dx = wall.End.X - wall.Start.X;
                double dy = wall.End.Y - wall.Start.Y;
                double length = Math.Sqrt(dx * dx + dy * dy);
                double ux = dx / length;
                double uy = dy / length;

                if (ux < -0.000001 || (Math.Abs(ux) <= 0.000001 && uy < 0))
                {
                    ux = -ux;
                    uy = -uy;
                }

                double nx = -uy;
                double ny = ux;
                double s0 = wall.Start.X * ux + wall.Start.Y * uy;
                double s1 = wall.End.X * ux + wall.End.Y * uy;
                double offset = wall.Start.X * nx + wall.Start.Y * ny;

                return new WallSegmentProjection
                {
                    Wall = wall,
                    Origin = wall.Start,
                    DirectionX = ux,
                    DirectionY = uy,
                    NormalX = nx,
                    NormalY = ny,
                    MinAlong = Math.Min(s0, s1),
                    MaxAlong = Math.Max(s0, s1),
                    Offset = offset,
                    GroupKey = BuildGroupKey(wall, ux, uy, offset)
                };
            }

            private static string BuildGroupKey(WallComponent wall, double ux, double uy, double offset)
            {
                double directionBucketX = Math.Round(ux, 3);
                double directionBucketY = Math.Round(uy, 3);
                double offsetBucket = Math.Round(offset / 50.0) * 50.0;
                double thicknessBucket = wall.ThicknessMm.HasValue ? Math.Round(wall.ThicknessMm.Value / 10.0) * 10.0 : 0;
                return string.Join("|",
                    wall.BaseLevel ?? "",
                    wall.TopLevel ?? "",
                    thicknessBucket.ToString(CultureInfo.InvariantCulture),
                    (wall.Material ?? "").Trim().ToLowerInvariant(),
                    directionBucketX.ToString("0.###", CultureInfo.InvariantCulture),
                    directionBucketY.ToString("0.###", CultureInfo.InvariantCulture),
                    offsetBucket.ToString("0.###", CultureInfo.InvariantCulture));
            }
        }

        private class WallRun
        {
            public List<WallComponent> Items { get; set; }
            public WallComponent Primary { get; set; }
            public Point3 Start { get; set; }
            public Point3 End { get; set; }
            public string Mark { get; set; }
            public int RecheckDuplicateRunCount { get; set; }
            public int RecheckPropertyConflictCount { get; set; }

            public static WallRun FromSegments(List<WallSegmentProjection> segments, double lineToleranceMm)
            {
                WallSegmentProjection first = segments.First();
                double minAlong = segments.Min(s => s.MinAlong);
                double maxAlong = segments.Max(s => s.MaxAlong);
                double offset = segments.Average(s => s.Offset);
                Point3 start = FromProjection(first, minAlong, offset);
                Point3 end = FromProjection(first, maxAlong, offset);
                List<WallComponent> items = segments.Select(s => s.Wall).OrderBy(w => w.Id).ToList();
                return new WallRun
                {
                    Items = items,
                    Primary = items.First(),
                    Start = start,
                    End = end,
                    Mark = items.Count == 1 ? items.First().Id : items.First().Id + "_MERGED"
                };
            }

            private static Point3 FromProjection(WallSegmentProjection basis, double along, double offset)
            {
                return new Point3
                {
                    X = basis.DirectionX * along + basis.NormalX * offset,
                    Y = basis.DirectionY * along + basis.NormalY * offset,
                    Z = basis.Origin.Z
                };
            }
        }

        private static Dictionary<string, FamilyInstance> CreateColumns(Document doc, StandardModel model, Dictionary<string, Level> levels, ModelingReport report)
        {
            Dictionary<string, FamilyInstance> created = new Dictionary<string, FamilyInstance>();
            foreach (ColumnComponent item in model.Components.Columns ?? new List<ColumnComponent>())
            {
                try
                {
                    string defaults = "";
                    if (IsRejected(item))
                    {
                        if (!CanRecoverColumnForModeling(item))
                        {
                            throw new InvalidOperationException(GetSkipReason(item));
                        }
                        defaults = AppendNote(defaults,
                            GetSkipReason(item) + " overridden because this reviewed column has usable level, location, and section data for Revit placement");
                    }
                    if (item.Location == null)
                    {
                        throw new InvalidOperationException("Missing column location.");
                    }
                    if (!levels.TryGetValue(item.Level, out Level baseLevel))
                    {
                        throw new InvalidOperationException("Base level not found: " + item.Level);
                    }

                    string typeNote;
                    defaults = AppendNote(defaults, TryLoadColumnFamilyFile(doc, item));
                    FamilySymbol symbol = FindColumnSymbol(doc, item, out typeNote);
                    if (symbol == null)
                    {
                        DirectShape fallback = CreateColumnFallbackShape(doc, item, baseLevel);
                        SetStringParameter(fallback, BuiltInParameter.ALL_MODEL_MARK, item.Id);
                        SetStringParameter(fallback, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, BuildMetadataNote(item));
                        defaults = AppendNote(defaults, typeNote);
                        defaults = AppendNote(defaults, "no compatible rectangular/round column family; created shape-correct DirectShape column fallback");
                        if (NeedsReview(item))
                        {
                            defaults = AppendNote(defaults, "created although review_status=needs_review; verify manually");
                        }
                        report.Success("columns", item.Id, ElementIdValue(fallback.Id), item.ReviewStatus, defaults);
                        continue;
                    }
                    if (!symbol.IsActive)
                    {
                        symbol.Activate();
                        doc.Regenerate();
                    }

                    FamilyInstance column = doc.Create.NewFamilyInstance(ToXyz(item.Location), symbol, baseLevel, Autodesk.Revit.DB.Structure.StructuralType.Column);
                    SetStringParameter(column, BuiltInParameter.ALL_MODEL_MARK, item.Id);
                    SetStringParameter(column, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, BuildMetadataNote(item));
                    SetLengthParameterByNames(column, item.WidthMm, "Width", "width", "b");
                    SetLengthParameterByNames(column, item.DepthMm, "Depth", "depth", "d");
                    SetLengthParameterByNames(column, item.DiameterMm, "Diameter", "diameter");
                    SetLengthParameter(column, BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM, item.BaseZMm);
                    if (!string.IsNullOrWhiteSpace(item.TopLevel) && levels.TryGetValue(item.TopLevel, out Level topLevel))
                    {
                        SetElementIdParameter(column, BuiltInParameter.FAMILY_TOP_LEVEL_PARAM, topLevel.Id);
                        if (item.TopZMm.HasValue)
                        {
                            SetLengthParameter(column, BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM, item.TopZMm.Value - FeetToMm(topLevel.Elevation));
                        }
                    }
                    SetLengthParameterByNames(column, item.HeightMm, "Height", "Unconnected Height", "Column Height");
                    if (item.RotationAngle.HasValue && Math.Abs(item.RotationAngle.Value) > 0.0001)
                    {
                        Line axis = Line.CreateBound(ToXyz(item.Location), ToXyz(new Point3 { X = item.Location.X, Y = item.Location.Y, Z = item.Location.Z + 1000 }));
                        ElementTransformUtils.RotateElement(doc, column.Id, axis, item.RotationAngle.Value * Math.PI / 180.0);
                    }
                    if (!string.IsNullOrWhiteSpace(item.Material))
                    {
                        SetMaterialParameterByNames(column, FindOrCreateMaterial(doc, item.Material).Id, "Material", "Structural Material");
                    }

                    defaults = AppendNote(defaults, typeNote);
                    if (NeedsReview(item))
                    {
                        defaults = AppendNote(defaults, "created although review_status=needs_review; verify manually");
                    }
                    created[item.Id] = column;
                    report.Success("columns", item.Id, ElementIdValue(column.Id), item.ReviewStatus, defaults);
                }
                catch (Exception ex)
                {
                    report.Failure("columns", item.Id, item.ReviewStatus, ex.Message);
                }
            }
            return created;
        }

        private static DirectShape CreateColumnFallbackShape(Document doc, ColumnComponent item, Level baseLevel)
        {
            double widthMm = PositiveOrDefault(item.WidthMm, 300);
            double depthMm = PositiveOrDefault(item.DepthMm, widthMm);
            double heightMm = item.HeightMm.HasValue && item.HeightMm.Value > 0
                ? item.HeightMm.Value
                : item.TopZMm.HasValue && item.BaseZMm.HasValue
                    ? Math.Abs(item.TopZMm.Value - item.BaseZMm.Value)
                    : 3300;
            double baseElevationMm = FeetToMm(baseLevel.Elevation) + (item.BaseZMm ?? 0) + item.Location.Z;
            Solid solid;
            if (item.DiameterMm.HasValue && item.DiameterMm.Value > 0)
            {
                double radiusMm = item.DiameterMm.Value / 2.0;
                List<Point3> boundary = Enumerable.Range(0, 24)
                    .Select(index =>
                    {
                        double angle = 2.0 * Math.PI * index / 24.0;
                        return new Point3
                        {
                            X = item.Location.X + radiusMm * Math.Cos(angle),
                            Y = item.Location.Y + radiusMm * Math.Sin(angle),
                            Z = 0
                        };
                    })
                    .ToList();
                solid = CreateExtrudedSolidAtElevation(boundary, baseElevationMm, heightMm);
            }
            else
            {
                solid = CreateHorizontalBoxSolid(
                    item.Location.X - widthMm / 2.0,
                    item.Location.Y - depthMm / 2.0,
                    baseElevationMm,
                    item.Location.X + widthMm / 2.0,
                    item.Location.Y + depthMm / 2.0,
                    baseElevationMm + heightMm);
            }

            DirectShape shape = CreateDirectShapeWithFallback(doc, BuiltInCategory.OST_StructuralColumns);
            shape.ApplicationId = "AI Revit Modeling";
            shape.ApplicationDataId = item.Id ?? Guid.NewGuid().ToString();
            shape.SetShape(new List<GeometryObject> { solid });
            try
            {
                shape.Name = string.IsNullOrWhiteSpace(item.Type) ? "AI Column Fallback" : item.Type;
            }
            catch
            {
                // Some Revit categories disallow renaming DirectShape instances.
            }
            if (item.RotationAngle.HasValue && Math.Abs(item.RotationAngle.Value) > 0.0001)
            {
                Line axis = Line.CreateBound(
                    new XYZ(MmToFeet(item.Location.X), MmToFeet(item.Location.Y), MmToFeet(baseElevationMm)),
                    new XYZ(MmToFeet(item.Location.X), MmToFeet(item.Location.Y), MmToFeet(baseElevationMm + 1000)));
                ElementTransformUtils.RotateElement(doc, shape.Id, axis, item.RotationAngle.Value * Math.PI / 180.0);
            }
            return shape;
        }

        private static Dictionary<string, Floor> CreateSlabs(
            Document doc,
            StandardModel model,
            Dictionary<string, Level> levels,
            ModelingReport report,
            out HashSet<string> createdFloorOpeningIds)
        {
            Dictionary<string, Floor> created = new Dictionary<string, Floor>();
            HashSet<string> integratedOpeningIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            createdFloorOpeningIds = integratedOpeningIds;
            foreach (SlabComponent item in model.Components.Slabs ?? new List<SlabComponent>())
            {
                try
                {
                    if (IsRejected(item))
                    {
                        throw new InvalidOperationException(GetSkipReason(item));
                    }
                    if (!levels.TryGetValue(item.Level, out Level level))
                    {
                        throw new InvalidOperationException("Level not found: " + item.Level);
                    }
                    List<CurveLoop> profiles = new List<CurveLoop> { BuildCurveLoop(item.Boundary) };
                    List<FloorOpeningComponent> hostedOpenings = (model.Components.FloorOpenings ?? new List<FloorOpeningComponent>())
                        .Where(opening => !IsRejected(opening) &&
                            string.Equals(opening.HostFloorId, item.Id, StringComparison.OrdinalIgnoreCase) &&
                            BoundaryInsideBoundary(opening.Boundary, item.Boundary))
                        .ToList();
                    foreach (FloorOpeningComponent opening in hostedOpenings)
                    {
                        profiles.Add(BuildCurveLoop(opening.Boundary));
                        integratedOpeningIds.Add(opening.Id);
                    }
                    string typeNote;
                    FloorType floorType = FindFloorType(doc, item, out typeNote);
                    if (floorType == null)
                    {
                        throw new InvalidOperationException("No floor type was found in this Revit project.");
                    }

                    Floor floor = Floor.Create(doc, profiles, floorType.Id, level.Id);
                    SetStringParameter(floor, BuiltInParameter.ALL_MODEL_MARK, item.Id);
                    SetStringParameter(floor, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, BuildMetadataNote(item));
                    double floorOffsetMm = EffectiveFloorOffsetMm(item, level);
                    SetLengthParameter(floor, BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM, floorOffsetMm);

                    string defaults = AppendNote(typeNote, "floor level=" + level.Name + "; level_offset_mm=" + Math.Round(floorOffsetMm).ToString(CultureInfo.InvariantCulture));
                    if (hostedOpenings.Count > 0)
                    {
                        defaults = AppendNote(defaults, "integrated " + hostedOpenings.Count.ToString(CultureInfo.InvariantCulture) + " floor opening(s) into the floor sketch");
                    }
                    if (NeedsReview(item))
                    {
                        defaults = AppendNote(defaults, "created although review_status=needs_review; verify manually");
                    }
                    created[item.Id] = floor;
                    report.Success("slabs", item.Id, ElementIdValue(floor.Id), item.ReviewStatus, defaults);
                    foreach (FloorOpeningComponent opening in hostedOpenings)
                    {
                        report.Success("floor_openings", opening.Id, ElementIdValue(floor.Id), opening.ReviewStatus, "integrated into host floor sketch; no separate Opening element was created");
                    }
                }
                catch (Exception ex)
                {
                    report.Failure("slabs", item.Id, item.ReviewStatus, ex.Message);
                }
            }
            foreach (FloorOpeningComponent opening in model.Components.FloorOpenings ?? new List<FloorOpeningComponent>())
            {
                if (!integratedOpeningIds.Contains(opening.Id))
                {
                    string reason = BuildFloorOpeningSkipReason(opening, model, created);
                    report.Skip("floor_openings", opening.Id, opening.ReviewStatus, reason);
                }
            }
            return created;
        }

        private static string BuildFloorOpeningSkipReason(FloorOpeningComponent opening, StandardModel model, Dictionary<string, Floor> slabs)
        {
            if (opening == null || string.IsNullOrWhiteSpace(opening.HostFloorId))
            {
                return "Missing host_floor_id.";
            }
            SlabComponent hostSlab = (model.Components.Slabs ?? new List<SlabComponent>())
                .FirstOrDefault(slab => string.Equals(slab.Id, opening.HostFloorId, StringComparison.OrdinalIgnoreCase));
            if (hostSlab != null && !BoundaryInsideBoundary(opening.Boundary, hostSlab.Boundary))
            {
                return "Skipped because the opening boundary is outside the host slab boundary; this is probably detail-drawing geometry, not a floor-plan opening.";
            }
            if (IsRejected(opening))
            {
                return GetSkipReason(opening);
            }
            if (slabs == null || !slabs.ContainsKey(opening.HostFloorId))
            {
                return "Host floor was not created: " + opening.HostFloorId;
            }
            return "Opening was not integrated into its host floor.";
        }

        private static void CreateFloorOpenings(Document doc, StandardModel model, Dictionary<string, Floor> slabs, ModelingReport report)
        {
            foreach (FloorOpeningComponent item in model.Components.FloorOpenings ?? new List<FloorOpeningComponent>())
            {
                try
                {
                    if (IsRejected(item))
                    {
                        throw new InvalidOperationException(GetSkipReason(item));
                    }
                    if (string.IsNullOrWhiteSpace(item.HostFloorId))
                    {
                        throw new InvalidOperationException("Missing host_floor_id.");
                    }
                    if (!slabs.TryGetValue(item.HostFloorId, out Floor host))
                    {
                        throw new InvalidOperationException("Host floor not found or was not created: " + item.HostFloorId);
                    }
                    CurveArray profile = BuildCurveArray(item.Boundary);
                    Opening opening = doc.Create.NewOpening(host, profile, true);
                    SetStringParameter(opening, BuiltInParameter.ALL_MODEL_MARK, item.Id);
                    SetStringParameter(opening, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, BuildMetadataNote(item));
                    string defaults = NeedsReview(item) ? "created although review_status=needs_review; verify manually" : "";
                    report.Success("floor_openings", item.Id, ElementIdValue(opening.Id), item.ReviewStatus, defaults);
                }
                catch (Exception ex)
                {
                    report.Failure("floor_openings", item.Id, item.ReviewStatus, ex.Message);
                }
            }
        }

        private static Dictionary<string, ElementId> CreateStairs(
            Document doc,
            StandardModel model,
            Dictionary<string, Level> levels,
            HashSet<string> createdFloorOpeningIds,
            ModelingReport report)
        {
            Dictionary<string, ElementId> created = new Dictionary<string, ElementId>();
            foreach (GenericModelComponent item in model.Components.Stairs ?? new List<GenericModelComponent>())
            {
                try
                {
                    if (IsRejected(item) && item.ManualBuildApproved != true)
                    {
                        throw new InvalidOperationException(GetSkipReason(item));
                    }

                    if (string.IsNullOrWhiteSpace(item.MatchedFloorOpeningId))
                    {
                        throw new InvalidOperationException(
                            "Stair execution blocked: matched_floor_opening_id is missing. "
                            + "The spatial agent must bind the stair detail to one confirmed upper-floor stairwell opening before Revit modeling."
                        );
                    }
                    if (createdFloorOpeningIds == null || !createdFloorOpeningIds.Contains(item.MatchedFloorOpeningId))
                    {
                        throw new InvalidOperationException(
                            "Stair execution blocked: bound floor opening " + item.MatchedFloorOpeningId
                            + " was not created in its host slab. Create and verify the upper-floor opening before creating this stair."
                        );
                    }

                    Level baseLevel = ResolveGenericLevel(FirstNonEmpty(item.StartLevel, item.Level), levels);
                    Level requestedTopLevel = ResolveGenericLevel(item.EndLevel, levels);
                    if (baseLevel == null || requestedTopLevel == null)
                    {
                        throw new InvalidOperationException("Native stair requires valid start_level and end_level.");
                    }
                    if (requestedTopLevel.ProjectElevation <= baseLevel.ProjectElevation)
                    {
                        throw new InvalidOperationException("Native stair end_level must be above start_level.");
                    }

                    int requestedRunCount = ResolveNativeStairRunCount(item);
                    ElementId stairId;
                    string creationNote;
                    if (requestedRunCount == 4)
                    {
                        Level intermediateLevel = FindIntermediateStairLevel(baseLevel, requestedTopLevel, levels);
                        if (intermediateLevel == null)
                        {
                            throw new InvalidOperationException("Four-run multistory stair requires an intermediate level between start_level and end_level.");
                        }
                        stairId = CreateNativeStairAssembly(doc, item, baseLevel, intermediateLevel, 2);
                        ElementId multistoryId = CreateNativeMultistoryStair(doc, stairId, requestedTopLevel);
                        creationNote = "native multistory switchback stair created; 2 runs per story, 4 runs total; multistory_element_id=" + ElementIdValue(multistoryId);
                    }
                    else
                    {
                        stairId = CreateNativeStairAssembly(doc, item, baseLevel, requestedTopLevel, requestedRunCount);
                        creationNote = "native Revit stair created; run_count=" + requestedRunCount.ToString(CultureInfo.InvariantCulture);
                    }
                    creationNote = AppendNote(creationNote, BuildNativeStairVerificationNote(doc, stairId, item));

                    using (Transaction metadataTransaction = new Transaction(doc, "Set AI stair metadata"))
                    {
                        metadataTransaction.Start();
                        Element stairElement = doc.GetElement(stairId);
                        SetStringParameter(stairElement, BuiltInParameter.ALL_MODEL_MARK, item.Id);
                        SetStringParameter(stairElement, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, BuildMetadataNote(item));
                        metadataTransaction.Commit();
                    }

                    string defaults = AppendNote(
                        creationNote,
                        "execution dependency confirmed: floor opening " + item.MatchedFloorOpeningId + " was created first"
                    );
                    defaults = AppendNote(defaults, BuildStairDimensionNote(item));
                    if (IsRejected(item))
                    {
                        defaults = AppendNote(defaults, (GetSkipReason(item) ?? "review-only") + " overridden because manual_build_approved=true");
                    }
                    created[item.Id] = stairId;
                    report.Success("stairs", item.Id, ElementIdValue(stairId), item.ReviewStatus, defaults);
                }
                catch (Exception ex)
                {
                    report.Failure("stairs", item.Id, item.ReviewStatus, ex.Message);
                }
            }
            return created;
        }

        private static void CreateNativeRailings(
            Document doc,
            StandardModel model,
            Dictionary<string, Level> levels,
            ModelingReport report)
        {
            List<GenericModelComponent> items = model.Components.Railings ?? new List<GenericModelComponent>();
            if (items.Count == 0)
            {
                return;
            }

            using (Transaction tx = new Transaction(doc, "Create AI native railings"))
            {
                tx.Start();
                RailingType baseType = new FilteredElementCollector(doc)
                    .OfClass(typeof(RailingType))
                    .Cast<RailingType>()
                    .FirstOrDefault();
                Dictionary<int, RailingType> typesByHeight = new Dictionary<int, RailingType>();
                foreach (GenericModelComponent item in items)
                {
                    try
                    {
                        if (IsRejected(item))
                        {
                            throw new InvalidOperationException(GetSkipReason(item));
                        }
                        if (item.Start == null || item.End == null || !item.HeightMm.HasValue || item.HeightMm.Value <= 0)
                        {
                            throw new InvalidOperationException("Railing requires start, end, and a positive height_mm.");
                        }
                        Level level = ResolveGenericLevel(item.Level, levels);
                        if (level == null)
                        {
                            throw new InvalidOperationException("Railing level not found: " + item.Level);
                        }
                        if (baseType == null)
                        {
                            throw new InvalidOperationException("The Revit project contains no RailingType to use as a native railing template.");
                        }

                        XYZ start = new XYZ(MmToFeet(item.Start.X), MmToFeet(item.Start.Y), level.Elevation);
                        XYZ end = new XYZ(MmToFeet(item.End.X), MmToFeet(item.End.Y), level.Elevation);
                        if (start.DistanceTo(end) <= MmToFeet(1))
                        {
                            throw new InvalidOperationException("Railing path is shorter than 1 mm.");
                        }
                        CurveLoop path = new CurveLoop();
                        path.Append(Line.CreateBound(start, end));
                        int heightKey = (int)Math.Round(item.HeightMm.Value);
                        if (!typesByHeight.TryGetValue(heightKey, out RailingType railingType))
                        {
                            string typeName = "AI Railing " + heightKey.ToString(CultureInfo.InvariantCulture) + "mm";
                            railingType = new FilteredElementCollector(doc)
                                .OfClass(typeof(RailingType))
                                .Cast<RailingType>()
                                .FirstOrDefault(candidate => string.Equals(candidate.Name, typeName, StringComparison.OrdinalIgnoreCase));
                            if (railingType == null)
                            {
                                railingType = baseType.Duplicate(typeName) as RailingType;
                            }
                            if (railingType == null)
                            {
                                throw new InvalidOperationException("Revit could not duplicate a RailingType for height " + heightKey + " mm.");
                            }
                            SetLengthParameterByNamesWithResult(
                                railingType,
                                item.HeightMm,
                                "Height", "Railing Height", "Top Rail Height", "栏杆高度", "扶手高度");
                            typesByHeight[heightKey] = railingType;
                        }
                        Railing railing = Railing.Create(doc, path, railingType.Id, level.Id);
                        if (railing == null)
                        {
                            throw new InvalidOperationException("Revit could not create a native railing from the supplied path.");
                        }

                        bool heightSet = SetLengthParameterByNamesWithResult(
                            railing,
                            item.HeightMm,
                            "Height", "Railing Height", "Top Rail Height", "栏杆高度", "扶手高度");
                        if (!heightSet)
                        {
                            heightSet = SetLengthParameterByNamesWithResult(
                                railingType,
                                item.HeightMm,
                                "Height", "Railing Height", "Top Rail Height", "栏杆高度", "扶手高度");
                        }
                        SetStringParameter(railing, BuiltInParameter.ALL_MODEL_MARK, item.Id);
                        SetStringParameter(railing, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, BuildMetadataNote(item));
                        string note = "native Revit Railing; requested_height_mm="
                            + item.HeightMm.Value.ToString("0.###", CultureInfo.InvariantCulture)
                            + (heightSet ? "; height parameter applied" : "; template controls height; verify template top rail height");
                        report.Success("railings", item.Id, ElementIdValue(railing.Id), item.ReviewStatus, note);
                    }
                    catch (Exception ex)
                    {
                        report.Failure("railings", item.Id, item.ReviewStatus, ex.Message);
                    }
                }
                tx.Commit();
            }
        }

        private static ElementId CreateNativeStairAssembly(Document doc, GenericModelComponent stair, Level baseLevel, Level topLevel, int runCount)
        {
            if (runCount != 1 && runCount != 2)
            {
                throw new InvalidOperationException("Native stair assembly currently supports one or two runs per story.");
            }

                    int requiredTreadsPerRun = ResolveRequiredTreadsPerRun(stair, runCount);
            ElementId stairsId;
            using (StairsEditScope stairsScope = new StairsEditScope(doc, "AI native stair " + (stair.Id ?? "")))
            {
                stairsId = stairsScope.Start(baseLevel.Id, topLevel.Id);
                using (Transaction stairsTransaction = new Transaction(doc, "Create stair runs and landings"))
                {
                    stairsTransaction.Start();
                    Stairs nativeStairs = doc.GetElement(stairsId) as Stairs;
                    if (nativeStairs == null)
                    {
                        throw new InvalidOperationException("Revit did not return the native stair being edited.");
                    }
                    StairsType preferredStairsType = FindPreferredConcreteStairsType(doc);
                    if (preferredStairsType == null)
                    {
                        string availableTypes = string.Join(", ", new FilteredElementCollector(doc)
                            .OfClass(typeof(StairsType))
                            .Cast<StairsType>()
                            .Select(type => type.Name)
                            .Where(name => !string.IsNullOrWhiteSpace(name))
                            .OrderBy(name => name, StringComparer.OrdinalIgnoreCase));
                        throw new InvalidOperationException(
                            "A compatible native concrete stair type was not found. "
                            + "Expected English type 'Precast Stair' or a recognized Chinese/English cast-in-place type. "
                            + "Available StairsType names: " + (string.IsNullOrWhiteSpace(availableTypes) ? "(none)" : availableTypes) + "."
                        );
                    }
                    nativeStairs.ChangeTypeId(preferredStairsType.Id);
                    double widthMm = PositiveOrDefault(stair.WidthMm, 1100);
                    double runLengthMm = ResolveNativeStairRunLengthMm(stair, runCount);
                    // Contract: landing length is the longitudinal depth; landing width spans both runs.
                    double landingLengthMm = ResolveNativeLandingLengthMm(stair, widthMm, stair.StairwellWidthMm);
                    double landingWidthMm = ResolveNativeLandingWidthMm(stair, widthMm, stair.StairwellWidthMm);
                    List<StairRunContract> explicitRuns = ResolveNativeStairRunContracts(stair, runCount);
                    bool useExplicitRunGeometry = explicitRuns.Count == runCount;
                    if (stair.TreadDepthMm.HasValue && stair.TreadDepthMm.Value > 0)
                    {
                        nativeStairs.ActualTreadDepth = MmToFeet(stair.TreadDepthMm.Value);
                    }
                    XYZ direction = useExplicitRunGeometry
                        ? ResolveStairRunContractDirection(explicitRuns[0])
                        : ResolveNativeStairDirection(stair);
                    XYZ perpendicular = new XYZ(-direction.Y, direction.X, 0);
                    XYZ origin = useExplicitRunGeometry
                        ? Point3ToPlanXyz(explicitRuns[0].LocationLine.Start)
                        : ResolveNativeStairOrigin(stair, widthMm);
                    double stairwellWidthMm = PositiveOrDefault(stair.StairwellWidthMm, 100);
                    if (useExplicitRunGeometry)
                    {
                        ValidateNativeStairRunContracts(stair, explicitRuns, widthMm);
                    }
                    else
                    {
                        ValidateNativeStairSideClearance(stair, direction, origin, widthMm, stairwellWidthMm, runCount);
                    }
                    double laneOffsetFeet = MmToFeet(widthMm + stairwellWidthMm);
                    double runLengthFeet = MmToFeet(runLengthMm);
                    double runWidthFeet = MmToFeet(widthMm);

                    StairsRun previousRun = null;
                    XYZ previousRunEnd = null;
                    XYZ previousRunDirection = null;
                    for (int index = 0; index < runCount; index++)
                    {
                        // StairsRun.TopElevation is relative to the stair base. Revit expects
                        // the straight-run location path at an absolute project elevation.
                        double elevation = baseLevel.ProjectElevation +
                            (previousRun == null ? 0.0 : previousRun.TopElevation);
                        XYZ start;
                        XYZ end;
                        double currentRunWidthFeet = runWidthFeet;
                        if (useExplicitRunGeometry)
                        {
                            StairRunContract runContract = explicitRuns[index];
                            start = Point3ToPlanXyz(runContract.LocationLine.Start, elevation);
                            end = Point3ToPlanXyz(runContract.LocationLine.End, elevation);
                            currentRunWidthFeet = MmToFeet(PositiveOrDefault(runContract.RunWidthMm, widthMm));
                        }
                        else
                        {
                            XYZ laneOrigin = origin + perpendicular.Multiply(index % 2 == 0 ? 0 : laneOffsetFeet);
                            start = index % 2 == 0
                                ? new XYZ(laneOrigin.X, laneOrigin.Y, elevation)
                                : new XYZ(laneOrigin.X + direction.X * runLengthFeet, laneOrigin.Y + direction.Y * runLengthFeet, elevation);
                            end = index % 2 == 0
                                ? new XYZ(laneOrigin.X + direction.X * runLengthFeet, laneOrigin.Y + direction.Y * runLengthFeet, elevation)
                                : new XYZ(laneOrigin.X, laneOrigin.Y, elevation);
                        }
                        XYZ currentRunDirection = (end - start).Normalize();
                        StairsRun currentRun;
                        try
                        {
                            currentRun = StairsRun.CreateStraightRun(doc, stairsId, Line.CreateBound(start, end), StairsRunJustification.Center);
                        }
                        catch (Exception ex)
                        {
                            throw new InvalidOperationException(
                                "Failed to create stair run " + (index + 1).ToString(CultureInfo.InvariantCulture)
                                + " for " + (stair.Id ?? "STAIR")
                                + "; base_project_elevation_mm=" + FeetToMm(baseLevel.ProjectElevation).ToString("0.###", CultureInfo.InvariantCulture)
                                + "; top_project_elevation_mm=" + FeetToMm(topLevel.ProjectElevation).ToString("0.###", CultureInfo.InvariantCulture)
                                + "; path_z_mm=" + FeetToMm(elevation).ToString("0.###", CultureInfo.InvariantCulture)
                                + "; path_length_mm=" + FeetToMm(start.DistanceTo(end)).ToString("0.###", CultureInfo.InvariantCulture)
                                + ". " + ex.Message,
                                ex
                            );
                        }
                        currentRun.ActualRunWidth = currentRunWidthFeet;
                        doc.Regenerate();
                        if (currentRun.ActualTreadsNumber != requiredTreadsPerRun)
                        {
                            throw new InvalidOperationException(
                                "Stair tread-count mismatch on run " + (index + 1).ToString(CultureInfo.InvariantCulture)
                                + ": required_treads=" + requiredTreadsPerRun.ToString(CultureInfo.InvariantCulture)
                                + "; actual_treads=" + currentRun.ActualTreadsNumber.ToString(CultureInfo.InvariantCulture)
                                + ". The stair was not committed."
                            );
                        }

                        if (previousRun != null)
                        {
                            CreateNativeSwitchbackLanding(
                                doc,
                                stairsId,
                                stair,
                                previousRun,
                                currentRun,
                                previousRunEnd,
                                start,
                                previousRunDirection,
                                Math.Max(previousRun.ActualRunWidth, currentRun.ActualRunWidth),
                                MmToFeet(landingLengthMm),
                                MmToFeet(landingWidthMm)
                            );
                        }
                        previousRun = currentRun;
                        previousRunEnd = end;
                        previousRunDirection = currentRunDirection;
                    }
                    doc.Regenerate();
                    int requiredTotalTreads = requiredTreadsPerRun * runCount;
                    if (nativeStairs.ActualTreadsNumber != requiredTotalTreads)
                    {
                        throw new InvalidOperationException(
                            "Stair total tread-count mismatch: required_treads=" + requiredTotalTreads.ToString(CultureInfo.InvariantCulture)
                            + "; actual_treads=" + nativeStairs.ActualTreadsNumber.ToString(CultureInfo.InvariantCulture)
                            + ". The stair was not committed."
                        );
                    }
                    stairsTransaction.Commit();
                }
                stairsScope.Commit(new StairFailurePreprocessor());
            }
            return stairsId;
        }

        private static List<StairRunContract> ResolveNativeStairRunContracts(GenericModelComponent stair, int runCount)
        {
            List<StairRunContract> runs = (stair.StairRuns ?? new List<StairRunContract>())
                .Where(run =>
                    run != null &&
                    run.LocationLine != null &&
                    run.LocationLine.Start != null &&
                    run.LocationLine.End != null &&
                    PlanDistanceMm(run.LocationLine.Start, run.LocationLine.End) > 1.0)
                .OrderBy(run => run.Sequence ?? int.MaxValue)
                .Take(runCount)
                .ToList();
            return runs.Count == runCount ? runs : new List<StairRunContract>();
        }

        private static XYZ ResolveStairRunContractDirection(StairRunContract run)
        {
            Point3 start = run.LocationLine.Start;
            Point3 end = run.LocationLine.End;
            double dx = end.X - start.X;
            double dy = end.Y - start.Y;
            double length = Math.Sqrt(dx * dx + dy * dy);
            if (length <= 1.0)
            {
                throw new InvalidOperationException("Explicit stair run location_line has no usable plan length.");
            }
            return new XYZ(dx / length, dy / length, 0);
        }

        private static XYZ Point3ToPlanXyz(Point3 point, double elevationFeet = 0.0)
        {
            return new XYZ(MmToFeet(point.X), MmToFeet(point.Y), elevationFeet);
        }

        private static double PlanDistanceMm(Point3 first, Point3 second)
        {
            double dx = second.X - first.X;
            double dy = second.Y - first.Y;
            return Math.Sqrt(dx * dx + dy * dy);
        }

        private static void ValidateNativeStairRunContracts(
            GenericModelComponent stair,
            List<StairRunContract> runs,
            double defaultWidthMm)
        {
            StairBox box = StairBox.FromBoundary(NormalizeBoundary(stair.Boundary));
            if (box == null)
            {
                return;
            }

            foreach (StairRunContract run in runs)
            {
                Point3 start = run.LocationLine.Start;
                Point3 end = run.LocationLine.End;
                double dx = end.X - start.X;
                double dy = end.Y - start.Y;
                double length = Math.Sqrt(dx * dx + dy * dy);
                double nx = -dy / length;
                double ny = dx / length;
                double halfWidthMm = PositiveOrDefault(run.RunWidthMm, defaultWidthMm) / 2.0;
                foreach (Point3 point in new[] { start, end })
                {
                    foreach (double sign in new[] { -1.0, 1.0 })
                    {
                        double x = point.X + nx * halfWidthMm * sign;
                        double y = point.Y + ny * halfWidthMm * sign;
                        if (x < box.MinX - 1.0 || x > box.MaxX + 1.0 || y < box.MinY - 1.0 || y > box.MaxY + 1.0)
                        {
                            throw new InvalidOperationException(
                                "Explicit stair run location_line exceeds the verified stair boundary."
                            );
                        }
                    }
                }
            }
        }

        private static double ResolveNativeLandingLengthMm(GenericModelComponent stair, double runWidthMm, double? stairwellWidthMm)
        {
            return PositiveOrDefault(stair.LandingLengthMm, runWidthMm);
        }

        private static double ResolveNativeLandingWidthMm(GenericModelComponent stair, double runWidthMm, double? stairwellWidthMm)
        {
            double connectedRunsSpanMm = runWidthMm * 2.0 + PositiveOrDefault(stairwellWidthMm, 0);
            return Math.Max(connectedRunsSpanMm, PositiveOrDefault(stair.LandingWidthMm, connectedRunsSpanMm));
        }

        private static StairsLanding CreateNativeSwitchbackLanding(
            Document doc,
            ElementId stairsId,
            GenericModelComponent stair,
            StairsRun previousRun,
            StairsRun currentRun,
            XYZ previousRunEnd,
            XYZ currentRunStart,
            XYZ firstRunDirection,
            double runWidthFeet,
            double landingDepthFeet,
            double landingWidthFeet)
        {
            if (previousRun == null || currentRun == null || previousRunEnd == null || currentRunStart == null)
            {
                throw new InvalidOperationException("Cannot create the stair landing because one of the adjacent run endpoints is missing.");
            }

            XYZ acrossRuns = currentRunStart - previousRunEnd;
            if (acrossRuns.GetLength() <= MmToFeet(1))
            {
                throw new InvalidOperationException("Cannot create the stair landing because the two run endpoints have no usable separation.");
            }

            XYZ acrossDirection = acrossRuns.Normalize();
            double safeLengthFeet = Math.Max(MmToFeet(1), landingDepthFeet);
            XYZ connectedEdgeStart = FlattenToStairBase(previousRunEnd - acrossDirection.Multiply(runWidthFeet / 2.0));
            XYZ connectedEdgeEnd = FlattenToStairBase(currentRunStart + acrossDirection.Multiply(runWidthFeet / 2.0));
            double connectedWidthFeet = connectedEdgeStart.DistanceTo(connectedEdgeEnd);
            double effectiveLandingWidthFeet = Math.Max(connectedWidthFeet, landingWidthFeet);
            CurveLoop boundary = BuildCadAlignedLandingBoundary(
                stair,
                firstRunDirection,
                safeLengthFeet,
                effectiveLandingWidthFeet
            );
            if (boundary == null)
            {
                XYZ landingCenter = (connectedEdgeStart + connectedEdgeEnd) * 0.5;
                XYZ firstEdgeStart = FlattenToStairBase(landingCenter - acrossDirection.Multiply(effectiveLandingWidthFeet / 2.0));
                XYZ firstEdgeEnd = FlattenToStairBase(landingCenter + acrossDirection.Multiply(effectiveLandingWidthFeet / 2.0));
                XYZ farEdgeEnd = FlattenToStairBase(firstEdgeEnd + firstRunDirection.Multiply(safeLengthFeet));
                XYZ farEdgeStart = FlattenToStairBase(firstEdgeStart + firstRunDirection.Multiply(safeLengthFeet));
                boundary = CreateRectangularCurveLoop(firstEdgeStart, firstEdgeEnd, farEdgeEnd, farEdgeStart);
            }
            StairsLanding landing = StairsLanding.CreateSketchedLanding(doc, stairsId, boundary, previousRun.TopElevation);
            if (landing == null)
            {
                throw new InvalidOperationException("Revit could not create the required sketched stair landing.");
            }
            doc.Regenerate();
            double[] actualDimensionsMm = MeasureLandingFootprintMm(landing, firstRunDirection);
            stair.CreatedLandingLengthMm = actualDimensionsMm[0];
            stair.CreatedLandingWidthMm = actualDimensionsMm[1];
            const double landingToleranceMm = 5.0;
            if (Math.Abs(actualDimensionsMm[0] - FeetToMm(safeLengthFeet)) > landingToleranceMm
                || Math.Abs(actualDimensionsMm[1] - FeetToMm(effectiveLandingWidthFeet)) > landingToleranceMm)
            {
                throw new InvalidOperationException(
                    "Created stair landing footprint does not match the CAD platform dimensions: "
                    + "required=" + FeetToMm(effectiveLandingWidthFeet).ToString("0.###", CultureInfo.InvariantCulture)
                    + "x" + FeetToMm(safeLengthFeet).ToString("0.###", CultureInfo.InvariantCulture)
                    + " mm; actual=" + actualDimensionsMm[1].ToString("0.###", CultureInfo.InvariantCulture)
                    + "x" + actualDimensionsMm[0].ToString("0.###", CultureInfo.InvariantCulture) + " mm."
                );
            }
            return landing;
        }

        private static CurveLoop BuildCadAlignedLandingBoundary(
            GenericModelComponent stair,
            XYZ firstRunDirection,
            double landingDepthFeet,
            double landingWidthFeet)
        {
            List<Point3> sourceBoundary = NormalizeBoundary(stair == null ? null : stair.Boundary);
            if (sourceBoundary.Count < 3)
            {
                return null;
            }
            XYZ along = FlattenToStairBase(firstRunDirection).Normalize();
            XYZ across = new XYZ(-along.Y, along.X, 0);
            List<XYZ> points = sourceBoundary
                .Select(point => new XYZ(MmToFeet(point.X), MmToFeet(point.Y), 0))
                .ToList();
            double minAlong = points.Min(point => point.DotProduct(along));
            double maxAlong = points.Max(point => point.DotProduct(along));
            double minAcross = points.Min(point => point.DotProduct(across));
            double maxAcross = points.Max(point => point.DotProduct(across));
            double availableDepthFeet = maxAlong - minAlong;
            double availableWidthFeet = maxAcross - minAcross;
            if (landingDepthFeet > availableDepthFeet + MmToFeet(5)
                || landingWidthFeet > availableWidthFeet + MmToFeet(5))
            {
                throw new InvalidOperationException(
                    "CAD platform dimensions exceed the verified stair opening boundary: required="
                    + FeetToMm(landingWidthFeet).ToString("0.###", CultureInfo.InvariantCulture)
                    + "x" + FeetToMm(landingDepthFeet).ToString("0.###", CultureInfo.InvariantCulture)
                    + " mm; available=" + FeetToMm(availableWidthFeet).ToString("0.###", CultureInfo.InvariantCulture)
                    + "x" + FeetToMm(availableDepthFeet).ToString("0.###", CultureInfo.InvariantCulture) + " mm."
                );
            }

            double farAlong = maxAlong;
            double nearAlong = farAlong - landingDepthFeet;
            double centerAcross = (minAcross + maxAcross) / 2.0;
            double lowAcross = centerAcross - landingWidthFeet / 2.0;
            double highAcross = centerAcross + landingWidthFeet / 2.0;
            XYZ nearLow = along.Multiply(nearAlong) + across.Multiply(lowAcross);
            XYZ nearHigh = along.Multiply(nearAlong) + across.Multiply(highAcross);
            XYZ farHigh = along.Multiply(farAlong) + across.Multiply(highAcross);
            XYZ farLow = along.Multiply(farAlong) + across.Multiply(lowAcross);
            return CreateRectangularCurveLoop(nearLow, nearHigh, farHigh, farLow);
        }

        private static CurveLoop CreateRectangularCurveLoop(XYZ first, XYZ second, XYZ third, XYZ fourth)
        {
            CurveLoop boundary = new CurveLoop();
            boundary.Append(Line.CreateBound(first, second));
            boundary.Append(Line.CreateBound(second, third));
            boundary.Append(Line.CreateBound(third, fourth));
            boundary.Append(Line.CreateBound(fourth, first));
            return boundary;
        }

        private static double[] MeasureLandingFootprintMm(StairsLanding landing, XYZ firstRunDirection)
        {
            CurveLoop footprint = landing.GetFootprintBoundary();
            List<XYZ> points = footprint
                .SelectMany(curve => new[] { curve.GetEndPoint(0), curve.GetEndPoint(1) })
                .ToList();
            if (points.Count < 4)
            {
                throw new InvalidOperationException("Revit returned no usable footprint boundary for the created stair landing.");
            }
            XYZ along = FlattenToStairBase(firstRunDirection).Normalize();
            XYZ across = new XYZ(-along.Y, along.X, 0);
            double depthFeet = points.Max(point => point.DotProduct(along)) - points.Min(point => point.DotProduct(along));
            double widthFeet = points.Max(point => point.DotProduct(across)) - points.Min(point => point.DotProduct(across));
            return new[] { FeetToMm(depthFeet), FeetToMm(widthFeet) };
        }

        private static XYZ FlattenToStairBase(XYZ point)
        {
            return new XYZ(point.X, point.Y, 0);
        }

        private static ElementId CreateNativeMultistoryStair(Document doc, ElementId baseStairId, Level topLevel)
        {
            using (Transaction transaction = new Transaction(doc, "Create multistory stair"))
            {
                transaction.Start();
                Stairs stairs = doc.GetElement(baseStairId) as Stairs;
                if (stairs == null)
                {
                    throw new InvalidOperationException("Base stair could not be resolved for multistory creation.");
                }
                MultistoryStairs multistory = MultistoryStairs.Create(stairs);
                if (!multistory.CanConnectLevel(topLevel.Id))
                {
                    throw new InvalidOperationException("The requested top level cannot be connected to the multistory stair.");
                }
                multistory.ConnectLevels(new HashSet<ElementId> { topLevel.Id });
                transaction.Commit();
                return multistory.Id;
            }
        }

        private static int ResolveNativeStairRunCount(GenericModelComponent stair)
        {
            int count = (int)Math.Round(PositiveOrDefault(stair.RunCount, IsDoubleRunStair(stair) ? 2 : 1));
            if (count != 1 && count != 2 && count != 4)
            {
                throw new InvalidOperationException("Native stair run_count must be 1, 2, or 4.");
            }
            return count;
        }

        private static double ResolveNativeStairRunLengthMm(GenericModelComponent stair, int runCount)
        {
            if (stair.TreadDepthMm.HasValue && stair.TreadDepthMm.Value > 0)
            {
                return Math.Max(1000, ResolveRequiredTreadsPerRun(stair, runCount) * stair.TreadDepthMm.Value);
            }
            if (stair.RunLengthMm.HasValue && stair.RunLengthMm.Value > 0)
            {
                return stair.RunLengthMm.Value;
            }
            if (stair.TotalRunMm.HasValue && stair.TotalRunMm.Value > 0)
            {
                return Math.Max(1000, stair.TotalRunMm.Value / Math.Max(1, runCount));
            }
            return runCount == 1 ? 4800 : 2400;
        }

        private static int ResolveRequiredTreadsPerRun(GenericModelComponent stair, int runCount)
        {
            if (stair.TreadsPerRun.HasValue && stair.TreadsPerRun.Value > 0)
            {
                int perRun = (int)Math.Round(stair.TreadsPerRun.Value);
                if (Math.Abs(stair.TreadsPerRun.Value - perRun) > 0.001)
                {
                    throw new InvalidOperationException("treads_per_run must be an integer.");
                }
                if (stair.NumberOfTreads.HasValue)
                {
                    int total = (int)Math.Round(stair.NumberOfTreads.Value);
                    if (Math.Abs(stair.NumberOfTreads.Value - total) > 0.001 || total != perRun * runCount)
                    {
                        throw new InvalidOperationException(
                            "number_of_treads must equal treads_per_run multiplied by run_count."
                        );
                    }
                }
                return perRun;
            }
            if (stair.NumberOfTreads.HasValue && stair.NumberOfTreads.Value > 0)
            {
                int total = (int)Math.Round(stair.NumberOfTreads.Value);
                if (Math.Abs(stair.NumberOfTreads.Value - total) > 0.001 || total % runCount != 0)
                {
                    throw new InvalidOperationException("number_of_treads must divide evenly across stair runs.");
                }
                return total / runCount;
            }
            if (stair.RisersPerRun.HasValue && stair.RisersPerRun.Value >= 2)
            {
                return Math.Max(1, (int)Math.Round(stair.RisersPerRun.Value) - 1);
            }
            throw new InvalidOperationException("Missing required treads_per_run or number_of_treads for native stair creation.");
        }

        private static StairsType FindPreferredConcreteStairsType(Document doc)
        {
            string[] preferredNames = {
                "precast stair", "precast",
                "现场浇筑楼梯", "现场浇筑", "现浇楼梯",
                "整体浇筑楼梯", "整体浇筑", "整体式楼梯", "整体式",
                "cast-in-place", "cast in place", "castinplace", "monolithic"
            };
            return new FilteredElementCollector(doc)
                .OfClass(typeof(StairsType))
                .Cast<StairsType>()
                .OrderBy(type =>
                {
                    string name = (type.Name ?? "").Trim().ToLowerInvariant();
                    for (int index = 0; index < preferredNames.Length; index++)
                    {
                        if (name.Contains(preferredNames[index].ToLowerInvariant())) return index;
                    }
                    return int.MaxValue;
                })
                .FirstOrDefault(type =>
                {
                    string name = (type.Name ?? "").Trim().ToLowerInvariant();
                    return preferredNames.Any(candidate => name.Contains(candidate.ToLowerInvariant()));
                });
        }

        private static string BuildNativeStairVerificationNote(Document doc, ElementId stairId, GenericModelComponent stair)
        {
            Stairs nativeStairs = doc.GetElement(stairId) as Stairs;
            if (nativeStairs == null)
            {
                return "native stair verification unavailable";
            }
            Element type = doc.GetElement(nativeStairs.GetTypeId());
            int requiredTreads = ResolveRequiredTreadsPerRun(stair, ResolveNativeStairRunCount(stair)) * ResolveNativeStairRunCount(stair);
            return "stair_type=" + (type == null ? "现场浇筑楼梯" : type.Name)
                + "; required_treads=" + requiredTreads.ToString(CultureInfo.InvariantCulture)
                + "; actual_treads=" + nativeStairs.ActualTreadsNumber.ToString(CultureInfo.InvariantCulture)
                + "; run_geometry_source="
                + ((stair.StairRuns != null && stair.StairRuns.Count >= ResolveNativeStairRunCount(stair))
                    ? "stair_runs.location_line"
                    : "revit_fallback_reconstruction");
        }

        private static XYZ ResolveNativeStairDirection(GenericModelComponent stair)
        {
            if (stair.Start != null && stair.End != null)
            {
                double dx = stair.End.X - stair.Start.X;
                double dy = stair.End.Y - stair.Start.Y;
                double length = Math.Sqrt(dx * dx + dy * dy);
                if (length > 1)
                {
                    return new XYZ(dx / length, dy / length, 0);
                }
            }
            string direction = (stair.Direction ?? "").Trim().ToLowerInvariant();
            if (ContainsAny(direction, "north", "+y", "北")) return new XYZ(0, 1, 0);
            if (ContainsAny(direction, "south", "-y", "南")) return new XYZ(0, -1, 0);
            if (ContainsAny(direction, "west", "-x", "西")) return new XYZ(-1, 0, 0);
            return new XYZ(1, 0, 0);
        }

        private static XYZ ResolveNativeStairOrigin(GenericModelComponent stair, double widthMm)
        {
            Point3 source = stair.Start ?? stair.Location;
            StairBox box = StairBox.FromBoundary(NormalizeBoundary(stair.Boundary));
            if (source != null)
            {
                if (box != null && !box.ContainsPlanPoint(source.X, source.Y, widthMm))
                {
                    throw new InvalidOperationException(
                        "Stair start/location is outside its resolved floor-plan boundary. Correct the stair placement in the spatial-agent JSON."
                    );
                }
                return new XYZ(MmToFeet(source.X), MmToFeet(source.Y), 0);
            }
            if (box != null)
            {
                return new XYZ(MmToFeet(box.MinX), MmToFeet(box.MinY + widthMm / 2.0), 0);
            }
            return XYZ.Zero;
        }

        private static void ValidateNativeStairSideClearance(
            GenericModelComponent stair,
            XYZ direction,
            XYZ origin,
            double widthMm,
            double stairwellWidthMm,
            int runCount)
        {
            if (runCount != 2)
            {
                return;
            }
            StairBox box = StairBox.FromBoundary(NormalizeBoundary(stair.Boundary));
            if (box == null)
            {
                return;
            }

            double requiredClearanceMm = PositiveOrDefault(stair.StairWallClearanceMm, 0);
            bool runsAlongY = Math.Abs(direction.Y) >= Math.Abs(direction.X);
            double perpendicularSign = runsAlongY ? -Math.Sign(direction.Y) : Math.Sign(direction.X);
            if (Math.Abs(perpendicularSign) < 0.5)
            {
                perpendicularSign = 1;
            }
            double firstCenterMm = runsAlongY ? FeetToMm(origin.X) : FeetToMm(origin.Y);
            double secondCenterMm = firstCenterMm + perpendicularSign * (widthMm + stairwellWidthMm);
            double occupiedMin = Math.Min(firstCenterMm, secondCenterMm) - widthMm / 2.0;
            double occupiedMax = Math.Max(firstCenterMm, secondCenterMm) + widthMm / 2.0;
            double clearMin = (runsAlongY ? box.MinX : box.MinY) + requiredClearanceMm;
            double clearMax = (runsAlongY ? box.MaxX : box.MaxY) - requiredClearanceMm;
            if (occupiedMin < clearMin - 0.5 || occupiedMax > clearMax + 0.5)
            {
                throw new InvalidOperationException(
                    "Stair side-clearance validation failed: the two runs would exceed the verified stairwell boundary. "
                    + "occupied_cross_range_mm=" + occupiedMin.ToString("0.###", CultureInfo.InvariantCulture)
                    + ".." + occupiedMax.ToString("0.###", CultureInfo.InvariantCulture)
                    + "; allowed_cross_range_mm=" + clearMin.ToString("0.###", CultureInfo.InvariantCulture)
                    + ".." + clearMax.ToString("0.###", CultureInfo.InvariantCulture)
                    + "; stairwell_width_mm=" + stairwellWidthMm.ToString("0.###", CultureInfo.InvariantCulture)
                    + ". Regenerate the JSON with spatial-agent V2.14 or later."
                );
            }
        }

        private static Level FindIntermediateStairLevel(Level baseLevel, Level topLevel, Dictionary<string, Level> levels)
        {
            return (levels ?? new Dictionary<string, Level>()).Values
                .Where(level => level != null && level.ProjectElevation > baseLevel.ProjectElevation + 0.001 && level.ProjectElevation < topLevel.ProjectElevation - 0.001)
                .OrderBy(level => level.ProjectElevation)
                .FirstOrDefault();
        }

        private sealed class StairFailurePreprocessor : IFailuresPreprocessor
        {
            public FailureProcessingResult PreprocessFailures(FailuresAccessor failuresAccessor)
            {
                return FailureProcessingResult.Continue;
            }
        }

        private static List<GeometryObject> BuildStairDirectShapeGeometry(GenericModelComponent stair, double baseElevationMm, double heightMm)
        {
            List<GeometryObject> geometry = new List<GeometryObject>();
            List<Point3> boundary = NormalizeBoundary(stair.Boundary);
            if (boundary.Count < 3)
            {
                throw new InvalidOperationException("Stair boundary needs at least three distinct points.");
            }

            if (IsDoubleRunStair(stair))
            {
                geometry.AddRange(BuildMultiRunStairMasses(stair, boundary, baseElevationMm, heightMm));
            }

            geometry = geometry.Where(item => item != null).ToList();
            if (geometry.Count == 0)
            {
                geometry.Add(CreateExtrudedSolidAtElevation(boundary, baseElevationMm, Math.Max(150, heightMm)));
            }

            return geometry;
        }

        private static bool IsDoubleRunStair(GenericModelComponent stair)
        {
            string text = ((stair.Type ?? "") + " " + (stair.StairType ?? "")).ToLowerInvariant();
            return ContainsAny(text, "double", "two", "双跑") || (stair.RunCount.HasValue && stair.RunCount.Value >= 2);
        }

        private static List<GeometryObject> BuildMultiRunStairMasses(GenericModelComponent stair, List<Point3> boundary, double baseElevationMm, double heightMm)
        {
            StairBox box = StairBox.FromBoundary(boundary);
            if (box == null)
            {
                return new List<GeometryObject>();
            }

            bool runAlongX = IsStairRunAlongX(stair, box);
            double stairWidthMm = PositiveOrDefault(stair.WidthMm, runAlongX ? box.DepthMm : box.WidthMm);
            double landingLengthMm = ResolveNativeLandingLengthMm(stair, stairWidthMm, stair.StairwellWidthMm);
            int runCount = Math.Max(2, (int)Math.Round(PositiveOrDefault(stair.RunCount, 2)));
            if (runCount % 2 != 0)
            {
                runCount++;
            }
            double runLengthMm = PositiveOrDefault(stair.RunLengthMm, Math.Max(300, (runAlongX ? box.WidthMm : box.DepthMm) - landingLengthMm));
            double waistThicknessMm = 150;
            double risePerRunMm = Math.Max(1, heightMm / runCount);

            List<GeometryObject> geometry = new List<GeometryObject>();
            for (int index = 0; index < runCount; index++)
            {
                bool reverse = index % 2 == 1;
                double runBaseMm = baseElevationMm + index * risePerRunMm;
                double runTopMm = runBaseMm + risePerRunMm;
                if (runAlongX)
                {
                    double y1 = reverse ? box.MaxY - stairWidthMm : box.MinY;
                    double y2 = y1 + stairWidthMm;
                    geometry.Add(CreateDirectedSlopedBoxSolid(box.MinX, y1, box.MinX + runLengthMm, y2, runBaseMm, runTopMm, true, !reverse, waistThicknessMm));
                }
                else
                {
                    double x1 = reverse ? box.MaxX - stairWidthMm : box.MinX;
                    double x2 = x1 + stairWidthMm;
                    geometry.Add(CreateDirectedSlopedBoxSolid(x1, box.MinY, x2, box.MinY + runLengthMm, runBaseMm, runTopMm, false, !reverse, waistThicknessMm));
                }

                if (index < runCount - 1)
                {
                    double landingElevationMm = runTopMm;
                    if (runAlongX)
                    {
                        double landingCenterX = reverse ? box.MinX : box.MinX + runLengthMm;
                        geometry.Add(CreateHorizontalBoxSolid(landingCenterX - landingLengthMm / 2.0, box.MinY, landingElevationMm - waistThicknessMm / 2.0, landingCenterX + landingLengthMm / 2.0, box.MaxY, landingElevationMm + waistThicknessMm / 2.0));
                    }
                    else
                    {
                        double landingCenterY = reverse ? box.MinY : box.MinY + runLengthMm;
                        geometry.Add(CreateHorizontalBoxSolid(box.MinX, landingCenterY - landingLengthMm / 2.0, landingElevationMm - waistThicknessMm / 2.0, box.MaxX, landingCenterY + landingLengthMm / 2.0, landingElevationMm + waistThicknessMm / 2.0));
                    }
                }
            }

            return geometry;
        }

        private static Solid CreateDirectedSlopedBoxSolid(
            double x1Mm,
            double y1Mm,
            double x2Mm,
            double y2Mm,
            double startElevationMm,
            double endElevationMm,
            bool runAlongX,
            bool risesTowardPositiveAxis,
            double thicknessMm)
        {
            double minX = Math.Min(x1Mm, x2Mm);
            double maxX = Math.Max(x1Mm, x2Mm);
            double minY = Math.Min(y1Mm, y2Mm);
            double maxY = Math.Max(y1Mm, y2Mm);
            double lowSideTop = risesTowardPositiveAxis ? startElevationMm : endElevationMm;
            double highSideTop = risesTowardPositiveAxis ? endElevationMm : startElevationMm;
            Func<double, double> topAt = coordinate => runAlongX
                ? (Math.Abs(coordinate - minX) < 0.001 ? lowSideTop : highSideTop)
                : (Math.Abs(coordinate - minY) < 0.001 ? lowSideTop : highSideTop);
            double z00 = topAt(runAlongX ? minX : minY);
            double z10 = topAt(runAlongX ? maxX : minY);
            double z11 = topAt(runAlongX ? maxX : maxY);
            double z01 = topAt(runAlongX ? minX : maxY);

            List<XYZ> points = new List<XYZ>
            {
                new XYZ(MmToFeet(minX), MmToFeet(minY), MmToFeet(z00 - thicknessMm)),
                new XYZ(MmToFeet(maxX), MmToFeet(minY), MmToFeet(z10 - thicknessMm)),
                new XYZ(MmToFeet(maxX), MmToFeet(maxY), MmToFeet(z11 - thicknessMm)),
                new XYZ(MmToFeet(minX), MmToFeet(maxY), MmToFeet(z01 - thicknessMm)),
                new XYZ(MmToFeet(minX), MmToFeet(minY), MmToFeet(z00)),
                new XYZ(MmToFeet(maxX), MmToFeet(minY), MmToFeet(z10)),
                new XYZ(MmToFeet(maxX), MmToFeet(maxY), MmToFeet(z11)),
                new XYZ(MmToFeet(minX), MmToFeet(maxY), MmToFeet(z01))
            };
            List<List<int>> faces = new List<List<int>>
            {
                new List<int> { 0, 1, 2, 3 }, new List<int> { 4, 7, 6, 5 },
                new List<int> { 0, 4, 5, 1 }, new List<int> { 1, 5, 6, 2 },
                new List<int> { 2, 6, 7, 3 }, new List<int> { 3, 7, 4, 0 }
            };
            return CreateTessellatedSolid(points, faces);
        }

        private static bool IsStairRunAlongX(GenericModelComponent stair, StairBox box)
        {
            if (stair.Start != null && stair.End != null)
            {
                double dx = Math.Abs(stair.End.X - stair.Start.X);
                double dy = Math.Abs(stair.End.Y - stair.Start.Y);
                if (Math.Max(dx, dy) > 1)
                {
                    return dx >= dy;
                }
            }
            string direction = (stair.Direction ?? "").Trim().ToLowerInvariant();
            if (ContainsAny(direction, "east", "west", "x", "横"))
            {
                return true;
            }
            if (ContainsAny(direction, "north", "south", "y", "竖"))
            {
                return false;
            }
            return box.WidthMm >= box.DepthMm;
        }

        private static Solid CreateHorizontalBoxSolid(double x1Mm, double y1Mm, double z1Mm, double x2Mm, double y2Mm, double z2Mm)
        {
            List<Point3> boundary = new List<Point3>
            {
                new Point3 { X = Math.Min(x1Mm, x2Mm), Y = Math.Min(y1Mm, y2Mm), Z = 0 },
                new Point3 { X = Math.Max(x1Mm, x2Mm), Y = Math.Min(y1Mm, y2Mm), Z = 0 },
                new Point3 { X = Math.Max(x1Mm, x2Mm), Y = Math.Max(y1Mm, y2Mm), Z = 0 },
                new Point3 { X = Math.Min(x1Mm, x2Mm), Y = Math.Max(y1Mm, y2Mm), Z = 0 }
            };
            return CreateExtrudedSolidAtElevation(boundary, Math.Min(z1Mm, z2Mm), Math.Max(1, Math.Abs(z2Mm - z1Mm)));
        }

        private static Solid CreateSlopedBoxSolid(double x1Mm, double y1Mm, double z1Mm, double x2Mm, double y2Mm, double z2Mm, double thicknessMm)
        {
            double minX = Math.Min(x1Mm, x2Mm);
            double maxX = Math.Max(x1Mm, x2Mm);
            double minY = Math.Min(y1Mm, y2Mm);
            double maxY = Math.Max(y1Mm, y2Mm);
            double lowZ = Math.Min(z1Mm, z2Mm);
            double highZ = Math.Max(z1Mm, z2Mm);
            double bottomLowZ = lowZ - thicknessMm;
            double bottomHighZ = highZ - thicknessMm;

            List<XYZ> points = new List<XYZ>
            {
                new XYZ(MmToFeet(minX), MmToFeet(minY), MmToFeet(bottomLowZ)),
                new XYZ(MmToFeet(maxX), MmToFeet(minY), MmToFeet(bottomHighZ)),
                new XYZ(MmToFeet(maxX), MmToFeet(maxY), MmToFeet(bottomHighZ)),
                new XYZ(MmToFeet(minX), MmToFeet(maxY), MmToFeet(bottomLowZ)),
                new XYZ(MmToFeet(minX), MmToFeet(minY), MmToFeet(lowZ)),
                new XYZ(MmToFeet(maxX), MmToFeet(minY), MmToFeet(highZ)),
                new XYZ(MmToFeet(maxX), MmToFeet(maxY), MmToFeet(highZ)),
                new XYZ(MmToFeet(minX), MmToFeet(maxY), MmToFeet(lowZ))
            };

            List<List<int>> faces = new List<List<int>>
            {
                new List<int> { 0, 1, 2, 3 },
                new List<int> { 4, 7, 6, 5 },
                new List<int> { 0, 4, 5, 1 },
                new List<int> { 1, 5, 6, 2 },
                new List<int> { 2, 6, 7, 3 },
                new List<int> { 3, 7, 4, 0 }
            };
            return CreateTessellatedSolid(points, faces);
        }

        private static Solid CreateTessellatedSolid(List<XYZ> points, List<List<int>> faces)
        {
            TessellatedShapeBuilder builder = new TessellatedShapeBuilder();
            builder.OpenConnectedFaceSet(true);
            foreach (List<int> face in faces)
            {
                builder.AddFace(new TessellatedFace(face.Select(index => points[index]).ToList(), ElementId.InvalidElementId));
            }
            builder.CloseConnectedFaceSet();
            builder.Target = TessellatedShapeBuilderTarget.Solid;
            builder.Fallback = TessellatedShapeBuilderFallback.Abort;
            builder.Build();
            return builder.GetBuildResult().GetGeometricalObjects().OfType<Solid>().FirstOrDefault();
        }

        private static string BuildStairDimensionNote(GenericModelComponent stair)
        {
            List<string> parts = new List<string>();
            if (!string.IsNullOrWhiteSpace(stair.StairType)) parts.Add("stair_type=" + stair.StairType);
            if (stair.RunCount.HasValue) parts.Add("run_count=" + stair.RunCount.Value.ToString("0.##", CultureInfo.InvariantCulture));
            if (stair.RiserHeightMm.HasValue) parts.Add("riser_height_mm=" + stair.RiserHeightMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
            if (stair.TreadDepthMm.HasValue) parts.Add("tread_depth_mm=" + stair.TreadDepthMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
            if (stair.NumberOfRisers.HasValue) parts.Add("number_of_risers=" + stair.NumberOfRisers.Value.ToString("0.##", CultureInfo.InvariantCulture));
            if (stair.NumberOfTreads.HasValue) parts.Add("number_of_treads=" + stair.NumberOfTreads.Value.ToString("0.##", CultureInfo.InvariantCulture));
            if (stair.RunLengthMm.HasValue) parts.Add("run_length_mm=" + stair.RunLengthMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
            if (stair.WidthMm.HasValue) parts.Add("width_mm=" + stair.WidthMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
            if (stair.WidthMm.HasValue)
            {
                parts.Add("landing_length_mm=" + ResolveNativeLandingLengthMm(stair, stair.WidthMm.Value, stair.StairwellWidthMm).ToString("0.##", CultureInfo.InvariantCulture));
                parts.Add("landing_width_mm=" + ResolveNativeLandingWidthMm(stair, stair.WidthMm.Value, stair.StairwellWidthMm).ToString("0.##", CultureInfo.InvariantCulture));
            }
            if (stair.CreatedLandingLengthMm.HasValue) parts.Add("created_landing_length_mm=" + stair.CreatedLandingLengthMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
            if (stair.CreatedLandingWidthMm.HasValue) parts.Add("created_landing_width_mm=" + stair.CreatedLandingWidthMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
            if (stair.StairwellWidthMm.HasValue) parts.Add("stairwell_width_mm=" + stair.StairwellWidthMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
            if (stair.StairWallClearanceMm.HasValue) parts.Add("stair_wall_clearance_mm=" + stair.StairWallClearanceMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
            return string.Join("; ", parts);
        }

        private static double EffectiveFloorOffsetMm(SlabComponent item, Level level)
        {
            if (item == null || !item.ElevationMm.HasValue)
            {
                return 0;
            }
            double elevationMm = item.ElevationMm.Value;
            if (Math.Abs(elevationMm) <= 1000)
            {
                return elevationMm;
            }
            double levelElevationMm = level == null ? 0 : FeetToMm(level.Elevation);
            double relativeOffsetMm = elevationMm - levelElevationMm;
            if (Math.Abs(relativeOffsetMm) <= 1000)
            {
                return relativeOffsetMm;
            }
            return elevationMm;
        }

        private static void CreateOpenings(Document doc, List<OpeningComponent> items, BuiltInCategory category, Dictionary<string, Wall> walls, Dictionary<string, Level> levels, ModelingReport report)
        {
            string group = category == BuiltInCategory.OST_Doors ? "doors" : "windows";
            List<OpeningComponent> openingItems = items ?? new List<OpeningComponent>();
            HashSet<long> rejectedPlacementSymbolIds = new HashSet<long>();

            // Load the complete batch first so a malformed preferred family can fall back to
            // another compatible wall-hosted family even when it appears later in the input.
            foreach (OpeningComponent familyItem in openingItems
                .Where(value => !string.IsNullOrWhiteSpace(value.FamilyFile))
                .GroupBy(value => value.FamilyFile, StringComparer.OrdinalIgnoreCase)
                .Select(grouped => grouped.First()))
            {
                TryLoadOpeningFamilyFile(doc, familyItem);
            }

            foreach (OpeningComponent item in openingItems)
            {
                try
                {
                    string defaults = "";
                    if (IsRejected(item))
                    {
                        if (!CanRecoverOpeningForModeling(item, category))
                        {
                            throw new InvalidOperationException(GetSkipReason(item));
                        }
                        defaults = AppendNote(defaults, GetSkipReason(item) + " overridden because this reviewed opening has enough host/location/size data for Revit placement");
                    }
                    if (item.Location == null)
                    {
                        throw new InvalidOperationException("Missing opening location.");
                    }
                    if (string.IsNullOrWhiteSpace(item.HostWallId))
                    {
                        throw new InvalidOperationException("Missing host_wall_id.");
                    }
                    if (!item.WidthMm.HasValue || item.WidthMm.Value <= 0)
                    {
                        throw new InvalidOperationException("Missing or invalid width_mm.");
                    }
                    double? heightMm = EffectiveOpeningHeightMm(item);
                    if (!heightMm.HasValue || heightMm.Value <= 0)
                    {
                        if (category == BuiltInCategory.OST_Doors && CanRecoverOpeningForModeling(item, category))
                        {
                            heightMm = 2100;
                            defaults = AppendNote(defaults, "height_mm defaulted to 2100 because the door had enough host/location/width data");
                        }
                        else
                        {
                            throw new InvalidOperationException("Missing or invalid height_mm.");
                        }
                    }
                    double? sillHeightMm = EffectiveSillHeightMm(item);
                    if ((!sillHeightMm.HasValue || sillHeightMm.Value < 0) && category == BuiltInCategory.OST_Windows)
                    {
                        sillHeightMm = 900;
                        defaults = AppendNote(defaults, "sill_height_mm defaulted to 900");
                    }
                    defaults = AppendNote(defaults, TryLoadOpeningFamilyFile(doc, item));
                    string familyMatchNote;
                    string typeHint = FirstNonEmpty(item.FamilyType, item.Type, item.FamilyName);
                    FamilySymbol symbol = FindFamilySymbol(doc, category, typeHint, item.WidthMm, heightMm, item.FamilyFile, item.FamilyName, out familyMatchNote);
                    if (symbol == null)
                    {
                        throw new InvalidOperationException("No loaded " + group + " family symbol was found.");
                    }
                    if (!walls.TryGetValue(item.HostWallId, out Wall host))
                    {
                        throw new InvalidOperationException("Host wall not found: " + item.HostWallId);
                    }
                    Point3 placement = BuildOpeningPlacementPoint(item, category, sillHeightMm, levels);
                    Point3 hostedPlacement = ProjectOpeningPointToHostWall(placement, host);
                    if (!IsSamePoint(placement, hostedPlacement))
                    {
                        defaults = AppendNote(defaults,
                            "opening XY projected to host wall centerline from (" +
                            Math.Round(placement.X).ToString(CultureInfo.InvariantCulture) + "," +
                            Math.Round(placement.Y).ToString(CultureInfo.InvariantCulture) + ") to (" +
                            Math.Round(hostedPlacement.X).ToString(CultureInfo.InvariantCulture) + "," +
                            Math.Round(hostedPlacement.Y).ToString(CultureInfo.InvariantCulture) + ")");
                    }
                    Level openingLevel = ResolveOpeningLevel(item, levels);
                    placement = hostedPlacement;
                    List<FamilySymbol> symbolCandidates = BuildOpeningPlacementCandidates(
                        doc, category, typeHint, item.WidthMm, heightMm, symbol, item.FamilyFile, item.FamilyName);
                    FamilyInstance instance = null;
                    string selectedOrientationNote = "";
                    List<string> rejectedFamilyNotes = new List<string>();
                    foreach (FamilySymbol candidate in symbolCandidates)
                    {
                        if (rejectedPlacementSymbolIds.Contains(ElementIdValue(candidate.Id)))
                        {
                            rejectedFamilyNotes.Add(
                                candidate.FamilyName + " / " + candidate.Name +
                                ": skipped because this symbol already failed geometry alignment in the current batch");
                            continue;
                        }
                        FamilyInstance candidateInstance = null;
                        try
                        {
                            if (!candidate.IsActive)
                            {
                                candidate.Activate();
                                doc.Regenerate();
                            }
                            // This overload creates the wall-hosted family's native opening cut.
                            candidateInstance = doc.Create.NewFamilyInstance(
                                ToXyz(placement),
                                candidate,
                                host,
                                Autodesk.Revit.DB.Structure.StructuralType.NonStructural);
                            SetLengthParameterByNames(candidateInstance, item.WidthMm, "Width", "width", "宽度");
                            SetLengthParameterByNames(candidateInstance, heightMm, "Height", "height", "高度");
                            SetLengthParameterByNames(candidateInstance, sillHeightMm, "Sill Height", "Default Sill Height", "窗台高度");
                            if (category == BuiltInCategory.OST_Windows)
                            {
                                SetLengthParameter(candidateInstance, BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM, sillHeightMm);
                            }
                            string candidateOrientationNote = category == BuiltInCategory.OST_Doors
                                ? ApplyDoorSwingOrientation(doc, candidateInstance, host, item)
                                : "";
                            doc.Regenerate();
                            if (OpeningGeometryCoversPlacement(
                                doc, candidateInstance, host, placement, item.WidthMm, out string geometryNote))
                            {
                                instance = candidateInstance;
                                selectedOrientationNote = candidateOrientationNote;
                                if (candidate.Id != symbol.Id)
                                {
                                    familyMatchNote = AppendNote(
                                        familyMatchNote,
                                        "fallback family used after placement validation: " +
                                        candidate.FamilyName + " / " + candidate.Name);
                                }
                                break;
                            }

                            rejectedFamilyNotes.Add(candidate.FamilyName + " / " + candidate.Name + ": " + geometryNote);
                            rejectedPlacementSymbolIds.Add(ElementIdValue(candidate.Id));
                            doc.Delete(candidateInstance.Id);
                            doc.Regenerate();
                        }
                        catch (Exception candidateError)
                        {
                            if (candidateInstance != null && doc.GetElement(candidateInstance.Id) != null)
                            {
                                doc.Delete(candidateInstance.Id);
                                doc.Regenerate();
                            }
                            rejectedFamilyNotes.Add(candidate.FamilyName + " / " + candidate.Name + ": " + candidateError.Message);
                        }
                    }
                    if (instance == null)
                    {
                        throw new InvalidOperationException(
                            "No trustworthy wall-hosted " + group + " family could be placed at the requested opening. " +
                            string.Join(" | ", rejectedFamilyNotes));
                    }
                    if (openingLevel != null)
                    {
                        SetElementIdParameter(instance, BuiltInParameter.FAMILY_LEVEL_PARAM, openingLevel.Id);
                        defaults = AppendNote(defaults, "opening level set to " + openingLevel.Name);
                    }
                    defaults = AppendNote(defaults, "opening placement_z_mm=" + Math.Round(placement.Z).ToString(CultureInfo.InvariantCulture));
                    defaults = AppendNote(defaults, selectedOrientationNote);
                    SetStringParameter(instance, BuiltInParameter.ALL_MODEL_MARK, item.Id);
                    SetStringParameter(instance, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, BuildMetadataNote(item));
                    if (rejectedFamilyNotes.Count > 0)
                    {
                        defaults = AppendNote(
                            defaults,
                            "rejected offset family geometry: " + string.Join(" | ", rejectedFamilyNotes));
                    }
                    defaults = AppendNote(defaults, "opening visible geometry alignment validated");
                    defaults = AppendNote(defaults, VerifyOpeningHostInsertion(doc, host, instance));
                    defaults = AppendNote(defaults, familyMatchNote);
                    if (NeedsReview(item))
                    {
                        defaults = AppendNote(defaults, "created although review_status=needs_review; verify manually");
                    }
                    report.Success(group, item.Id, ElementIdValue(instance.Id), item.ReviewStatus, defaults);
                }
                catch (Exception ex)
                {
                    report.Failure(group, item.Id, item.ReviewStatus, ex.Message);
                }
            }
        }

        private static CurveLoop BuildCurveLoop(List<Point3> boundary)
        {
            List<Point3> points = NormalizeBoundary(boundary);
            if (points.Count < 3)
            {
                throw new InvalidOperationException("Boundary needs at least three distinct points.");
            }

            CurveLoop loop = new CurveLoop();
            for (int i = 0; i < points.Count; i++)
            {
                Point3 start = points[i];
                Point3 end = points[(i + 1) % points.Count];
                if (!IsSamePoint(start, end))
                {
                    loop.Append(Line.CreateBound(ToXyz(start), ToXyz(end)));
                }
            }
            return loop;
        }

        private static CurveArray BuildCurveArray(List<Point3> boundary)
        {
            List<Point3> points = NormalizeBoundary(boundary);
            if (points.Count < 3)
            {
                throw new InvalidOperationException("Opening boundary needs at least three distinct points.");
            }

            CurveArray curves = new CurveArray();
            for (int i = 0; i < points.Count; i++)
            {
                Point3 start = points[i];
                Point3 end = points[(i + 1) % points.Count];
                if (!IsSamePoint(start, end))
                {
                    curves.Append(Line.CreateBound(ToXyz(start), ToXyz(end)));
                }
            }
            return curves;
        }

        private static List<Point3> NormalizeBoundary(List<Point3> boundary)
        {
            if (boundary == null)
            {
                return new List<Point3>();
            }

            List<Point3> points = boundary
                .Where(point => point != null)
                .Select(point => new Point3 { X = point.X, Y = point.Y, Z = point.Z })
                .ToList();
            if (points.Count > 1 && IsSamePoint(points.First(), points.Last()))
            {
                points.RemoveAt(points.Count - 1);
            }
            return points;
        }

        private static Solid CreateExtrudedSolidAtElevation(List<Point3> boundary, double baseElevationMm, double heightMm)
        {
            List<Point3> points = NormalizeBoundary(boundary);
            if (points.Count < 3)
            {
                throw new InvalidOperationException("Boundary needs at least three distinct points.");
            }

            CurveLoop loop = new CurveLoop();
            for (int i = 0; i < points.Count; i++)
            {
                Point3 start = points[i];
                Point3 end = points[(i + 1) % points.Count];
                XYZ startXyz = new XYZ(MmToFeet(start.X), MmToFeet(start.Y), MmToFeet(baseElevationMm));
                XYZ endXyz = new XYZ(MmToFeet(end.X), MmToFeet(end.Y), MmToFeet(baseElevationMm));
                if (startXyz.DistanceTo(endXyz) > MmToFeet(1))
                {
                    loop.Append(Line.CreateBound(startXyz, endXyz));
                }
            }
            return GeometryCreationUtilities.CreateExtrusionGeometry(new List<CurveLoop> { loop }, XYZ.BasisZ, MmToFeet(Math.Max(1, heightMm)));
        }

        private static bool BoundaryInsideBoundary(List<Point3> innerBoundary, List<Point3> outerBoundary)
        {
            List<Point3> inner = NormalizeBoundary(innerBoundary);
            List<Point3> outer = NormalizeBoundary(outerBoundary);
            if (inner.Count < 3 || outer.Count < 3)
            {
                return false;
            }
            foreach (Point3 point in inner)
            {
                if (!PointInsidePolygon2D(point, outer))
                {
                    return false;
                }
            }
            return true;
        }

        private static bool PointInsidePolygon2D(Point3 point, List<Point3> polygon)
        {
            bool inside = false;
            int count = polygon.Count;
            for (int i = 0, j = count - 1; i < count; j = i++)
            {
                Point3 a = polygon[i];
                Point3 b = polygon[j];
                if (PointOnBoundarySegment2D(point, a, b))
                {
                    return true;
                }
                bool crosses = ((a.Y > point.Y) != (b.Y > point.Y)) &&
                    (point.X < (b.X - a.X) * (point.Y - a.Y) / ((b.Y - a.Y) == 0 ? 0.000001 : (b.Y - a.Y)) + a.X);
                if (crosses)
                {
                    inside = !inside;
                }
            }
            return inside;
        }

        private static bool PointOnBoundarySegment2D(Point3 point, Point3 start, Point3 end)
        {
            const double tolerance = 1.0;
            double cross = (point.Y - start.Y) * (end.X - start.X) - (point.X - start.X) * (end.Y - start.Y);
            if (Math.Abs(cross) > tolerance)
            {
                return false;
            }
            double dot = (point.X - start.X) * (end.X - start.X) + (point.Y - start.Y) * (end.Y - start.Y);
            if (dot < -tolerance)
            {
                return false;
            }
            double lengthSquared = (end.X - start.X) * (end.X - start.X) + (end.Y - start.Y) * (end.Y - start.Y);
            return dot <= lengthSquared + tolerance;
        }

        private static FamilySymbol FindColumnSymbol(Document doc, ColumnComponent column, out string note)
        {
            note = "";
            List<FamilySymbol> symbols = new FilteredElementCollector(doc)
                .OfCategory(BuiltInCategory.OST_StructuralColumns)
                .OfClass(typeof(FamilySymbol))
                .Cast<FamilySymbol>()
                .ToList();
            if (symbols.Count == 0)
            {
                note = "no loaded column family symbol";
                return null;
            }

            List<FamilySymbol> preferredSymbols = GetPreferredColumnSymbols(symbols, column);
            preferredSymbols = FilterColumnSymbolsByShape(preferredSymbols, column);
            if (preferredSymbols.Count > 0)
            {
                FamilySymbol preferred = FindOrCreateColumnSymbolFromList(preferredSymbols, column, out string preferredNote);
                if (preferred != null)
                {
                    note = AppendNote("column family_file matched " + Path.GetFileName(column.FamilyFile), preferredNote);
                    return preferred;
                }
            }

            if (!string.IsNullOrWhiteSpace(column.FamilyFile))
            {
                note = "specified column family_file did not load as a compatible column family; refusing arbitrary loaded-column fallback";
                return null;
            }

            List<FamilySymbol> compatibleSymbols = FilterColumnSymbolsByShape(symbols, column);
            if (compatibleSymbols.Count == 0)
            {
                note = "no loaded column family matches requested section shape " + RequestedColumnShape(column);
                return null;
            }

            FamilySymbol matched = compatibleSymbols.FirstOrDefault(s => string.Equals(s.Name, column.Type, StringComparison.OrdinalIgnoreCase));
            if (matched != null)
            {
                return matched;
            }

            string generatedName = BuildGeneratedColumnTypeName(column);
            FamilySymbol existing = compatibleSymbols.FirstOrDefault(s => string.Equals(s.Name, generatedName, StringComparison.OrdinalIgnoreCase));
            if (existing != null)
            {
                note = "reused generated column type " + generatedName;
                return existing;
            }

            FamilySymbol template = compatibleSymbols.FirstOrDefault(s => CanSetColumnSize(s, column)) ?? compatibleSymbols.FirstOrDefault();
            try
            {
                FamilySymbol generated = template.Duplicate(generatedName) as FamilySymbol;
                if (generated == null)
                {
                    note = "could not duplicate column family type; fallback to first loaded column";
                    return template;
                }

                bool sizeSet = ApplyColumnSymbolSize(generated, column);
                note = sizeSet
                    ? "generated column type " + generatedName + " from loaded template"
                    : "generated column type " + generatedName + " but size parameters were not fully writable";
                return generated;
            }
            catch (Exception ex)
            {
                note = "could not generate column type " + generatedName + ": " + ex.Message;
                return template;
            }
        }

        private static FamilySymbol FindOrCreateColumnSymbolFromList(List<FamilySymbol> symbols, ColumnComponent column, out string note)
        {
            note = "";
            if (symbols == null || symbols.Count == 0)
            {
                return null;
            }
            FamilySymbol matched = symbols.FirstOrDefault(s =>
                string.Equals(s.Name, column.FamilyType, StringComparison.OrdinalIgnoreCase) ||
                string.Equals(s.Name, column.Type, StringComparison.OrdinalIgnoreCase));
            if (matched != null)
            {
                note = "column symbol matched from explicit family";
                return matched;
            }

            string generatedName = BuildGeneratedColumnTypeName(column);
            FamilySymbol existing = symbols.FirstOrDefault(s => string.Equals(s.Name, generatedName, StringComparison.OrdinalIgnoreCase));
            if (existing != null)
            {
                note = "reused generated column type " + generatedName + " from explicit family";
                return existing;
            }

            FamilySymbol template = symbols.FirstOrDefault(s => CanSetColumnSize(s, column)) ?? symbols.FirstOrDefault();
            try
            {
                FamilySymbol generated = template.Duplicate(generatedName) as FamilySymbol;
                if (generated == null)
                {
                    note = "could not duplicate explicit column family type; fallback to first symbol in that family";
                    return template;
                }
                bool sizeSet = ApplyColumnSymbolSize(generated, column);
                note = sizeSet
                    ? "generated column type " + generatedName + " from explicit family"
                    : "generated column type " + generatedName + " from explicit family but size parameters were not fully writable";
                return generated;
            }
            catch (Exception ex)
            {
                note = "could not generate explicit column type " + generatedName + ": " + ex.Message;
                return template;
            }
        }

        private static List<FamilySymbol> GetPreferredColumnSymbols(List<FamilySymbol> symbols, ColumnComponent column)
        {
            List<string> familyNames = new List<string>();
            if (!string.IsNullOrWhiteSpace(column.FamilyName))
            {
                familyNames.Add(column.FamilyName);
            }
            if (!string.IsNullOrWhiteSpace(column.FamilyFile))
            {
                familyNames.Add(Path.GetFileNameWithoutExtension(column.FamilyFile));
            }
            familyNames = familyNames
                .Where(value => !string.IsNullOrWhiteSpace(value))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToList();
            if (familyNames.Count == 0)
            {
                return new List<FamilySymbol>();
            }
            return symbols
                .Where(symbol => familyNames.Any(name => string.Equals(symbol.FamilyName, name, StringComparison.OrdinalIgnoreCase)))
                .ToList();
        }

        private static List<FamilySymbol> FilterColumnSymbolsByShape(List<FamilySymbol> symbols, ColumnComponent column)
        {
            string requestedShape = RequestedColumnShape(column);
            if (string.IsNullOrWhiteSpace(requestedShape))
            {
                return symbols ?? new List<FamilySymbol>();
            }
            return (symbols ?? new List<FamilySymbol>())
                .Where(symbol => string.Equals(
                    ColumnShapeFromText((symbol.FamilyName ?? "") + " " + (symbol.Name ?? "")),
                    requestedShape,
                    StringComparison.OrdinalIgnoreCase))
                .ToList();
        }

        private static string RequestedColumnShape(ColumnComponent column)
        {
            if (column == null)
            {
                return "";
            }
            if (column.DiameterMm.HasValue && column.DiameterMm.Value > 0)
            {
                return "round";
            }
            string explicitShape = ColumnShapeFromText((column.Type ?? "") + " " + (column.Material ?? ""));
            if (!string.IsNullOrWhiteSpace(explicitShape))
            {
                return explicitShape;
            }
            return column.WidthMm.HasValue && column.DepthMm.HasValue ? "rectangular" : "";
        }

        private static string ColumnShapeFromText(string text)
        {
            text = (text ?? "").ToLowerInvariant().Replace('_', ' ').Replace('-', ' ');
            if (ContainsAny(text, "h section", "h shaped", "h steel", "i section", "i shaped", "wide flange", "h型", "i型", "工字", "型钢")) return "steel_section";
            if (ContainsAny(text, "chamfer", "bevel", "octagonal", "倒角", "八边")) return "chamfered";
            if (ContainsAny(text, "round", "circular", "circle", "圆柱", "圆形截面")) return "round";
            if (ContainsAny(text, "rectangular", "rectangle", "square", "方柱", "矩形柱", "方形", "直角截面")) return "rectangular";
            return "";
        }

        private static string BuildGeneratedColumnTypeName(ColumnComponent column)
        {
            if (column.DiameterMm.HasValue && column.DiameterMm.Value > 0)
            {
                return "AI Column D" + Math.Round(column.DiameterMm.Value).ToString(CultureInfo.InvariantCulture);
            }
            double width = column.WidthMm ?? 300;
            double depth = column.DepthMm ?? width;
            return "AI Column " +
                Math.Round(width).ToString(CultureInfo.InvariantCulture) + "x" +
                Math.Round(depth).ToString(CultureInfo.InvariantCulture);
        }

        private static bool CanSetColumnSize(FamilySymbol symbol, ColumnComponent column)
        {
            if (column.DiameterMm.HasValue && column.DiameterMm.Value > 0)
            {
                return CanSetLengthParameterByNames(symbol, "Diameter", "diameter", "D");
            }
            return CanSetLengthParameterByNames(symbol, "Width", "width", "b") &&
                CanSetLengthParameterByNames(symbol, "Depth", "depth", "d");
        }

        private static bool ApplyColumnSymbolSize(FamilySymbol symbol, ColumnComponent column)
        {
            if (column.DiameterMm.HasValue && column.DiameterMm.Value > 0)
            {
                return SetLengthParameterByNamesWithResult(symbol, column.DiameterMm, "Diameter", "diameter", "D");
            }
            bool widthSet = SetLengthParameterByNamesWithResult(symbol, column.WidthMm, "Width", "width", "b");
            bool depthSet = SetLengthParameterByNamesWithResult(symbol, column.DepthMm ?? column.WidthMm, "Depth", "depth", "d");
            return widthSet && depthSet;
        }

        private static FloorType FindFloorType(Document doc, SlabComponent slab, out string note)
        {
            note = "";
            List<FloorType> floorTypes = new FilteredElementCollector(doc)
                .OfClass(typeof(FloorType))
                .Cast<FloorType>()
                .ToList();
            if (floorTypes.Count == 0)
            {
                return null;
            }

            string generatedName = BuildGeneratedFloorTypeName(slab);
            FloorType existing = floorTypes.FirstOrDefault(t => string.Equals(t.Name, generatedName, StringComparison.OrdinalIgnoreCase));
            if (existing != null)
            {
                string layerNote;
                note = TryApplyFloorMaterialLayers(doc, existing, slab, out layerNote)
                    ? "reused generated floor type | " + layerNote
                    : "reused generated floor type" + (string.IsNullOrWhiteSpace(layerNote) ? "" : " | " + layerNote);
                return existing;
            }

            FloorType matched = floorTypes.FirstOrDefault(t => string.Equals(t.Name, slab.Type, StringComparison.OrdinalIgnoreCase));
            FloorType template = matched ?? floorTypes.FirstOrDefault();
            if (string.IsNullOrWhiteSpace(generatedName))
            {
                note = matched == null ? "fallback to first floor type" : "";
                return template;
            }

            try
            {
                FloorType generated = template.Duplicate(generatedName) as FloorType;
                if (generated == null)
                {
                    note = "could not duplicate floor type; fallback to template";
                    return template;
                }

                List<string> notes = new List<string>();
                string layerNote;
                bool compoundLayersApplied = TryApplyFloorMaterialLayers(doc, generated, slab, out layerNote);
                if (compoundLayersApplied)
                {
                    notes.Add(layerNote);
                }
                else if (slab.ThicknessMm.HasValue && TrySetFloorTypeThickness(generated, slab.ThicknessMm.Value))
                {
                    notes.Add("thickness_mm applied to generated floor type");
                }
                if (!compoundLayersApplied && !string.IsNullOrWhiteSpace(slab.Material) && TryApplyHostTypeMaterial(doc, generated, slab.Material))
                {
                    notes.Add("material=" + slab.Material);
                }
                if (!compoundLayersApplied && !string.IsNullOrWhiteSpace(layerNote))
                {
                    notes.Add(layerNote);
                }
                note = notes.Count > 0 ? string.Join(" | ", notes) : "generated floor type from JSON slab data";
                return generated;
            }
            catch (Exception ex)
            {
                note = "could not generate floor type " + generatedName + ": " + ex.Message;
                return template;
            }
        }

        private static string BuildGeneratedFloorTypeName(SlabComponent slab)
        {
            List<string> parts = new List<string> { "AI Floor" };
            if (slab.ThicknessMm.HasValue && slab.ThicknessMm.Value > 0)
            {
                parts.Add(Math.Round(slab.ThicknessMm.Value).ToString(CultureInfo.InvariantCulture) + "mm");
            }
            if (!string.IsNullOrWhiteSpace(slab.Material))
            {
                parts.Add(SafeTypeName(slab.Material));
            }
            return string.Join(" ", parts).Trim();
        }

        private static bool TrySetFloorTypeThickness(FloorType floorType, double thicknessMm)
        {
            try
            {
                CompoundStructure structure = floorType.GetCompoundStructure();
                if (structure == null)
                {
                    return false;
                }
                IList<CompoundStructureLayer> layers = structure.GetLayers();
                if (layers == null || layers.Count == 0)
                {
                    return false;
                }
                int layerIndex = structure.GetFirstCoreLayerIndex();
                if (layerIndex < 0 || layerIndex >= layers.Count)
                {
                    layerIndex = 0;
                }
                structure.SetLayerWidth(layerIndex, MmToFeet(thicknessMm));
                floorType.SetCompoundStructure(structure);
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static bool TryApplyFloorMaterialLayers(Document doc, FloorType floorType, SlabComponent slab, out string note)
        {
            note = "";
            List<WallMaterialLayer> sourceLayers = slab.MaterialLayers ?? new List<WallMaterialLayer>();
            List<WallMaterialLayer> usableLayers = sourceLayers
                .Where(layer => layer != null && !string.IsNullOrWhiteSpace(layer.MaterialName ?? layer.Material))
                .ToList();
            if (usableLayers.Count == 0)
            {
                return false;
            }

            bool singleLayerCanUseTotal = usableLayers.Count == 1 && slab.ThicknessMm.HasValue && slab.ThicknessMm.Value > 0;
            if (!singleLayerCanUseTotal && usableLayers.Any(layer => !layer.ThicknessMm.HasValue || layer.ThicknessMm.Value <= 0))
            {
                note = "compound floor layers kept as metadata because one or more layer thicknesses are unresolved";
                return false;
            }

            try
            {
                CompoundStructure structure = floorType.GetCompoundStructure();
                if (structure == null)
                {
                    note = "floor type has no compound structure; material layers were not applied";
                    return false;
                }
                List<CompoundStructureLayer> revitLayers = new List<CompoundStructureLayer>();
                foreach (WallMaterialLayer layer in usableLayers)
                {
                    string materialName = string.IsNullOrWhiteSpace(layer.MaterialName) ? layer.Material : layer.MaterialName;
                    double thicknessMm = singleLayerCanUseTotal ? slab.ThicknessMm.Value : layer.ThicknessMm.Value;
                    MaterialFunctionAssignment function = WallLayerFunctionFromRole(layer.Role, materialName);
                    revitLayers.Add(new CompoundStructureLayer(MmToFeet(thicknessMm), function, FindOrCreateMaterial(doc, materialName).Id));
                }
                structure.SetLayers(revitLayers);
                floorType.SetCompoundStructure(structure);
                note = "applied " + revitLayers.Count.ToString(CultureInfo.InvariantCulture) + " confirmed floor material layer(s)";
                return true;
            }
            catch (Exception ex)
            {
                note = "could not apply floor material layers: " + ex.Message;
                return false;
            }
        }

        private static bool TryApplyHostTypeMaterial(Document doc, HostObjAttributes hostType, string materialName)
        {
            try
            {
                CompoundStructure structure = hostType.GetCompoundStructure();
                if (structure == null)
                {
                    return false;
                }
                IList<CompoundStructureLayer> layers = structure.GetLayers();
                if (layers == null || layers.Count == 0)
                {
                    return false;
                }
                int layerIndex = structure.GetFirstCoreLayerIndex();
                if (layerIndex < 0 || layerIndex >= layers.Count)
                {
                    layerIndex = 0;
                }
                structure.SetMaterialId(layerIndex, FindOrCreateMaterial(doc, materialName).Id);
                hostType.SetCompoundStructure(structure);
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static Level FindLevel(Document doc, string name)
        {
            return new FilteredElementCollector(doc)
                .OfClass(typeof(Level))
                .Cast<Level>()
                .FirstOrDefault(level => string.Equals(level.Name, name, StringComparison.OrdinalIgnoreCase));
        }

        private static Grid FindGrid(Document doc, string name)
        {
            if (string.IsNullOrWhiteSpace(name))
            {
                return null;
            }
            return new FilteredElementCollector(doc)
                .OfClass(typeof(Grid))
                .Cast<Grid>()
                .FirstOrDefault(grid => string.Equals(grid.Name, name, StringComparison.OrdinalIgnoreCase));
        }

        private static string SafeGridName(string name, string fallbackId)
        {
            string cleaned = Regex.Replace(name ?? "", @"\\[A-Za-z0-9]+", "");
            cleaned = Regex.Replace(cleaned, @"[\\:{}\[\]\|;<>\?`~]", "");
            cleaned = Regex.Replace(cleaned, @"\s+", " ").Trim();
            if (string.IsNullOrWhiteSpace(cleaned))
            {
                cleaned = fallbackId;
            }
            return cleaned;
        }

        private static WallType FindWallType(Document doc, WallComponent wall, out string note)
        {
            note = "";
            List<WallType> wallTypes = new FilteredElementCollector(doc)
                .OfClass(typeof(WallType))
                .Cast<WallType>()
                .Where(t => t.Kind == WallKind.Basic)
                .ToList();

            string generatedName = BuildGeneratedWallTypeName(wall);
            bool hasMaterialDefinition = HasWallMaterialDefinition(wall);
            if (!string.IsNullOrWhiteSpace(generatedName))
            {
                WallType existingGenerated = wallTypes.FirstOrDefault(t => string.Equals(t.Name, generatedName, StringComparison.OrdinalIgnoreCase));
                if (existingGenerated != null)
                {
                    note = "reused generated wall type with material layers";
                    return existingGenerated;
                }
            }

            WallType matched = wallTypes.FirstOrDefault(t => string.Equals(t.Name, wall.Type, StringComparison.OrdinalIgnoreCase));
            if (matched != null && !hasMaterialDefinition)
            {
                return matched;
            }

            WallType template = matched;
            if (template == null && wall.ThicknessMm.HasValue && wall.ThicknessMm.Value > 0)
            {
                template = wallTypes.FirstOrDefault(t => Math.Abs(FeetToMm(t.Width) - wall.ThicknessMm.Value) <= 5);
            }
            template = template ?? FindBasicWallTemplate(wallTypes);
            if (template == null)
            {
                note = "No basic wall type was found in this Revit project.";
                return null;
            }

            if (string.IsNullOrWhiteSpace(generatedName))
            {
                note = string.IsNullOrWhiteSpace(wall.Type) ? "fallback to first basic wall type" : "semantic wall type was not found; fallback to first basic wall type";
                return template;
            }

            WallType generated = template.Duplicate(generatedName) as WallType;
            if (generated == null)
            {
                note = "could not duplicate wall type; fallback to template";
                return template;
            }

            List<string> notes = new List<string>();
            if (wall.ThicknessMm.HasValue && !HasDetailedWallLayers(wall) && TrySetWallTypeThickness(generated, wall.ThicknessMm.Value))
            {
                notes.Add("thickness_mm applied to generated wall type");
            }
            if (TryApplyWallMaterialLayers(doc, generated, wall, out string materialNote))
            {
                notes.Add(materialNote);
            }
            else if (!string.IsNullOrWhiteSpace(materialNote))
            {
                notes.Add(materialNote);
            }

            note = notes.Count > 0 ? string.Join(" | ", notes) : "generated wall type from JSON wall data";
            return generated;
        }

        private static WallType FindOrCreateParapetWallType(
            Document doc,
            ParapetComponent parapet,
            Dictionary<string, Wall> sourceWalls,
            Dictionary<string, WallComponent> sourceWallComponents,
            out string note)
        {
            note = "";
            List<WallType> wallTypes = new FilteredElementCollector(doc)
                .OfClass(typeof(WallType))
                .Cast<WallType>()
                .Where(type => type.Kind == WallKind.Basic)
                .ToList();
            double thicknessMm = parapet != null && parapet.ThicknessMm.HasValue && parapet.ThicknessMm.Value > 0
                ? parapet.ThicknessMm.Value
                : 200.0;
            Wall sourceWall = null;
            if (parapet != null &&
                !string.IsNullOrWhiteSpace(parapet.ExteriorMaterialSourceWallId) &&
                sourceWalls != null)
            {
                sourceWalls.TryGetValue(parapet.ExteriorMaterialSourceWallId, out sourceWall);
            }
            WallType sourceWallType = sourceWall == null ? null : doc.GetElement(sourceWall.GetTypeId()) as WallType;
            string sourceResolutionNote = sourceWallType == null ? "" : "material type read from created exterior wall instance";
            if (sourceWallType == null &&
                parapet != null &&
                !string.IsNullOrWhiteSpace(parapet.ExteriorMaterialSourceWallId) &&
                sourceWallComponents != null &&
                sourceWallComponents.TryGetValue(parapet.ExteriorMaterialSourceWallId, out WallComponent sourceWallComponent))
            {
                sourceWallType = FindWallType(doc, sourceWallComponent, out string sourceTypeNote);
                sourceResolutionNote = "material type resolved from superseded roof-edge wall definition";
                if (!string.IsNullOrWhiteSpace(sourceTypeNote))
                {
                    sourceResolutionNote += ": " + sourceTypeNote;
                }
            }
            string sourceTypeName = sourceWallType == null ? "Default Exterior Wall" : sourceWallType.Name ?? "Exterior Wall";
            if (sourceTypeName.Length > 80)
            {
                sourceTypeName = sourceTypeName.Substring(0, 80);
            }
            string typeName = "AI Parapet "
                + Math.Round(thicknessMm).ToString(CultureInfo.InvariantCulture)
                + "mm From "
                + SafeTypeName(sourceTypeName);
            WallType parapetType = wallTypes.FirstOrDefault(type =>
                string.Equals(type.Name, typeName, StringComparison.OrdinalIgnoreCase));
            WallType template = parapetType ?? sourceWallType ?? wallTypes
                .OrderBy(type => Math.Abs(FeetToMm(type.Width) - thicknessMm))
                .FirstOrDefault();
            if (template == null)
            {
                note = "No basic wall type was available for a dedicated parapet type.";
                return null;
            }
            if (parapetType == null)
            {
                parapetType = template.Duplicate(typeName) as WallType;
                if (parapetType == null)
                {
                    note = "Could not duplicate a basic wall type for the dedicated parapet type.";
                    return null;
                }
            }

            List<CompoundStructureLayer> layers = BuildParapetMaterialLayersFromExteriorWall(
                sourceWallType,
                template,
                thicknessMm);
            if (layers.Count == 0)
            {
                layers.Add(new CompoundStructureLayer(
                    MmToFeet(thicknessMm),
                    MaterialFunctionAssignment.Structure,
                    ElementId.InvalidElementId));
            }
            CompoundStructure cleanStructure = CompoundStructure.CreateSimpleCompoundStructure(layers);
            parapetType.SetCompoundStructure(cleanStructure);
            note = "dedicated simple parapet wall type; vertically_compound=false; wall_sweeps=0; thickness_mm="
                + thicknessMm.ToString("0.###", CultureInfo.InvariantCulture)
                + "; material_source_wall_id=" + (parapet == null ? "" : parapet.ExteriorMaterialSourceWallId ?? "")
                + "; material_source_wall_type=" + sourceTypeName
                + "; inherited_material_layer_count=" + layers.Count.ToString(CultureInfo.InvariantCulture)
                + "; source_resolution=" + sourceResolutionNote;
            return parapetType;
        }

        private static List<CompoundStructureLayer> BuildParapetMaterialLayersFromExteriorWall(
            WallType sourceWallType,
            WallType fallbackType,
            double targetThicknessMm)
        {
            WallType materialSourceType = sourceWallType ?? fallbackType;
            List<CompoundStructureLayer> sourceLayers = new List<CompoundStructureLayer>();
            try
            {
                CompoundStructure sourceStructure = materialSourceType == null
                    ? null
                    : materialSourceType.GetCompoundStructure();
                if (sourceStructure != null)
                {
                    sourceLayers = sourceStructure.GetLayers()
                        .Where(layer => layer != null)
                        .ToList();
                }
            }
            catch
            {
            }
            if (sourceLayers.Count == 0)
            {
                return new List<CompoundStructureLayer>();
            }

            double targetWidth = MmToFeet(targetThicknessMm);
            double sourcePositiveWidth = sourceLayers
                .Where(layer => layer.Width > 0)
                .Sum(layer => layer.Width);
            double scale = sourcePositiveWidth > 1e-9 ? targetWidth / sourcePositiveWidth : 1.0;
            List<CompoundStructureLayer> inherited = new List<CompoundStructureLayer>();
            foreach (CompoundStructureLayer layer in sourceLayers)
            {
                double width = layer.Width > 0 ? layer.Width * scale : 0.0;
                inherited.Add(new CompoundStructureLayer(width, layer.Function, layer.MaterialId));
            }
            return inherited;
        }

        private static bool TrySetWallTypeThickness(WallType wallType, double thicknessMm)
        {
            try
            {
                CompoundStructure structure = wallType.GetCompoundStructure();
                if (structure == null)
                {
                    return false;
                }
                IList<CompoundStructureLayer> layers = structure.GetLayers();
                if (layers == null || layers.Count == 0)
                {
                    return false;
                }
                int layerIndex = structure.GetFirstCoreLayerIndex();
                if (layerIndex < 0 || layerIndex >= layers.Count)
                {
                    layerIndex = 0;
                }
                structure.SetLayerWidth(layerIndex, MmToFeet(thicknessMm));
                wallType.SetCompoundStructure(structure);
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static string BuildGeneratedWallTypeName(WallComponent wall)
        {
            List<string> parts = new List<string> { "AI Wall" };
            if (wall.ThicknessMm.HasValue && wall.ThicknessMm.Value > 0)
            {
                parts.Add(Math.Round(wall.ThicknessMm.Value).ToString(CultureInfo.InvariantCulture) + "mm");
            }

            string core = EffectiveCoreMaterial(wall);
            if (!string.IsNullOrWhiteSpace(core))
            {
                parts.Add(SafeTypeName(core));
            }

            string finish = PrimaryFinishMaterial(wall);
            if (!string.IsNullOrWhiteSpace(finish))
            {
                parts.Add("+ " + SafeTypeName(finish));
            }
            List<WallMaterialLayer> layers = wall.MaterialLayers ?? new List<WallMaterialLayer>();
            if (layers.Count > 0)
            {
                parts.Add("layered");
            }

            return string.Join(" ", parts).Trim();
        }

        private static bool TryApplyWallMaterialLayers(Document doc, WallType wallType, WallComponent wall, out string note)
        {
            note = "";
            List<WallLayerSpec> layerSpecs = BuildWallLayerSpecs(wall);
            if (layerSpecs.Count == 0)
            {
                return false;
            }

            try
            {
                CompoundStructure structure = wallType.GetCompoundStructure();
                if (structure == null)
                {
                    note = "wall type has no compound structure; material layers were not applied";
                    return false;
                }

                List<CompoundStructureLayer> revitLayers = new List<CompoundStructureLayer>();
                List<string> applied = new List<string>();
                foreach (WallLayerSpec spec in layerSpecs)
                {
                    ElementId materialId = FindOrCreateMaterial(doc, spec.MaterialName).Id;
                    revitLayers.Add(new CompoundStructureLayer(MmToFeet(spec.ThicknessMm), spec.Function, materialId));
                    applied.Add(spec.Role + "=" + spec.MaterialName + " " + Math.Round(spec.ThicknessMm).ToString(CultureInfo.InvariantCulture) + "mm");
                }

                structure.SetLayers(revitLayers);
                wallType.SetCompoundStructure(structure);
                note = "applied wall material layers: " + string.Join(", ", applied);
                return true;
            }
            catch (Exception ex)
            {
                note = "could not apply wall material layers: " + ex.Message;
                return false;
            }
        }

        private static bool HasWallMaterialDefinition(WallComponent wall)
        {
            return HasDetailedWallLayers(wall)
                || !string.IsNullOrWhiteSpace(EffectiveCoreMaterial(wall))
                || (wall.FinishMaterials != null && wall.FinishMaterials.Any(value => !string.IsNullOrWhiteSpace(value)));
        }

        private static bool HasDetailedWallLayers(WallComponent wall)
        {
            return wall.MaterialLayers != null && wall.MaterialLayers.Any(layer => !string.IsNullOrWhiteSpace(layer.MaterialName ?? layer.Material));
        }

        private static WallType FindBasicWallTemplate(List<WallType> wallTypes)
        {
            WallType namedBasic = wallTypes.FirstOrDefault(t =>
                ContainsAny((t.Name ?? "").ToLowerInvariant(), "basic", "generic", "常规", "基础", "默认"));
            if (namedBasic != null)
            {
                return namedBasic;
            }

            return wallTypes
                .OrderBy(t => Math.Abs(FeetToMm(t.Width) - 200))
                .FirstOrDefault();
        }

        private static List<WallLayerSpec> BuildWallLayerSpecs(WallComponent wall)
        {
            List<WallMaterialLayer> sourceLayers = wall.MaterialLayers ?? new List<WallMaterialLayer>();
            bool hasSemanticLayers = sourceLayers.Any(layer => layer != null && !string.IsNullOrWhiteSpace(layer.MaterialName ?? layer.Material));
            bool allLayerThicknessesKnown = hasSemanticLayers && sourceLayers
                .Where(layer => layer != null && !string.IsNullOrWhiteSpace(layer.MaterialName ?? layer.Material))
                .All(layer => layer.ThicknessMm.HasValue && layer.ThicknessMm.Value > 0);
            if (allLayerThicknessesKnown)
            {
                List<WallLayerSpec> explicitLayers = BuildExplicitWallLayerSpecs(wall);
                return NormalizeWallLayerThicknesses(explicitLayers, wall.ThicknessMm);
            }

            if (hasSemanticLayers)
            {
                List<WallLayerSpec> inferredLayers = BuildExplicitWallLayerSpecs(wall);
                string confirmedCore = EffectiveCoreMaterial(wall);
                if (!inferredLayers.Any(spec => spec.Function == MaterialFunctionAssignment.Structure) &&
                    !string.IsNullOrWhiteSpace(confirmedCore))
                {
                    inferredLayers.Add(
                        new WallLayerSpec
                        {
                            MaterialName = confirmedCore,
                            Role = "core_structure",
                            Function = MaterialFunctionAssignment.Structure,
                            ThicknessMm = wall.ThicknessMm.HasValue && wall.ThicknessMm.Value > 0
                                ? wall.ThicknessMm.Value
                                : 200
                        }
                    );
                }
                return NormalizeWallLayerThicknesses(inferredLayers, wall.ThicknessMm);
            }

            string coreMaterialName = EffectiveCoreMaterial(wall);
            List<string> finishMaterials = (wall.FinishMaterials ?? new List<string>())
                .Where(value => !string.IsNullOrWhiteSpace(value))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToList();
            if (string.IsNullOrWhiteSpace(coreMaterialName) && finishMaterials.Count == 0)
            {
                return new List<WallLayerSpec>();
            }

            double totalMm = wall.ThicknessMm.HasValue && wall.ThicknessMm.Value > 0 ? wall.ThicknessMm.Value : 200;
            List<WallLayerSpec> specs = new List<WallLayerSpec>();
            if (finishMaterials.Count > 0)
            {
                specs.Add(new WallLayerSpec
                {
                    MaterialName = finishMaterials[0],
                    Role = "exterior_finish",
                    Function = MaterialFunctionAssignment.Finish1,
                    ThicknessMm = GuessWallLayerThicknessMm(finishMaterials[0], "finish")
                });
            }

            if (!string.IsNullOrWhiteSpace(coreMaterialName))
            {
                specs.Add(new WallLayerSpec
                {
                    MaterialName = coreMaterialName,
                    Role = "core_structure",
                    Function = MaterialFunctionAssignment.Structure,
                    ThicknessMm = totalMm
                });
            }

            if (finishMaterials.Count > 1)
            {
                specs.Add(new WallLayerSpec
                {
                    MaterialName = finishMaterials[1],
                    Role = "interior_finish",
                    Function = MaterialFunctionAssignment.Finish2,
                    ThicknessMm = GuessWallLayerThicknessMm(finishMaterials[1], "finish")
                });
            }

            return NormalizeWallLayerThicknesses(specs, wall.ThicknessMm);
        }

        private static List<WallLayerSpec> BuildExplicitWallLayerSpecs(WallComponent wall)
        {
            List<WallLayerSpec> specs = new List<WallLayerSpec>();
            foreach (WallMaterialLayer layer in wall.MaterialLayers ?? new List<WallMaterialLayer>())
            {
                string materialName = layer.MaterialName;
                if (string.IsNullOrWhiteSpace(materialName))
                {
                    materialName = layer.Material;
                }
                if (string.IsNullOrWhiteSpace(materialName))
                {
                    continue;
                }

                string role = string.IsNullOrWhiteSpace(layer.Role) ? "core_structure" : layer.Role.Trim();
                specs.Add(new WallLayerSpec
                {
                    MaterialName = materialName.Trim(),
                    Role = role,
                    Function = WallLayerFunctionFromRole(role, materialName),
                    ThicknessMm = layer.ThicknessMm.HasValue && layer.ThicknessMm.Value > 0
                        ? layer.ThicknessMm.Value
                        : GuessWallLayerThicknessMm(materialName, role)
                });
            }
            return specs;
        }

        private static List<WallLayerSpec> NormalizeWallLayerThicknesses(List<WallLayerSpec> specs, double? wallThicknessMm)
        {
            specs = specs.Where(spec => !string.IsNullOrWhiteSpace(spec.MaterialName)).ToList();
            if (specs.Count == 0)
            {
                return specs;
            }

            double targetMm = wallThicknessMm.HasValue && wallThicknessMm.Value > 0 ? wallThicknessMm.Value : specs.Sum(spec => Math.Max(1, spec.ThicknessMm));
            double fixedNonCoreMm = specs
                .Where(spec => spec.Function != MaterialFunctionAssignment.Structure)
                .Sum(spec => Math.Max(1, spec.ThicknessMm));
            List<WallLayerSpec> coreSpecs = specs.Where(spec => spec.Function == MaterialFunctionAssignment.Structure).ToList();
            if (coreSpecs.Count == 0)
            {
                WallLayerSpec thickest = specs.OrderByDescending(spec => spec.ThicknessMm).First();
                thickest.Function = MaterialFunctionAssignment.Structure;
                thickest.Role = string.IsNullOrWhiteSpace(thickest.Role) ? "core_structure" : thickest.Role;
                coreSpecs.Add(thickest);
                fixedNonCoreMm = specs.Where(spec => !object.ReferenceEquals(spec, thickest)).Sum(spec => Math.Max(1, spec.ThicknessMm));
            }

            double remainingCoreMm = Math.Max(1, targetMm - fixedNonCoreMm);
            double currentCoreMm = coreSpecs.Sum(spec => Math.Max(1, spec.ThicknessMm));
            foreach (WallLayerSpec core in coreSpecs)
            {
                double ratio = currentCoreMm > 0 ? Math.Max(1, core.ThicknessMm) / currentCoreMm : 1.0 / coreSpecs.Count;
                core.ThicknessMm = Math.Max(1, remainingCoreMm * ratio);
            }

            foreach (WallLayerSpec spec in specs)
            {
                spec.ThicknessMm = Math.Max(1, spec.ThicknessMm);
            }
            return specs;
        }

        private static MaterialFunctionAssignment WallLayerFunctionFromRole(string role, string materialName)
        {
            string text = ((role ?? "") + " " + (materialName ?? "")).ToLowerInvariant();
            if (ContainsAny(text, "insulation", "thermal", "保温", "隔热")) return MaterialFunctionAssignment.Insulation;
            if (ContainsAny(text, "substrate", "sheathing", "找平", "基层", "砂浆")) return MaterialFunctionAssignment.Substrate;
            if (ContainsAny(text, "interior", "inside", "inner", "内", "finish2")) return MaterialFunctionAssignment.Finish2;
            if (ContainsAny(text, "finish", "paint", "coating", "plaster", "render", "tile", "面层", "饰面", "涂料", "抹灰", "外")) return MaterialFunctionAssignment.Finish1;
            return MaterialFunctionAssignment.Structure;
        }

        private static double GuessWallLayerThicknessMm(string materialName, string role)
        {
            string text = ((materialName ?? "") + " " + (role ?? "")).ToLowerInvariant();
            if (ContainsAny(text, "paint", "coating", "涂料", "面漆")) return 5;
            if (ContainsAny(text, "plaster", "render", "抹灰", "砂浆")) return 15;
            if (ContainsAny(text, "tile", "瓷砖", "面砖")) return 10;
            if (ContainsAny(text, "insulation", "保温", "隔热")) return 50;
            return 20;
        }

        private static int FindExteriorLayerIndex(CompoundStructure structure, IList<CompoundStructureLayer> layers, int coreIndex)
        {
            int firstCore = structure.GetFirstCoreLayerIndex();
            if (firstCore > 0)
            {
                return firstCore - 1;
            }
            return Math.Max(0, Math.Min(coreIndex, layers.Count - 1));
        }

        private static void EnsureFinishLayerWidth(CompoundStructure structure, IList<CompoundStructureLayer> layers, int layerIndex)
        {
            if (layerIndex < 0 || layerIndex >= layers.Count)
            {
                return;
            }
            if (layers[layerIndex].Width <= 0)
            {
                structure.SetLayerWidth(layerIndex, MmToFeet(5));
            }
        }

        private static Material FindOrCreateMaterial(Document doc, string materialName)
        {
            string normalized = materialName.Trim();
            Material existing = new FilteredElementCollector(doc)
                .OfClass(typeof(Material))
                .Cast<Material>()
                .FirstOrDefault(material => string.Equals(material.Name, normalized, StringComparison.OrdinalIgnoreCase));
            if (existing != null)
            {
                ApplyMaterialColor(existing, normalized);
                return existing;
            }

            ElementId id = Material.Create(doc, normalized);
            Material created = doc.GetElement(id) as Material;
            if (created != null)
            {
                ApplyMaterialColor(created, normalized);
            }
            return created;
        }

        private static void ApplyMaterialColor(Material material, string materialName)
        {
            if (material == null)
            {
                return;
            }
            material.Color = GuessMaterialColor(materialName);
        }

        private static Color GuessMaterialColor(string materialName)
        {
            string text = (materialName ?? "").ToLowerInvariant();
            if (ContainsAny(text, "yellow", "黄色", "黄")) return new Color(245, 205, 70);
            if (ContainsAny(text, "red", "红色", "红")) return new Color(180, 60, 50);
            if (ContainsAny(text, "blue", "蓝色", "蓝")) return new Color(70, 120, 200);
            if (ContainsAny(text, "green", "绿色", "绿")) return new Color(80, 150, 90);
            if (ContainsAny(text, "white", "白色", "白")) return new Color(235, 235, 225);
            if (ContainsAny(text, "black", "黑色", "黑")) return new Color(35, 35, 35);
            if (ContainsAny(text, "concrete", "混凝土", "砼")) return new Color(150, 150, 145);
            if (ContainsAny(text, "brick", "masonry", "砖")) return new Color(170, 90, 65);
            if (ContainsAny(text, "glass", "玻璃")) return new Color(160, 205, 220);
            if (ContainsAny(text, "wood", "木")) return new Color(150, 100, 55);
            return new Color(180, 180, 180);
        }

        private static string EffectiveCoreMaterial(WallComponent wall)
        {
            if (!string.IsNullOrWhiteSpace(wall.MaterialName)) return wall.MaterialName;
            return wall.Material;
        }

        private static string PrimaryFinishMaterial(WallComponent wall)
        {
            if (wall.FinishMaterials == null || wall.FinishMaterials.Count == 0)
            {
                return "";
            }
            return wall.FinishMaterials.FirstOrDefault(value => !string.IsNullOrWhiteSpace(value)) ?? "";
        }

        private static string SafeTypeName(string value)
        {
            string cleaned = new string((value ?? "").Select(ch => Path.GetInvalidFileNameChars().Contains(ch) ? ' ' : ch).ToArray());
            cleaned = cleaned.Replace("/", " ").Replace("\\", " ").Replace(":", " ");
            cleaned = Regex.Replace(cleaned, @"\s+", " ").Trim();
            return cleaned.Length <= 60 ? cleaned : cleaned.Substring(0, 60).Trim();
        }

        private static string FirstNonEmpty(params string[] values)
        {
            foreach (string value in values ?? new string[0])
            {
                if (!string.IsNullOrWhiteSpace(value))
                {
                    return value;
                }
            }
            return "";
        }

        private static string NormalizeText(string value)
        {
            return Regex.Replace((value ?? "").Trim().ToLowerInvariant(), @"\s+", "");
        }

        private static string TryLoadOpeningFamilyFile(Document doc, OpeningComponent item)
        {
            if (item == null || string.IsNullOrWhiteSpace(item.FamilyFile))
            {
                return "";
            }
            string path = item.FamilyFile;
            if (!File.Exists(path))
            {
                return "family_file not found: " + path;
            }
            try
            {
                Family loadedFamily;
                bool loaded = doc.LoadFamily(path, out loadedFamily);
                if (loadedFamily != null)
                {
                    // The internal Revit family name is not guaranteed to
                    // equal the RFA file name. Use the name returned by Revit
                    // so symbol lookup can find the family just loaded.
                    item.FamilyName = loadedFamily.Name;
                }
                return loaded
                    ? "loaded family_file " + Path.GetFileName(path)
                    : "family_file already loaded or unchanged: " + Path.GetFileName(path);
            }
            catch (Exception ex)
            {
                return "family_file could not be loaded: " + Path.GetFileName(path) + " (" + ex.Message + ")";
            }
        }

        private static string TryLoadColumnFamilyFile(Document doc, ColumnComponent item)
        {
            if (item == null || string.IsNullOrWhiteSpace(item.FamilyFile))
            {
                return "";
            }
            string path = item.FamilyFile;
            if (!File.Exists(path))
            {
                return "column family_file not found: " + path;
            }
            try
            {
                Family loadedFamily;
                bool loaded = doc.LoadFamily(path, out loadedFamily);
                if (loadedFamily != null && string.IsNullOrWhiteSpace(item.FamilyName))
                {
                    item.FamilyName = loadedFamily.Name;
                }
                return loaded
                    ? "loaded column family_file " + Path.GetFileName(path)
                    : "column family_file already loaded or unchanged: " + Path.GetFileName(path);
            }
            catch (Exception ex)
            {
                return "column family_file could not be loaded: " + Path.GetFileName(path) + " (" + ex.Message + ")";
            }
        }

        private static FamilySymbol FindFamilySymbol(Document doc, BuiltInCategory category, string typeName, double? widthMm, double? heightMm, string familyFile, string explicitFamilyName, out string note)
        {
            note = "";
            List<FamilySymbol> symbols = new FilteredElementCollector(doc)
                .OfCategory(category)
                .OfClass(typeof(FamilySymbol))
                .Cast<FamilySymbol>()
                .ToList();

            FamilySymbol explicitFileSymbol = FindFamilySymbolFromFamilyFile(symbols, category, typeName, widthMm, heightMm, familyFile, explicitFamilyName, out string explicitFileNote);
            if (explicitFileSymbol != null)
            {
                note = explicitFileNote;
                return explicitFileSymbol;
            }
            if (!string.IsNullOrWhiteSpace(familyFile))
            {
                note = "explicit family_file could not provide a compatible symbol: " + Path.GetFileName(familyFile) + "; cross-family fallback is forbidden";
                return null;
            }

            List<FamilySymbol> compatibleSymbols = FilterDoorSymbolsByLeafIntent(symbols, category, typeName, widthMm);
            if (compatibleSymbols.Count == 0 && category == BuiltInCategory.OST_Doors && RequestedDoorLeafCount(typeName, widthMm) > 0)
            {
                note = "no loaded door family symbol matches the requested leaf count";
                return null;
            }

            FamilySymbol matched = compatibleSymbols.FirstOrDefault(s =>
                string.Equals(s.Name, typeName, StringComparison.OrdinalIgnoreCase) ||
                string.Equals(s.FamilyName + " " + s.Name, typeName, StringComparison.OrdinalIgnoreCase));
            if (matched != null)
            {
                return matched;
            }

            if (widthMm.HasValue && heightMm.HasValue)
            {
                FamilySymbol bySize = compatibleSymbols.FirstOrDefault(s => SymbolMatchesSize(s, widthMm.Value, heightMm.Value));
                if (bySize != null)
                {
                    note = "family symbol matched by width_mm and height_mm";
                    return bySize;
                }

                FamilySymbol generated = FindOrCreateSizedFamilySymbol(compatibleSymbols, category, widthMm.Value, heightMm.Value, typeName, out string generatedNote);
                if (generated != null)
                {
                    note = generatedNote;
                    return generated;
                }
            }

            note = string.IsNullOrWhiteSpace(typeName) ? "fallback to first loaded family symbol" : "semantic family type was not found; fallback to first loaded family symbol";
            return compatibleSymbols.FirstOrDefault();
        }

        private static List<FamilySymbol> BuildOpeningPlacementCandidates(
            Document doc,
            BuiltInCategory category,
            string typeName,
            double? widthMm,
            double? heightMm,
            FamilySymbol preferred,
            string familyFile,
            string explicitFamilyName)
        {
            List<FamilySymbol> result = new List<FamilySymbol>();
            if (preferred != null)
            {
                result.Add(preferred);
            }

            List<FamilySymbol> compatible = FilterDoorSymbolsByLeafIntent(
                new FilteredElementCollector(doc)
                    .OfCategory(category)
                    .OfClass(typeof(FamilySymbol))
                    .Cast<FamilySymbol>()
                    .ToList(),
                category,
                typeName,
                widthMm);
            if (OpeningSymbolMatchesExplicitFamily(preferred, familyFile, explicitFamilyName))
            {
                long preferredFamilyId = ElementIdValue(preferred.Family.Id);
                compatible = compatible
                    .Where(candidate => candidate.Family != null && ElementIdValue(candidate.Family.Id) == preferredFamilyId)
                    .ToList();
            }
            long rejectedFamilyId = preferred == null || preferred.Family == null
                ? -1
                : ElementIdValue(preferred.Family.Id);
            IEnumerable<IGrouping<long, FamilySymbol>> otherFamilies = compatible
                .Where(candidate =>
                    candidate != null &&
                    (preferred == null || candidate.Id != preferred.Id) &&
                    (candidate.Family == null || ElementIdValue(candidate.Family.Id) != rejectedFamilyId))
                .GroupBy(candidate => candidate.Family == null ? ElementIdValue(candidate.Id) : ElementIdValue(candidate.Family.Id));

            foreach (IGrouping<long, FamilySymbol> familySymbols in otherFamilies)
            {
                FamilySymbol candidate = null;
                if (widthMm.HasValue && heightMm.HasValue)
                {
                    candidate = familySymbols.FirstOrDefault(value =>
                        SymbolMatchesSize(value, widthMm.Value, heightMm.Value));
                    if (candidate == null)
                    {
                        candidate = FindOrCreateSizedFamilySymbol(
                            familySymbols.ToList(),
                            category,
                            widthMm.Value,
                            heightMm.Value,
                            typeName,
                            out string ignoredNote);
                    }
                }
                candidate = candidate ?? familySymbols.FirstOrDefault();
                if (candidate != null && result.All(existing => existing.Id != candidate.Id))
                {
                    result.Add(candidate);
                }
            }
            return result;
        }

        private static bool OpeningSymbolMatchesExplicitFamily(FamilySymbol symbol, string familyFile, string explicitFamilyName)
        {
            if (symbol == null || symbol.Family == null || string.IsNullOrWhiteSpace(familyFile))
            {
                return false;
            }
            string fileFamilyName = Path.GetFileNameWithoutExtension(familyFile);
            return string.Equals(symbol.FamilyName, explicitFamilyName, StringComparison.OrdinalIgnoreCase) ||
                string.Equals(symbol.FamilyName, fileFamilyName, StringComparison.OrdinalIgnoreCase);
        }

        private static FamilySymbol FindFamilySymbolFromFamilyFile(List<FamilySymbol> symbols, BuiltInCategory category, string typeName, double? widthMm, double? heightMm, string familyFile, string explicitFamilyName, out string note)
        {
            note = "";
            if (symbols == null || symbols.Count == 0 || string.IsNullOrWhiteSpace(familyFile))
            {
                return null;
            }

            List<string> familyNames = new List<string>();
            if (!string.IsNullOrWhiteSpace(explicitFamilyName))
            {
                familyNames.Add(explicitFamilyName);
            }
            string fileFamilyName = Path.GetFileNameWithoutExtension(familyFile);
            if (!string.IsNullOrWhiteSpace(fileFamilyName))
            {
                familyNames.Add(fileFamilyName);
            }
            familyNames = familyNames
                .Where(value => !string.IsNullOrWhiteSpace(value))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToList();
            if (familyNames.Count == 0)
            {
                return null;
            }

            List<FamilySymbol> familySymbols = symbols
                .Where(s => familyNames.Any(name => string.Equals(s.FamilyName, name, StringComparison.OrdinalIgnoreCase)))
                .ToList();
            if (familySymbols.Count == 0)
            {
                return null;
            }

            // The family agent has already classified an explicit RFA. Some
            // files use opaque internal names and size-only type names, so an
            // unknown leaf count is acceptable unless the RFA contradicts it.
            familySymbols = FilterDoorSymbolsByLeafIntent(familySymbols, category, typeName, widthMm, true);
            if (familySymbols.Count == 0)
            {
                note = "explicit family_file conflicts with requested door leaf count or opening method";
                return null;
            }

            FamilySymbol typed = familySymbols.FirstOrDefault(s =>
                string.Equals(s.Name, typeName, StringComparison.OrdinalIgnoreCase) ||
                string.Equals(s.FamilyName + " " + s.Name, typeName, StringComparison.OrdinalIgnoreCase));
            if (typed != null)
            {
                note = "family symbol matched from explicit family_file";
                return typed;
            }

            if (widthMm.HasValue && heightMm.HasValue)
            {
                FamilySymbol bySize = familySymbols.FirstOrDefault(s => SymbolMatchesSize(s, widthMm.Value, heightMm.Value));
                if (bySize != null)
                {
                    note = "family_file symbol matched by width_mm and height_mm";
                    return bySize;
                }

                FamilySymbol generated = FindOrCreateSizedFamilySymbol(
                    familySymbols,
                    category,
                    widthMm.Value,
                    heightMm.Value,
                    typeName,
                    out string generatedNote,
                    true);
                if (generated != null)
                {
                    note = AppendNote("family_file matched " + Path.GetFileName(familyFile), generatedNote);
                    return generated;
                }
            }

            note = "family_file matched " + Path.GetFileName(familyFile) + "; fallback to first symbol in that family";
            return familySymbols.FirstOrDefault();
        }

        private static FamilySymbol FindOrCreateSizedFamilySymbol(
            List<FamilySymbol> symbols,
            BuiltInCategory category,
            double widthMm,
            double heightMm,
            string typeName,
            out string note,
            bool allowUnknownLeafIntent = false)
        {
            note = "";
            if (symbols == null || symbols.Count == 0)
            {
                return null;
            }

            string prefix = category == BuiltInCategory.OST_Doors ? "AI Door " : "AI Window ";
            string generatedName = prefix +
                Math.Round(widthMm).ToString(CultureInfo.InvariantCulture) + "x" +
                Math.Round(heightMm).ToString(CultureInfo.InvariantCulture);

            FamilySymbol existing = symbols.FirstOrDefault(s => string.Equals(s.Name, generatedName, StringComparison.OrdinalIgnoreCase));
            if (existing != null)
            {
                note = "reused generated family type " + generatedName;
                return existing;
            }

            List<FamilySymbol> compatibleSymbols = FilterDoorSymbolsByLeafIntent(
                symbols,
                category,
                typeName,
                widthMm,
                allowUnknownLeafIntent);
            FamilySymbol template = compatibleSymbols.FirstOrDefault(s => CanSetTypeSize(s)) ?? compatibleSymbols.FirstOrDefault();
            if (template == null)
            {
                note = "no compatible family template matches requested door leaf count";
                return null;
            }

            try
            {
                FamilySymbol generated = template.Duplicate(generatedName) as FamilySymbol;
                if (generated == null)
                {
                    note = "could not duplicate loaded family type; fallback to first loaded family symbol";
                    return null;
                }

                bool widthSet = SetLengthParameterByNamesWithResult(generated, widthMm, "Width", "width", "宽度");
                bool heightSet = SetLengthParameterByNamesWithResult(generated, heightMm, "Height", "height", "高度");
                if (widthSet && heightSet)
                {
                    note = "generated family type " + generatedName + " from loaded template";
                    return generated;
                }

                note = "generated family type " + generatedName + " but width/height parameters were not fully writable";
                return generated;
            }
            catch (Exception ex)
            {
                note = "could not generate family type " + generatedName + ": " + ex.Message;
                return null;
            }
        }

        private static bool CanSetTypeSize(FamilySymbol symbol)
        {
            return CanSetLengthParameterByNames(symbol, "Width", "width", "宽度") &&
                CanSetLengthParameterByNames(symbol, "Height", "height", "高度");
        }

        private static bool SymbolMatchesSize(FamilySymbol symbol, double widthMm, double heightMm)
        {
            double? symbolWidth = GetLengthParameterByNames(symbol, "Width", "width");
            double? symbolHeight = GetLengthParameterByNames(symbol, "Height", "height");
            if (!symbolWidth.HasValue || !symbolHeight.HasValue)
            {
                return false;
            }
            return Math.Abs(FeetToMm(symbolWidth.Value) - widthMm) <= 50 &&
                Math.Abs(FeetToMm(symbolHeight.Value) - heightMm) <= 50;
        }

        private static void SetStringParameter(Element element, BuiltInParameter parameterId, string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return;
            }
            Parameter parameter = element.get_Parameter(parameterId);
            if (parameter != null && !parameter.IsReadOnly)
            {
                parameter.Set(value);
            }
        }

        private static void SetLengthParameterByNames(Element element, double? valueMm, params string[] names)
        {
            if (!valueMm.HasValue)
            {
                return;
            }
            foreach (string name in ExpandParameterNames(names))
            {
                Parameter parameter = element.LookupParameter(name);
                if (parameter != null && !parameter.IsReadOnly)
                {
                    parameter.Set(MmToFeet(valueMm.Value));
                    return;
                }
            }
        }

        private static void SetMaterialParameterByNames(Element element, ElementId materialId, params string[] names)
        {
            foreach (string name in names)
            {
                Parameter parameter = element.LookupParameter(name);
                if (parameter != null && !parameter.IsReadOnly && parameter.StorageType == StorageType.ElementId)
                {
                    parameter.Set(materialId);
                    return;
                }
            }
        }

        private static bool SetLengthParameterByNamesWithResult(Element element, double? valueMm, params string[] names)
        {
            if (!valueMm.HasValue)
            {
                return false;
            }
            foreach (string name in ExpandParameterNames(names))
            {
                Parameter parameter = element.LookupParameter(name);
                if (parameter != null && !parameter.IsReadOnly && parameter.StorageType == StorageType.Double)
                {
                    return parameter.Set(MmToFeet(valueMm.Value));
                }
            }
            return false;
        }

        private static bool CanSetLengthParameterByNames(Element element, params string[] names)
        {
            foreach (string name in ExpandParameterNames(names))
            {
                Parameter parameter = element.LookupParameter(name);
                if (parameter != null && !parameter.IsReadOnly && parameter.StorageType == StorageType.Double)
                {
                    return true;
                }
            }
            return false;
        }

        private static void SetLengthParameter(Element element, BuiltInParameter parameterId, double? valueMm)
        {
            if (!valueMm.HasValue)
            {
                return;
            }
            Parameter parameter = element.get_Parameter(parameterId);
            if (parameter != null && !parameter.IsReadOnly)
            {
                parameter.Set(MmToFeet(valueMm.Value));
            }
        }

        private static double? GetLengthParameterByNames(Element element, params string[] names)
        {
            foreach (string name in ExpandParameterNames(names))
            {
                Parameter parameter = element.LookupParameter(name);
                if (parameter != null && parameter.StorageType == StorageType.Double)
                {
                    return parameter.AsDouble();
                }
            }
            return null;
        }

        private static void SetElementIdParameter(Element element, BuiltInParameter parameterId, ElementId value)
        {
            Parameter parameter = element.get_Parameter(parameterId);
            if (parameter != null && !parameter.IsReadOnly)
            {
                parameter.Set(value);
            }
        }

        private static void SetDoubleParameter(Element element, BuiltInParameter parameterId, double value)
        {
            Parameter parameter = element.get_Parameter(parameterId);
            if (parameter != null && !parameter.IsReadOnly)
            {
                parameter.Set(value);
            }
        }

        private static string BuildMetadataNote(ComponentBase item)
        {
            List<string> parts = new List<string>();
            string material = GetMaterial(item);
            if (!string.IsNullOrWhiteSpace(item.Source)) parts.Add("source=" + item.Source);
            if (!string.IsNullOrWhiteSpace(item.ReviewStatus)) parts.Add("review_status=" + item.ReviewStatus);
            if (!string.IsNullOrWhiteSpace(item.ModelingStatus)) parts.Add("modeling_status=" + item.ModelingStatus);
            if (!string.IsNullOrWhiteSpace(item.RevitExecutionScope)) parts.Add("revit_execution_scope=" + item.RevitExecutionScope);
            if (!string.IsNullOrWhiteSpace(item.HostRelationshipStatus)) parts.Add("host_relationship_status=" + item.HostRelationshipStatus);
            if (!string.IsNullOrWhiteSpace(item.VerticalBindingStatus)) parts.Add("vertical_binding_status=" + item.VerticalBindingStatus);
            if (!string.IsNullOrWhiteSpace(material)) parts.Add("material=" + material);
            if (item is WallComponent wall && wall.FinishMaterials != null && wall.FinishMaterials.Count > 0)
            {
                parts.Add("finish_materials=" + string.Join("|", wall.FinishMaterials.Where(value => !string.IsNullOrWhiteSpace(value))));
            }
            if (item is WallComponent layeredWall && layeredWall.MaterialLayers != null && layeredWall.MaterialLayers.Count > 0)
            {
                parts.Add("material_layers=" + string.Join("|", layeredWall.MaterialLayers
                    .Where(layer => layer != null && !string.IsNullOrWhiteSpace(layer.MaterialName ?? layer.Material))
                    .Select(layer =>
                    {
                        string materialName = string.IsNullOrWhiteSpace(layer.MaterialName) ? layer.Material : layer.MaterialName;
                        string thickness = layer.ThicknessMm.HasValue ? "@" + Math.Round(layer.ThicknessMm.Value).ToString(CultureInfo.InvariantCulture) + "mm" : "";
                        return (layer.Role ?? "layer") + ":" + materialName + thickness;
                    })));
            }
            if (item is OpeningComponent opening)
            {
                if (!string.IsNullOrWhiteSpace(opening.FamilyFile)) parts.Add("family_file=" + Path.GetFileName(opening.FamilyFile));
                if (!string.IsNullOrWhiteSpace(opening.FamilyName)) parts.Add("family_name=" + opening.FamilyName);
                if (!string.IsNullOrWhiteSpace(opening.FamilyType)) parts.Add("family_type=" + opening.FamilyType);
            }
            if (item is ColumnComponent column)
            {
                if (!string.IsNullOrWhiteSpace(column.FamilyFile)) parts.Add("family_file=" + Path.GetFileName(column.FamilyFile));
                if (!string.IsNullOrWhiteSpace(column.FamilyName)) parts.Add("family_name=" + column.FamilyName);
                if (!string.IsNullOrWhiteSpace(column.FamilyType)) parts.Add("family_type=" + column.FamilyType);
                if (!string.IsNullOrWhiteSpace(column.Type)) parts.Add("column_type=" + column.Type);
                if (column.WidthMm.HasValue) parts.Add("width_mm=" + column.WidthMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
                if (column.DepthMm.HasValue) parts.Add("depth_mm=" + column.DepthMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
                if (column.DiameterMm.HasValue) parts.Add("diameter_mm=" + column.DiameterMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
                if (column.HeightMm.HasValue) parts.Add("height_mm=" + column.HeightMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
            }
            if (item is GenericModelComponent generic)
            {
                if (!string.IsNullOrWhiteSpace(generic.StairCoreId)) parts.Add("stair_core_id=" + generic.StairCoreId);
                if (!string.IsNullOrWhiteSpace(generic.StairSegmentId)) parts.Add("stair_segment_id=" + generic.StairSegmentId);
                if (!string.IsNullOrWhiteSpace(generic.RecordRole)) parts.Add("record_role=" + generic.RecordRole);
                if (generic.StairSegmentNumber.HasValue) parts.Add("stair_segment_number=" + generic.StairSegmentNumber.Value.ToString(CultureInfo.InvariantCulture));
                if (!string.IsNullOrWhiteSpace(generic.MatchedFloorOpeningId)) parts.Add("matched_floor_opening_id=" + generic.MatchedFloorOpeningId);
                if (!string.IsNullOrWhiteSpace(generic.StairType)) parts.Add("stair_type=" + generic.StairType);
                if (!string.IsNullOrWhiteSpace(generic.Direction)) parts.Add("direction=" + generic.Direction);
                if (generic.RunCount.HasValue) parts.Add("run_count=" + generic.RunCount.Value.ToString("0.##", CultureInfo.InvariantCulture));
                if (generic.RisersPerRun.HasValue) parts.Add("risers_per_run=" + generic.RisersPerRun.Value.ToString("0.##", CultureInfo.InvariantCulture));
                if (generic.TreadsPerRun.HasValue) parts.Add("treads_per_run=" + generic.TreadsPerRun.Value.ToString("0.##", CultureInfo.InvariantCulture));
                if (generic.RiserHeightMm.HasValue) parts.Add("riser_height_mm=" + generic.RiserHeightMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
                if (generic.TreadDepthMm.HasValue) parts.Add("tread_depth_mm=" + generic.TreadDepthMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
                if (generic.NumberOfRisers.HasValue) parts.Add("number_of_risers=" + generic.NumberOfRisers.Value.ToString("0.##", CultureInfo.InvariantCulture));
                if (generic.NumberOfTreads.HasValue) parts.Add("number_of_treads=" + generic.NumberOfTreads.Value.ToString("0.##", CultureInfo.InvariantCulture));
                if (generic.RunLengthMm.HasValue) parts.Add("run_length_mm=" + generic.RunLengthMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
                if (generic.LandingLengthMm.HasValue) parts.Add("landing_length_mm=" + generic.LandingLengthMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
                if (generic.LandingWidthMm.HasValue) parts.Add("landing_width_mm=" + generic.LandingWidthMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
                if (generic.StairwellWidthMm.HasValue) parts.Add("stairwell_width_mm=" + generic.StairwellWidthMm.Value.ToString("0.##", CultureInfo.InvariantCulture));
            }
            if (!string.IsNullOrWhiteSpace(item.MaterialSource)) parts.Add("material_source=" + item.MaterialSource);
            if (item.MaterialConfidence.HasValue) parts.Add("material_confidence=" + item.MaterialConfidence.Value.ToString("0.##", CultureInfo.InvariantCulture));
            if (item.MaterialNeedsReview.HasValue && item.MaterialNeedsReview.Value) parts.Add("material_needs_review=true");
            if (!string.IsNullOrWhiteSpace(item.MaterialReason)) parts.Add("material_reason=" + item.MaterialReason);
            if (!string.IsNullOrWhiteSpace(item.ModelingReason)) parts.Add("modeling_reason=" + item.ModelingReason);
            return string.Join("; ", parts);
        }

        private static string GetMaterial(ComponentBase item)
        {
            if (item is WallComponent wall) return EffectiveCoreMaterial(wall);
            if (item is ColumnComponent column) return column.Material;
            if (item is OpeningComponent opening) return opening.Material;
            if (item is SlabComponent slab) return slab.Material;
            return "";
        }

        private static IEnumerable<string> ExpandParameterNames(IEnumerable<string> names)
        {
            foreach (string name in names)
            {
                yield return name;
                if (string.Equals(name, "Width", StringComparison.OrdinalIgnoreCase))
                {
                    yield return "宽度";
                }
                else if (string.Equals(name, "Height", StringComparison.OrdinalIgnoreCase))
                {
                    yield return "高度";
                }
                else if (string.Equals(name, "Sill Height", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(name, "Default Sill Height", StringComparison.OrdinalIgnoreCase))
                {
                    yield return "窗台高度";
                }
            }
        }

        private static string AppendNote(string existing, string note)
        {
            if (string.IsNullOrWhiteSpace(existing))
            {
                return note;
            }
            return existing + " | " + note;
        }

        private static XYZ ToXyz(Point3 value)
        {
            return new XYZ(MmToFeet(value.X), MmToFeet(value.Y), MmToFeet(value.Z));
        }

        private static bool CanCreateStairPlaceholder(GenericModelComponent item)
        {
            if (item == null)
            {
                return false;
            }
            string reviewStatus = (item.ReviewStatus ?? "").Trim().ToLowerInvariant();
            if (reviewStatus == "rejected")
            {
                return false;
            }
            if (item.ManualBuildApproved.HasValue && !item.ManualBuildApproved.Value)
            {
                return false;
            }
            return NormalizeBoundary(item.Boundary).Count >= 3;
        }

        private static Level ResolveGenericLevel(string levelName, Dictionary<string, Level> levels)
        {
            if (string.IsNullOrWhiteSpace(levelName) || levels == null)
            {
                return null;
            }
            if (levels.TryGetValue(levelName, out Level exact))
            {
                return exact;
            }
            string normalized = NormalizeText(levelName);
            foreach (KeyValuePair<string, Level> pair in levels)
            {
                if (NormalizeText(pair.Key) == normalized || NormalizeText(pair.Value.Name) == normalized)
                {
                    return pair.Value;
                }
            }
            return null;
        }

        private static double EffectiveStairHeightMm(GenericModelComponent item, Level startLevel, Level endLevel)
        {
            if (item.TotalRiseMm.HasValue && item.TotalRiseMm.Value > 0)
            {
                return item.TotalRiseMm.Value;
            }
            if (item.HeightMm.HasValue && item.HeightMm.Value > 0)
            {
                return item.HeightMm.Value;
            }
            if (startLevel != null && endLevel != null)
            {
                double delta = FeetToMm(endLevel.Elevation - startLevel.Elevation);
                if (Math.Abs(delta) > 1)
                {
                    return Math.Abs(delta);
                }
            }
            if (item.Start != null && item.End != null && Math.Abs(item.End.Z - item.Start.Z) > 1)
            {
                return Math.Abs(item.End.Z - item.Start.Z);
            }
            return 3300;
        }

        private static DirectShape CreateDirectShapeWithFallback(Document doc, BuiltInCategory preferredCategory)
        {
            try
            {
                return DirectShape.CreateElement(doc, new ElementId(preferredCategory));
            }
            catch
            {
                return DirectShape.CreateElement(doc, new ElementId(BuiltInCategory.OST_GenericModel));
            }
        }

        private static bool BoundaryLooksOutsidePlan(StandardModel model, List<Point3> boundary)
        {
            List<Point3> points = NormalizeBoundary(boundary);
            List<WallComponent> walls = model == null || model.Components == null ? new List<WallComponent>() : model.Components.Walls ?? new List<WallComponent>();
            if (points.Count == 0 || walls.Count == 0)
            {
                return false;
            }
            List<double> wallXs = walls.SelectMany(wall => new[] { wall.Start, wall.End }).Where(point => point != null).Select(point => point.X).ToList();
            List<double> wallYs = walls.SelectMany(wall => new[] { wall.Start, wall.End }).Where(point => point != null).Select(point => point.Y).ToList();
            if (wallXs.Count == 0 || wallYs.Count == 0)
            {
                return false;
            }
            double minX = wallXs.Min();
            double maxX = wallXs.Max();
            double minY = wallYs.Min();
            double maxY = wallYs.Max();
            double tolerance = Math.Max(maxX - minX, maxY - minY) * 2.0 + 1000;
            double centerX = points.Average(point => point.X);
            double centerY = points.Average(point => point.Y);
            return centerX < minX - tolerance || centerX > maxX + tolerance ||
                centerY < minY - tolerance || centerY > maxY + tolerance;
        }

        private static bool CanRecoverOpeningForModeling(OpeningComponent item, BuiltInCategory category)
        {
            if (item == null)
            {
                return false;
            }
            string reviewStatus = (item.ReviewStatus ?? "").Trim().ToLowerInvariant();
            if (reviewStatus == "rejected")
            {
                return false;
            }
            if (item.Location == null || string.IsNullOrWhiteSpace(item.HostWallId) || !item.WidthMm.HasValue || item.WidthMm.Value <= 0)
            {
                return false;
            }
            double? heightMm = EffectiveOpeningHeightMm(item);
            if (heightMm.HasValue && heightMm.Value > 0)
            {
                return true;
            }
            return category == BuiltInCategory.OST_Doors;
        }

        private static bool CanRecoverColumnForModeling(ColumnComponent item)
        {
            if (item == null || item.Location == null || string.IsNullOrWhiteSpace(item.Level))
            {
                return false;
            }
            if (string.Equals((item.ReviewStatus ?? "").Trim(), "rejected", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }

            bool hasRoundSection = item.DiameterMm.HasValue && item.DiameterMm.Value > 0;
            bool hasRectangularSection = item.WidthMm.HasValue && item.WidthMm.Value > 0 &&
                item.DepthMm.HasValue && item.DepthMm.Value > 0;
            return hasRoundSection || hasRectangularSection;
        }

        private static double? EffectiveOpeningHeightMm(OpeningComponent item)
        {
            if (item == null)
            {
                return null;
            }
            return item.HeightMm.HasValue ? item.HeightMm : item.RevitHeightMm;
        }

        private static double? EffectiveSillHeightMm(OpeningComponent item)
        {
            if (item == null)
            {
                return null;
            }
            return item.SillHeightMm.HasValue ? item.SillHeightMm : item.RevitSillOffsetMm;
        }

        private static Point3 BuildOpeningPlacementPoint(OpeningComponent item, BuiltInCategory category, double? sillHeightMm, Dictionary<string, Level> levels)
        {
            Point3 location = item.Location;
            double baseZ = ResolveOpeningBaseElevationMm(item, levels);
            double z = category == BuiltInCategory.OST_Windows && sillHeightMm.HasValue
                ? baseZ + sillHeightMm.Value
                : baseZ;

            return new Point3
            {
                X = location.X,
                Y = location.Y,
                Z = z
            };
        }

        private static Point3 ProjectOpeningPointToHostWall(Point3 placement, Wall host)
        {
            if (placement == null || host == null)
            {
                return placement;
            }
            LocationCurve locationCurve = host.Location as LocationCurve;
            Line line = locationCurve == null ? null : locationCurve.Curve as Line;
            if (line == null)
            {
                return placement;
            }

            XYZ start = line.GetEndPoint(0);
            XYZ end = line.GetEndPoint(1);
            double sx = FeetToMm(start.X);
            double sy = FeetToMm(start.Y);
            double ex = FeetToMm(end.X);
            double ey = FeetToMm(end.Y);
            double dx = ex - sx;
            double dy = ey - sy;
            double lengthSquared = dx * dx + dy * dy;
            if (lengthSquared <= 1)
            {
                return placement;
            }

            double t = ((placement.X - sx) * dx + (placement.Y - sy) * dy) / lengthSquared;
            double wallLengthMm = Math.Sqrt(lengthSquared);
            if (wallLengthMm > 2)
            {
                double margin = Math.Min(200, wallLengthMm * 0.05) / wallLengthMm;
                t = Math.Max(margin, Math.Min(1.0 - margin, t));
            }

            double projectedX = sx + dx * t;
            double projectedY = sy + dy * t;
            return new Point3
            {
                X = projectedX,
                Y = projectedY,
                Z = placement.Z
            };
        }

        private static string VerifyOpeningHostInsertion(Document doc, Wall host, FamilyInstance instance)
        {
            if (doc == null || host == null || instance == null)
            {
                return "";
            }

            doc.Regenerate();
            if (instance.Host == null || instance.Host.Id != host.Id)
            {
                return "warning: opening is not hosted by the requested wall after placement";
            }

            IList<ElementId> inserts = host.FindInserts(true, true, true, true);
            foreach (ElementId insertId in inserts)
            {
                if (insertId == instance.Id)
                {
                    return "native wall opening cut confirmed";
                }
            }

            return "warning: family is hosted but the wall has no native opening cut for this instance; " +
                   "use a wall-hosted Door/Window family with a configured Opening Cut";
        }

        private static bool OpeningGeometryCoversPlacement(
            Document doc,
            FamilyInstance instance,
            Wall host,
            Point3 placement,
            double? openingWidthMm,
            out string note)
        {
            note = "";
            if (doc == null || instance == null || host == null || placement == null)
            {
                note = "placement geometry validation did not receive a complete instance, host, and point";
                return false;
            }
            if (instance.Host == null || instance.Host.Id != host.Id)
            {
                note = "instance is not hosted by the requested wall";
                return false;
            }

            LocationPoint locationPoint = instance.Location as LocationPoint;
            if (locationPoint != null)
            {
                double locationDx = FeetToMm(locationPoint.Point.X) - placement.X;
                double locationDy = FeetToMm(locationPoint.Point.Y) - placement.Y;
                double locationDistanceMm = Math.Sqrt(locationDx * locationDx + locationDy * locationDy);
                if (locationDistanceMm > 100)
                {
                    note = "instance insertion point shifted " +
                        Math.Round(locationDistanceMm).ToString(CultureInfo.InvariantCulture) +
                        " mm from the requested opening";
                    return false;
                }
            }

            Options options = new Options
            {
                ComputeReferences = false,
                IncludeNonVisibleObjects = false,
                DetailLevel = ViewDetailLevel.Fine
            };
            List<XYZ> solidVertices = new List<XYZ>();
            CollectSolidVertices(instance.get_Geometry(options), solidVertices);
            double minX;
            double maxX;
            double minY;
            double maxY;
            if (solidVertices.Count > 0)
            {
                minX = solidVertices.Min(point => FeetToMm(point.X));
                maxX = solidVertices.Max(point => FeetToMm(point.X));
                minY = solidVertices.Min(point => FeetToMm(point.Y));
                maxY = solidVertices.Max(point => FeetToMm(point.Y));
            }
            else
            {
                BoundingBoxXYZ box = instance.get_BoundingBox(null);
                if (box == null)
                {
                    note = "family exposes no model geometry or bounding box";
                    return false;
                }
                minX = FeetToMm(box.Min.X);
                maxX = FeetToMm(box.Max.X);
                minY = FeetToMm(box.Min.Y);
                maxY = FeetToMm(box.Max.Y);
            }

            double dx = placement.X < minX ? minX - placement.X : placement.X > maxX ? placement.X - maxX : 0;
            double dy = placement.Y < minY ? minY - placement.Y : placement.Y > maxY ? placement.Y - maxY : 0;
            double geometryGapMm = Math.Sqrt(dx * dx + dy * dy);
            double wallHalfWidthMm = host.Width > 0 ? FeetToMm(host.Width) / 2.0 : 0;
            double allowedGapMm = Math.Max(300, wallHalfWidthMm + 150);
            if (geometryGapMm > allowedGapMm)
            {
                note = "visible model geometry is offset " +
                    Math.Round(geometryGapMm).ToString(CultureInfo.InvariantCulture) +
                    " mm from the requested opening (allowed " +
                    Math.Round(allowedGapMm).ToString(CultureInfo.InvariantCulture) + " mm)";
                return false;
            }
            if (solidVertices.Count > 0)
            {
                double farthestGeometryDistanceMm = solidVertices.Max(point =>
                {
                    double vertexDx = FeetToMm(point.X) - placement.X;
                    double vertexDy = FeetToMm(point.Y) - placement.Y;
                    return Math.Sqrt(vertexDx * vertexDx + vertexDy * vertexDy);
                });
                double allowedExtentMm = Math.Max(
                    900,
                    openingWidthMm.HasValue && openingWidthMm.Value > 0
                        ? openingWidthMm.Value * 1.75
                        : 1200);
                if (farthestGeometryDistanceMm > allowedExtentMm)
                {
                    note = "visible model geometry extends " +
                        Math.Round(farthestGeometryDistanceMm).ToString(CultureInfo.InvariantCulture) +
                        " mm from the requested opening (allowed " +
                        Math.Round(allowedExtentMm).ToString(CultureInfo.InvariantCulture) +
                        " mm); the family likely contains a displaced door leaf or nested solid";
                    return false;
                }
            }
            return true;
        }

        private static string ApplyDoorSwingOrientation(
            Document doc,
            FamilyInstance instance,
            Wall host,
            OpeningComponent item)
        {
            if (doc == null || instance == null || host == null || item == null)
            {
                return "";
            }
            LocationCurve wallLocation = host.Location as LocationCurve;
            Line wallLine = wallLocation == null ? null : wallLocation.Curve as Line;
            if (wallLine == null)
            {
                return "door orientation unchanged because the host wall is not linear";
            }
            XYZ wallDirection = (wallLine.GetEndPoint(1) - wallLine.GetEndPoint(0)).Normalize();
            XYZ desiredFacing = DoorPanelDirection(item);
            if (desiredFacing == null)
            {
                return "door orientation unchanged because CAD panel direction is unavailable";
            }
            desiredFacing = new XYZ(desiredFacing.X, desiredFacing.Y, 0).Normalize();
            double panelWallDot = Math.Abs(desiredFacing.DotProduct(wallDirection));
            if (panelWallDot > 0.35)
            {
                return "door orientation unchanged because CAD panel is not perpendicular to its host wall";
            }

            bool facingFlipped = false;
            bool handFlipped = false;
            if (instance.FacingOrientation.DotProduct(desiredFacing) < 0.95 && instance.CanFlipFacing)
            {
                facingFlipped = instance.flipFacing();
                doc.Regenerate();
            }

            string swingSide = (item.SwingSide ?? "").Trim().ToLowerInvariant();
            XYZ desiredHingeDirection = null;
            string handEvidence = "";
            if (item.PanelStart != null && item.Location != null)
            {
                XYZ centerToHinge = new XYZ(
                    item.PanelStart.X - item.Location.X,
                    item.PanelStart.Y - item.Location.Y,
                    0);
                double hingeAlongWall = centerToHinge.DotProduct(wallDirection);
                if (Math.Abs(hingeAlongWall) > 1)
                {
                    desiredHingeDirection = hingeAlongWall > 0 ? wallDirection : wallDirection.Negate();
                    handEvidence = "cad_hinge_point";
                }
            }
            if (desiredHingeDirection == null && (swingSide == "left" || swingSide == "right"))
            {
                desiredHingeDirection = swingSide == "left" ? wallDirection.Negate() : wallDirection;
                handEvidence = "swing_side_fallback";
            }
            if (desiredHingeDirection != null && instance.CanFlipHand)
            {
                // For the wall-hosted door families used here, HandOrientation
                // points toward the hinge side. Prefer the explicit CAD hinge
                // coordinate so wall direction and family mirroring cannot
                // invert left/right semantics.
                if (instance.HandOrientation.DotProduct(desiredHingeDirection) < 0.95)
                {
                    handFlipped = instance.flipHand();
                    doc.Regenerate();
                }
            }
            double facingAlignment = instance.FacingOrientation.DotProduct(desiredFacing);
            double handAlignment = desiredHingeDirection == null
                ? double.NaN
                : instance.HandOrientation.DotProduct(desiredHingeDirection);
            string orientationAudit =
                "; facing_alignment=" + Math.Round(facingAlignment, 3).ToString(CultureInfo.InvariantCulture) +
                "; hand_alignment=" + (double.IsNaN(handAlignment)
                    ? "n/a"
                    : Math.Round(handAlignment, 3).ToString(CultureInfo.InvariantCulture)) +
                "; can_flip_facing=" + instance.CanFlipFacing.ToString().ToLowerInvariant() +
                "; can_flip_hand=" + instance.CanFlipHand.ToString().ToLowerInvariant();
            return "door orientation matched to CAD panel perpendicular to host wall" +
                "; facing_flipped=" + facingFlipped.ToString().ToLowerInvariant() +
                "; hand_flipped=" + handFlipped.ToString().ToLowerInvariant() +
                "; hand_evidence=" + handEvidence +
                "; open_direction=" + (item.OpenDirection ?? "") +
                "; swing_side=" + (item.SwingSide ?? "") +
                orientationAudit;
        }

        private static XYZ DoorPanelDirection(OpeningComponent item)
        {
            if (item.PanelStart != null && item.PanelEnd != null)
            {
                double dx = item.PanelEnd.X - item.PanelStart.X;
                double dy = item.PanelEnd.Y - item.PanelStart.Y;
                if (Math.Sqrt(dx * dx + dy * dy) > 1)
                {
                    return new XYZ(dx, dy, 0);
                }
            }
            switch ((item.OpenDirection ?? "").Trim().ToLowerInvariant())
            {
                case "north": return XYZ.BasisY;
                case "south": return XYZ.BasisY.Negate();
                case "east": return XYZ.BasisX;
                case "west": return XYZ.BasisX.Negate();
                default: return null;
            }
        }

        private static void CollectSolidVertices(GeometryElement geometry, List<XYZ> vertices)
        {
            if (geometry == null || vertices == null)
            {
                return;
            }
            foreach (GeometryObject geometryObject in geometry)
            {
                Solid solid = geometryObject as Solid;
                if (solid != null && solid.Faces.Size > 0 && solid.Edges.Size > 0)
                {
                    foreach (Edge edge in solid.Edges)
                    {
                        vertices.AddRange(edge.Tessellate());
                    }
                    continue;
                }
                GeometryInstance geometryInstance = geometryObject as GeometryInstance;
                if (geometryInstance != null)
                {
                    CollectSolidVertices(geometryInstance.GetInstanceGeometry(), vertices);
                }
            }
        }

        private static double ResolveOpeningBaseElevationMm(OpeningComponent item, Dictionary<string, Level> levels)
        {
            if (item == null || item.Location == null)
            {
                return 0;
            }
            double locationZ = item.Location.Z;
            Level level = ResolveOpeningLevel(item, levels);
            if (level != null)
            {
                double levelElevationMm = FeetToMm(level.Elevation);
                if (Math.Abs(locationZ) <= 1.0)
                {
                    return levelElevationMm;
                }
            }
            return locationZ;
        }

        private static Level ResolveOpeningLevel(OpeningComponent item, Dictionary<string, Level> levels)
        {
            if (item == null || string.IsNullOrWhiteSpace(item.Level) || levels == null)
            {
                return null;
            }
            if (levels.TryGetValue(item.Level, out Level exact))
            {
                return exact;
            }
            string normalized = NormalizeText(item.Level);
            foreach (KeyValuePair<string, Level> pair in levels)
            {
                if (NormalizeText(pair.Key) == normalized || NormalizeText(pair.Value.Name) == normalized)
                {
                    return pair.Value;
                }
            }
            return null;
        }

        private static Point3 BuildOpeningPlacementPoint(Point3 location, BuiltInCategory category, double? sillHeightMm)
        {
            if (category != BuiltInCategory.OST_Windows || !sillHeightMm.HasValue)
            {
                return location;
            }

            return new Point3
            {
                X = location.X,
                Y = location.Y,
                Z = sillHeightMm.Value
            };
        }

        private static bool IsSamePoint(Point3 first, Point3 second)
        {
            const double toleranceMm = 0.001;
            return Math.Abs(first.X - second.X) <= toleranceMm &&
                Math.Abs(first.Y - second.Y) <= toleranceMm &&
                Math.Abs(first.Z - second.Z) <= toleranceMm;
        }

        private static double MmToFeet(double mm)
        {
            return mm / 304.8;
        }

        private static double FeetToMm(double feet)
        {
            return feet * 304.8;
        }

        private static double PositiveOrDefault(double? value, double fallback)
        {
            return value.HasValue && value.Value > 0 ? value.Value : fallback;
        }

        private static long ElementIdValue(ElementId id)
        {
            return id.Value;
        }

        private class StairBox
        {
            public double MinX { get; set; }
            public double MaxX { get; set; }
            public double MinY { get; set; }
            public double MaxY { get; set; }
            public double WidthMm { get { return MaxX - MinX; } }
            public double DepthMm { get { return MaxY - MinY; } }
            public double CenterX { get { return (MinX + MaxX) / 2.0; } }
            public double CenterY { get { return (MinY + MaxY) / 2.0; } }

            public bool ContainsPlanPoint(double x, double y, double toleranceMm)
            {
                double tolerance = Math.Max(20.0, toleranceMm);
                return x >= MinX - tolerance && x <= MaxX + tolerance
                    && y >= MinY - tolerance && y <= MaxY + tolerance;
            }

            public static StairBox FromBoundary(List<Point3> boundary)
            {
                List<Point3> points = NormalizeBoundary(boundary);
                if (points.Count < 3)
                {
                    return null;
                }
                return new StairBox
                {
                    MinX = points.Min(point => point.X),
                    MaxX = points.Max(point => point.X),
                    MinY = points.Min(point => point.Y),
                    MaxY = points.Max(point => point.Y)
                };
            }
        }
    }

    public class StandardModel
    {
        [JsonPropertyName("schema_version")]
        public string SchemaVersion { get; set; }
        public ProjectInfo Project { get; set; }
        public ComponentSet Components { get; set; }
        public List<RoomComponent> Rooms { get; set; }
        public ValidationInfo Validation { get; set; }
        [JsonPropertyName("llm_revit_execution_plan")]
        public RevitExecutionPlan LlmRevitExecutionPlan { get; set; }
    }

    public class OpeningFamilyRequirement
    {
        public string GeneratedName { get; set; }
        public double WidthMm { get; set; }
        public double HeightMm { get; set; }
        public int Count { get; set; }
        public string ComponentIds { get; set; }
        public List<string> TypeHints { get; set; }
        public List<string> SourceFiles { get; set; }
        public List<string> Evidence { get; set; }
    }

    internal class WallLayerSpec
    {
        public string MaterialName { get; set; }
        public string Role { get; set; }
        public double ThicknessMm { get; set; }
        public MaterialFunctionAssignment Function { get; set; }
    }

    public class FamilyLoadCandidate
    {
        public BuiltInCategory Category { get; set; }
        public string RequirementName { get; set; }
        public string FilePath { get; set; }
        public string FileName { get; set; }
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
    }

    public class FamilyFileEvaluation
    {
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
    }

    public class FamilySemanticIndex
    {
        [JsonPropertyName("families")]
        public List<FamilySemanticEntry> Families { get; set; }
    }

    public class FamilySemanticEntry
    {
        [JsonPropertyName("file_path")]
        public string FilePath { get; set; }
        [JsonPropertyName("file_name")]
        public string FileName { get; set; }
        [JsonPropertyName("file_hash")]
        public string FileHash { get; set; }
        [JsonPropertyName("visual_summary")]
        public string VisualSummary { get; set; }
        [JsonPropertyName("category")]
        public string Category { get; set; }
        [JsonPropertyName("family_type")]
        public string FamilyType { get; set; }
        [JsonPropertyName("features")]
        public List<string> Features { get; set; }
        [JsonPropertyName("confidence")]
        public double Confidence { get; set; }
        [JsonPropertyName("source")]
        public string Source { get; set; }
        [JsonPropertyName("created_at")]
        public string CreatedAt { get; set; }
    }

    public class ComponentReferenceIndex
    {
        [JsonPropertyName("component_group")]
        public string ComponentGroup { get; set; }
        [JsonPropertyName("semantic_descriptions")]
        public List<ComponentSemanticDescription> SemanticDescriptions { get; set; }
    }

    public class ComponentSemanticDescription
    {
        [JsonPropertyName("family_file")]
        public string FamilyFile { get; set; }
        [JsonPropertyName("image_file")]
        public string ImageFile { get; set; }
        [JsonPropertyName("visual_summary")]
        public string VisualSummary { get; set; }
        [JsonPropertyName("category")]
        public string Category { get; set; }
        [JsonPropertyName("family_type")]
        public string FamilyType { get; set; }
        [JsonPropertyName("features")]
        public List<string> Features { get; set; }
        [JsonPropertyName("confidence")]
        public double Confidence { get; set; }
    }

    public class ProjectInfo
    {
        public string Name { get; set; }
        public string Units { get; set; }
        [JsonPropertyName("design_note_summary")]
        public string DesignNoteSummary { get; set; }
        [JsonPropertyName("specification_summary")]
        public string SpecificationSummary { get; set; }
    }

    public class ComponentSet
    {
        public List<LevelComponent> Levels { get; set; }
        public List<GridComponent> Grids { get; set; }
        public List<ColumnComponent> Columns { get; set; }
        public List<WallComponent> Walls { get; set; }
        public List<SlabComponent> Slabs { get; set; }
        [JsonPropertyName("floor_openings")]
        public List<FloorOpeningComponent> FloorOpenings { get; set; }
        public List<OpeningComponent> Doors { get; set; }
        public List<OpeningComponent> Windows { get; set; }
        public List<RoomComponent> Rooms { get; set; }
        public List<GenericModelComponent> Stairs { get; set; }
        public List<GenericModelComponent> Railings { get; set; }
        public List<GenericModelComponent> Roofs { get; set; }
        public List<ParapetComponent> Parapets { get; set; }
    }

    public class ComponentBase
    {
        public string Id { get; set; }
        [JsonPropertyName("drawing_id")]
        public string DrawingId { get; set; }
        [JsonPropertyName("floor_number")]
        public JsonElement FloorNumber { get; set; }
        [JsonPropertyName("element_id")]
        public string ElementIdAlias { get { return Id; } set { if (!string.IsNullOrWhiteSpace(value)) Id = value; } }
        [JsonPropertyName("needs_review")]
        public bool? NeedsReviewAlias
        {
            get { return string.Equals(ReviewStatus, "needs_review", StringComparison.OrdinalIgnoreCase); }
            set { if (value.HasValue && value.Value && string.IsNullOrWhiteSpace(ReviewStatus)) ReviewStatus = "needs_review"; }
        }
        public string Type { get; set; }
        public string Source { get; set; }
        public double Confidence { get; set; }
        [JsonPropertyName("review_status")]
        public string ReviewStatus { get; set; }
        [JsonPropertyName("modeling_status")]
        public string ModelingStatus { get; set; }
        [JsonPropertyName("revit_execution_scope")]
        public string RevitExecutionScope { get; set; }
        [JsonPropertyName("modeling_reason")]
        public string ModelingReason { get; set; }
        [JsonPropertyName("host_relationship_status")]
        public string HostRelationshipStatus { get; set; }
        [JsonPropertyName("vertical_binding_status")]
        public string VerticalBindingStatus { get; set; }
        public string Notes { get; set; }
        [JsonPropertyName("material_source")]
        public string MaterialSource { get; set; }
        [JsonPropertyName("material_evidence")]
        public string MaterialEvidence { get; set; }
        [JsonPropertyName("material_confidence")]
        [JsonConverter(typeof(FlexibleNullableConfidenceConverter))]
        public double? MaterialConfidence { get; set; }
        [JsonPropertyName("material_needs_review")]
        public bool? MaterialNeedsReview { get; set; }
        [JsonPropertyName("material_reason")]
        public string MaterialReason { get; set; }
    }

    public class LevelComponent : ComponentBase
    {
        public string Name { get; set; }
        [JsonPropertyName("elevation_mm")]
        public double ElevationMm { get; set; }
    }

    public class GridComponent : ComponentBase
    {
        public string Name { get; set; }
        public Point3 Start { get; set; }
        public Point3 End { get; set; }
    }

    public class WallComponent : ComponentBase
    {
        [JsonPropertyName("base_level")]
        public string BaseLevel { get; set; }
        [JsonPropertyName("top_level")]
        public string TopLevel { get; set; }
        [JsonPropertyName("height_mm")]
        public double? HeightMm { get; set; }
        [JsonPropertyName("thickness_mm")]
        public double? ThicknessMm { get; set; }
        public string Material { get; set; }
        [JsonPropertyName("material_name")]
        public string MaterialName { get; set; }
        [JsonPropertyName("finish_materials")]
        [JsonConverter(typeof(FlexibleMaterialNameListConverter))]
        public List<string> FinishMaterials { get; set; }
        [JsonPropertyName("material_layers")]
        public List<WallMaterialLayer> MaterialLayers { get; set; }
        public Point3 Start { get; set; }
        public Point3 End { get; set; }
    }

    public class ParapetComponent : WallComponent
    {
        [JsonPropertyName("host_roof_id")]
        public string HostRoofId { get; set; }
        [JsonPropertyName("exterior_material_source_wall_id")]
        public string ExteriorMaterialSourceWallId { get; set; }
        [JsonPropertyName("exterior_material_inheritance_status")]
        public string ExteriorMaterialInheritanceStatus { get; set; }
        [JsonPropertyName("revit_base_level")]
        public string RevitBaseLevel { get; set; }
        [JsonPropertyName("bottom_relative_elevation_mm")]
        public double? BottomRelativeElevationMm { get; set; }
        [JsonPropertyName("top_relative_elevation_mm")]
        public double? TopRelativeElevationMm { get; set; }
        [JsonPropertyName("revit_bottom_elevation_mm")]
        public double? RevitBottomElevationMm { get; set; }
        [JsonPropertyName("revit_top_elevation_mm")]
        public double? RevitTopElevationMm { get; set; }
    }

    public class WallMaterialLayer
    {
        public string Role { get; set; }
        public string Scope { get; set; }
        public string Material { get; set; }
        [JsonPropertyName("material_name")]
        public string MaterialName { get; set; }
        [JsonPropertyName("thickness_mm")]
        public double? ThicknessMm { get; set; }
        public string Source { get; set; }
        public string Evidence { get; set; }
        public string Notes { get; set; }
    }

    public class FlexibleMaterialNameListConverter : JsonConverter<List<string>>
    {
        public override List<string> Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
        {
            using (JsonDocument document = JsonDocument.ParseValue(ref reader))
            {
                List<string> names = new List<string>();
                JsonElement root = document.RootElement;
                if (root.ValueKind == JsonValueKind.Array)
                {
                    foreach (JsonElement item in root.EnumerateArray())
                    {
                        AddMaterialName(names, item);
                    }
                }
                else
                {
                    AddMaterialName(names, root);
                }
                return names.Distinct(StringComparer.OrdinalIgnoreCase).ToList();
            }
        }

        public override void Write(Utf8JsonWriter writer, List<string> value, JsonSerializerOptions options)
        {
            writer.WriteStartArray();
            foreach (string item in value ?? new List<string>())
            {
                writer.WriteStringValue(item);
            }
            writer.WriteEndArray();
        }

        private static void AddMaterialName(List<string> names, JsonElement item)
        {
            string name = ExtractMaterialName(item);
            if (!string.IsNullOrWhiteSpace(name))
            {
                names.Add(name.Trim());
            }
        }

        private static string ExtractMaterialName(JsonElement item)
        {
            if (item.ValueKind == JsonValueKind.String)
            {
                return item.GetString();
            }
            if (item.ValueKind != JsonValueKind.Object)
            {
                return "";
            }

            string[] preferredNames = { "material_name", "material", "name", "label", "material_id" };
            foreach (string preferredName in preferredNames)
            {
                foreach (JsonProperty property in item.EnumerateObject())
                {
                    if (!string.Equals(property.Name, preferredName, StringComparison.OrdinalIgnoreCase))
                    {
                        continue;
                    }
                    if (property.Value.ValueKind == JsonValueKind.String)
                    {
                        return property.Value.GetString();
                    }
                    if (property.Value.ValueKind == JsonValueKind.Object)
                    {
                        string nested = ExtractMaterialName(property.Value);
                        if (!string.IsNullOrWhiteSpace(nested))
                        {
                            return nested;
                        }
                    }
                }
            }
            return "";
        }
    }

    public class FlexibleNullableConfidenceConverter : JsonConverter<double?>
    {
        public override double? Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
        {
            using (JsonDocument document = JsonDocument.ParseValue(ref reader))
            {
                return ReadConfidence(document.RootElement);
            }
        }

        public override void Write(Utf8JsonWriter writer, double? value, JsonSerializerOptions options)
        {
            if (value.HasValue)
            {
                writer.WriteNumberValue(value.Value);
            }
            else
            {
                writer.WriteNullValue();
            }
        }

        private static double? ReadConfidence(JsonElement value)
        {
            if (value.ValueKind == JsonValueKind.Null || value.ValueKind == JsonValueKind.Undefined)
            {
                return null;
            }
            if (value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out double numericValue))
            {
                return Normalize(numericValue);
            }
            if (value.ValueKind == JsonValueKind.True)
            {
                return 1.0;
            }
            if (value.ValueKind == JsonValueKind.False)
            {
                return 0.0;
            }
            if (value.ValueKind == JsonValueKind.String)
            {
                string text = (value.GetString() ?? "").Trim().ToLowerInvariant();
                if (string.IsNullOrWhiteSpace(text) || text == "null" || text == "unknown" || text == "unresolved")
                {
                    return null;
                }
                if (text == "high") return 0.9;
                if (text == "medium" || text == "moderate") return 0.7;
                if (text == "low") return 0.4;
                if (text.EndsWith("%", StringComparison.Ordinal))
                {
                    text = text.Substring(0, text.Length - 1).Trim();
                    if (double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out double percent))
                    {
                        return Normalize(percent / 100.0);
                    }
                }
                if (double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out double parsed))
                {
                    return Normalize(parsed);
                }
                return null;
            }
            if (value.ValueKind == JsonValueKind.Object)
            {
                string[] names = { "value", "confidence", "score", "level" };
                foreach (string name in names)
                {
                    foreach (JsonProperty property in value.EnumerateObject())
                    {
                        if (string.Equals(property.Name, name, StringComparison.OrdinalIgnoreCase))
                        {
                            return ReadConfidence(property.Value);
                        }
                    }
                }
            }
            return null;
        }

        private static double Normalize(double value)
        {
            if (value > 1.0 && value <= 100.0)
            {
                value /= 100.0;
            }
            return Math.Max(0.0, Math.Min(1.0, value));
        }
    }

    public class ColumnComponent : ComponentBase
    {
        public string Level { get; set; }
        [JsonPropertyName("level_id")]
        public string LevelIdAlias { get { return Level; } set { if (!string.IsNullOrWhiteSpace(value)) Level = value; } }
        [JsonPropertyName("top_level")]
        public string TopLevel { get; set; }
        public Point3 Location { get; set; }
        [JsonPropertyName("center")]
        public Point3 Center { get; set; }
        [JsonPropertyName("center_x")]
        public double? CenterX { get; set; }
        [JsonPropertyName("center_y")]
        public double? CenterY { get; set; }
        [JsonPropertyName("center_z")]
        public double? CenterZ { get; set; }
        [JsonPropertyName("column_type")]
        public string ColumnTypeAlias { get { return Type; } set { if (!string.IsNullOrWhiteSpace(value)) Type = value; } }
        [JsonPropertyName("base_z_mm")]
        public double? BaseZMm { get; set; }
        [JsonPropertyName("base_z")]
        public double? BaseZAlias { get { return BaseZMm; } set { if (value.HasValue) BaseZMm = value; } }
        [JsonPropertyName("top_z_mm")]
        public double? TopZMm { get; set; }
        [JsonPropertyName("top_z")]
        public double? TopZAlias { get { return TopZMm; } set { if (value.HasValue) TopZMm = value; } }
        [JsonPropertyName("height_mm")]
        public double? HeightMm { get; set; }
        [JsonPropertyName("height")]
        public double? HeightAlias { get { return HeightMm; } set { if (value.HasValue) HeightMm = value; } }
        [JsonPropertyName("width_mm")]
        public double? WidthMm { get; set; }
        [JsonPropertyName("width")]
        public double? WidthAlias { get { return WidthMm; } set { if (value.HasValue) WidthMm = value; } }
        [JsonPropertyName("depth_mm")]
        public double? DepthMm { get; set; }
        [JsonPropertyName("depth")]
        public double? DepthAlias { get { return DepthMm; } set { if (value.HasValue) DepthMm = value; } }
        [JsonPropertyName("diameter_mm")]
        public double? DiameterMm { get; set; }
        [JsonPropertyName("diameter")]
        public double? DiameterAlias { get { return DiameterMm; } set { if (value.HasValue) DiameterMm = value; } }
        [JsonPropertyName("rotation_angle")]
        public double? RotationAngle { get; set; }
        [JsonPropertyName("family_file")]
        public string FamilyFile { get; set; }
        [JsonPropertyName("family_name")]
        public string FamilyName { get; set; }
        [JsonPropertyName("family_type")]
        public string FamilyType { get; set; }
        public string Material { get; set; }
    }

    public class SlabComponent : ComponentBase
    {
        public string Level { get; set; }
        public List<Point3> Boundary { get; set; }
        [JsonPropertyName("thickness_mm")]
        public double? ThicknessMm { get; set; }
        [JsonPropertyName("elevation_mm")]
        public double? ElevationMm { get; set; }
        public string Material { get; set; }
        [JsonPropertyName("material_layers")]
        public List<WallMaterialLayer> MaterialLayers { get; set; }
    }

    public class FloorOpeningComponent : ComponentBase
    {
        public string Level { get; set; }
        [JsonPropertyName("host_floor_id")]
        public string HostFloorId { get; set; }
        public Point3 Location { get; set; }
        public List<Point3> Boundary { get; set; }
        [JsonPropertyName("width_mm")]
        public double? WidthMm { get; set; }
        [JsonPropertyName("depth_mm")]
        public double? DepthMm { get; set; }
    }

    public class OpeningComponent : ComponentBase
    {
        public string Level { get; set; }
        [JsonPropertyName("host_wall_id")]
        public string HostWallId { get; set; }
        public Point3 Location { get; set; }
        [JsonPropertyName("width_mm")]
        public double? WidthMm { get; set; }
        [JsonPropertyName("height_mm")]
        public double? HeightMm { get; set; }
        [JsonPropertyName("revit_height_mm")]
        public double? RevitHeightMm { get; set; }
        [JsonPropertyName("sill_height_mm")]
        public double? SillHeightMm { get; set; }
        [JsonPropertyName("revit_sill_offset_mm")]
        public double? RevitSillOffsetMm { get; set; }
        [JsonPropertyName("family_file")]
        public string FamilyFile { get; set; }
        [JsonPropertyName("family_name")]
        public string FamilyName { get; set; }
        [JsonPropertyName("family_type")]
        public string FamilyType { get; set; }
        [JsonPropertyName("open_direction")]
        public string OpenDirection { get; set; }
        [JsonPropertyName("swing_side")]
        public string SwingSide { get; set; }
        [JsonPropertyName("panel_start")]
        public Point3 PanelStart { get; set; }
        [JsonPropertyName("panel_end")]
        public Point3 PanelEnd { get; set; }
        [JsonPropertyName("panel_thickness_mm")]
        public double? PanelThicknessMm { get; set; }
        [JsonPropertyName("panel_wall_angle_deg")]
        public double? PanelWallAngleDeg { get; set; }
        public string Material { get; set; }
    }

    public class RoomComponent : ComponentBase
    {
        public string Name { get; set; }
        public string Level { get; set; }
        public Point3 Location { get; set; }
        [JsonPropertyName("space_seed_point")]
        public Point3 SpaceSeedPoint
        {
            get { return Location; }
            set { if (value != null) Location = value; }
        }
        public List<Point3> Boundary { get; set; }
        [JsonPropertyName("area_mm2")]
        public double? AreaMm2 { get; set; }
        [JsonPropertyName("number")]
        public string Number { get; set; }
        [JsonPropertyName("room_number")]
        public string RoomNumber { get; set; }
        [JsonPropertyName("space_resolution_method")]
        public string SpaceResolutionMethod { get; set; }
        [JsonPropertyName("space_binding_status")]
        public string SpaceBindingStatus { get; set; }
    }

    public class GenericModelComponent : ComponentBase
    {
        public string Name { get; set; }
        [JsonPropertyName("element_name")]
        public string ElementNameAlias { get { return Name; } set { if (!string.IsNullOrWhiteSpace(value)) Name = value; } }
        public string Level { get; set; }
        [JsonPropertyName("level_id")]
        public string LevelIdAlias { get { return Level; } set { if (!string.IsNullOrWhiteSpace(value)) Level = value; } }
        [JsonPropertyName("start_level")]
        public string StartLevel { get; set; }
        [JsonPropertyName("start_level_id")]
        public string StartLevelIdAlias { get { return StartLevel; } set { if (!string.IsNullOrWhiteSpace(value)) StartLevel = value; } }
        [JsonPropertyName("base_level")]
        public string BaseLevelAlias { get { return StartLevel; } set { if (!string.IsNullOrWhiteSpace(value)) StartLevel = value; } }
        [JsonPropertyName("end_level")]
        public string EndLevel { get; set; }
        [JsonPropertyName("end_level_id")]
        public string EndLevelIdAlias { get { return EndLevel; } set { if (!string.IsNullOrWhiteSpace(value)) EndLevel = value; } }
        [JsonPropertyName("top_level")]
        public string TopLevelAlias { get { return EndLevel; } set { if (!string.IsNullOrWhiteSpace(value)) EndLevel = value; } }
        [JsonPropertyName("stair_core_id")]
        public string StairCoreId { get; set; }
        [JsonPropertyName("stair_segment_id")]
        public string StairSegmentId { get; set; }
        [JsonPropertyName("record_role")]
        public string RecordRole { get; set; }
        [JsonPropertyName("stair_segment_number")]
        public int? StairSegmentNumber { get; set; }
        [JsonPropertyName("matched_floor_opening_id")]
        public string MatchedFloorOpeningId { get; set; }
        public Point3 Location { get; set; }
        public Point3 Start { get; set; }
        public Point3 End { get; set; }
        public List<Point3> Boundary { get; set; }
        [JsonPropertyName("boundary_points")]
        public string BoundaryPointsText { get; set; }
        [JsonPropertyName("width_mm")]
        public double? WidthMm { get; set; }
        [JsonPropertyName("width")]
        public double? WidthAlias { get { return WidthMm; } set { if (value.HasValue) WidthMm = value; } }
        [JsonPropertyName("height_mm")]
        public double? HeightMm { get; set; }
        [JsonPropertyName("height")]
        public double? HeightAlias { get { return HeightMm; } set { if (value.HasValue) HeightMm = value; } }
        [JsonPropertyName("total_rise_mm")]
        public double? TotalRiseMm { get; set; }
        [JsonPropertyName("total_rise")]
        public double? TotalRiseAlias { get { return TotalRiseMm; } set { if (value.HasValue) TotalRiseMm = value; } }
        [JsonPropertyName("total_run_mm")]
        public double? TotalRunMm { get; set; }
        [JsonPropertyName("total_run")]
        public double? TotalRunAlias { get { return TotalRunMm; } set { if (value.HasValue) TotalRunMm = value; } }
        [JsonPropertyName("riser_height_mm")]
        public double? RiserHeightMm { get; set; }
        [JsonPropertyName("riser_height")]
        public double? RiserHeightAlias { get { return RiserHeightMm; } set { if (value.HasValue) RiserHeightMm = value; } }
        [JsonPropertyName("tread_depth_mm")]
        public double? TreadDepthMm { get; set; }
        [JsonPropertyName("tread_depth")]
        public double? TreadDepthAlias { get { return TreadDepthMm; } set { if (value.HasValue) TreadDepthMm = value; } }
        [JsonPropertyName("number_of_risers")]
        public double? NumberOfRisers { get; set; }
        [JsonPropertyName("number_of_treads")]
        public double? NumberOfTreads { get; set; }
        [JsonPropertyName("run_count")]
        public double? RunCount { get; set; }
        [JsonPropertyName("risers_per_run")]
        public double? RisersPerRun { get; set; }
        [JsonPropertyName("treads_per_run")]
        public double? TreadsPerRun { get; set; }
        [JsonPropertyName("run_length_mm")]
        public double? RunLengthMm { get; set; }
        [JsonPropertyName("run_length")]
        public double? RunLengthAlias { get { return RunLengthMm; } set { if (value.HasValue) RunLengthMm = value; } }
        [JsonPropertyName("landing_length_mm")]
        public double? LandingLengthMm { get; set; }
        [JsonPropertyName("landing_length")]
        public double? LandingLengthAlias { get { return LandingLengthMm; } set { if (value.HasValue) LandingLengthMm = value; } }
        [JsonPropertyName("landing_width_mm")]
        public double? LandingWidthMm { get; set; }
        [JsonPropertyName("landing_width")]
        public double? LandingWidthAlias { get { return LandingWidthMm; } set { if (value.HasValue) LandingWidthMm = value; } }
        [JsonIgnore]
        public double? CreatedLandingLengthMm { get; set; }
        [JsonIgnore]
        public double? CreatedLandingWidthMm { get; set; }
        [JsonPropertyName("stairwell_width_mm")]
        public double? StairwellWidthMm { get; set; }
        [JsonPropertyName("stairwell_width")]
        public double? StairwellWidthAlias { get { return StairwellWidthMm; } set { if (value.HasValue) StairwellWidthMm = value; } }
        [JsonPropertyName("stair_wall_clearance_mm")]
        public double? StairWallClearanceMm { get; set; }
        [JsonPropertyName("stair_runs")]
        public List<StairRunContract> StairRuns { get; set; }
        [JsonPropertyName("stair_type")]
        public string StairType { get; set; }
        public string Direction { get; set; }
        [JsonPropertyName("start_x")]
        public double? StartX { get; set; }
        [JsonPropertyName("start_y")]
        public double? StartY { get; set; }
        [JsonPropertyName("start_z")]
        public double? StartZ { get; set; }
        [JsonPropertyName("end_x")]
        public double? EndX { get; set; }
        [JsonPropertyName("end_y")]
        public double? EndY { get; set; }
        [JsonPropertyName("end_z")]
        public double? EndZ { get; set; }
        [JsonPropertyName("manual_build_approved")]
        public bool? ManualBuildApproved { get; set; }
        [JsonPropertyName("height_needs_review")]
        public bool? HeightNeedsReview { get; set; }
        [JsonPropertyName("railing_role")]
        public string RailingRole { get; set; }
        [JsonPropertyName("source_layer")]
        public string SourceLayer { get; set; }
        [JsonPropertyName("height_source")]
        public string HeightSource { get; set; }
        [JsonPropertyName("height_status")]
        public string HeightStatus { get; set; }
        [JsonPropertyName("stair_needs_review")]
        public bool? StairNeedsReview { get; set; }
        public string Material { get; set; }
        [JsonPropertyName("material_layers")]
        public List<WallMaterialLayer> MaterialLayers { get; set; }
    }

    public class StairRunContract
    {
        [JsonPropertyName("run_id")]
        public string RunId { get; set; }
        [JsonPropertyName("sequence")]
        public int? Sequence { get; set; }
        [JsonPropertyName("run_width_mm")]
        public double? RunWidthMm { get; set; }
        [JsonPropertyName("run_length_mm")]
        public double? RunLengthMm { get; set; }
        [JsonPropertyName("location_line")]
        public StairRunLocationLine LocationLine { get; set; }
    }

    public class StairRunLocationLine
    {
        public Point3 Start { get; set; }
        public Point3 End { get; set; }
    }

    public class Point3
    {
        public double X { get; set; }
        public double Y { get; set; }
        public double Z { get; set; }
    }

    public class ValidationInfo
    {
        [JsonPropertyName("requires_human_confirmation")]
        public bool RequiresHumanConfirmation { get; set; }
        public List<ValidationIssue> Issues { get; set; }
        [JsonPropertyName("model_sequence")]
        public List<string> ModelSequence { get; set; }
    }

    public class ValidationIssue
    {
        [JsonPropertyName("component_group")]
        public string ComponentGroup { get; set; }
        [JsonPropertyName("component_id")]
        public string ComponentId { get; set; }
        public string Severity { get; set; }
        public string Message { get; set; }
    }

    public class RevitExecutionPlan
    {
        [JsonPropertyName("schema_version")]
        public string SchemaVersion { get; set; }
        [JsonPropertyName("model_sequence")]
        public List<string> ModelSequence { get; set; }
        public List<RevitOperation> Operations { get; set; }
        [JsonPropertyName("planning_notes")]
        public string PlanningNotes { get; set; }
    }

    public class RevitOperation
    {
        [JsonPropertyName("operation_id")]
        public string OperationId { get; set; }
        public string Operation { get; set; }
        [JsonPropertyName("component_group")]
        public string ComponentGroup { get; set; }
        [JsonPropertyName("component_id")]
        public string ComponentId { get; set; }
        public Dictionary<string, object> Parameters { get; set; }
        [JsonPropertyName("requires_human_confirmation")]
        public bool RequiresHumanConfirmation { get; set; }
        public string Reason { get; set; }
    }

    public class ModelingReport
    {
        private readonly List<ReportRow> rows = new List<ReportRow>();
        private readonly string inputMode;
        private readonly DateTime startedAtUtc = DateTime.UtcNow;
        private string inputFile;
        private string revitDocument;
        private string revitVersion;
        private string familyLibraryFolder;
        private bool? transactionCommitted;
        private string executionNote;
        private DateTime? completedAtUtc;

        public ModelingReport(StandardModel model)
            : this(model, "revit_model_input")
        {
        }

        public ModelingReport(StandardModel model, string inputMode)
        {
            this.inputMode = string.IsNullOrWhiteSpace(inputMode) ? "revit_model_input" : inputMode;
            AddPending("levels", model.Components.Levels);
            AddPending("grids", model.Components.Grids);
            AddPending("columns", model.Components.Columns);
            AddPending("walls", model.Components.Walls);
            AddPending("slabs", model.Components.Slabs);
            AddPending("floor_openings", model.Components.FloorOpenings);
            AddPending("doors", model.Components.Doors);
            AddPending("windows", model.Components.Windows);
            AddPending("rooms", model.Components.Rooms);
            AddPending("rooms", model.Rooms);
            AddPending("stairs", model.Components.Stairs);
            AddPending("railings", model.Components.Railings);
            AddPending("roofs", model.Components.Roofs);
            AddPending("parapets", model.Components.Parapets);
        }

        public void Success(string group, string id, long elementId, string reviewStatus, string defaults)
        {
            ReportRow row = Find(group, id, reviewStatus);
            row.Success = true;
            row.Status = "created";
            row.RevitElementId = elementId.ToString(CultureInfo.InvariantCulture);
            row.FailureReason = "";
            row.DefaultValuesUsed = defaults;
            row.AiCorrectionPlan = "";
        }

        public void Failure(string group, string id, string reviewStatus, string reason)
        {
            ReportRow row = Find(group, id, reviewStatus);
            row.Success = false;
            row.Status = "failed";
            row.FailureReason = reason;
            row.AiCorrectionPlan = BuildAiCorrectionPlan(reason);
        }

        public void Skip(string group, string id, string reviewStatus, string reason)
        {
            ReportRow row = Find(group, id, reviewStatus);
            row.Success = null;
            row.Status = "skipped";
            row.FailureReason = reason;
        }

        public int SkippedCount
        {
            get { return rows.Count(r => string.Equals(r.Status, "skipped", StringComparison.OrdinalIgnoreCase)); }
        }

        public int FailedCount
        {
            get { return rows.Count(r => string.Equals(r.Status, "failed", StringComparison.OrdinalIgnoreCase)); }
        }

        public void SetExecutionContext(string inputFile, string revitDocument, string revitVersion, string familyLibraryFolder)
        {
            this.inputFile = inputFile ?? "";
            this.revitDocument = revitDocument ?? "";
            this.revitVersion = revitVersion ?? "";
            this.familyLibraryFolder = familyLibraryFolder ?? "";
        }

        public void MarkExecutionCompleted(bool committed, string note)
        {
            transactionCommitted = committed;
            executionNote = note ?? "";
            completedAtUtc = DateTime.UtcNow;
        }

        public bool ContainsComponent(string id)
        {
            return rows.Any(row => string.Equals(row.ComponentId, id, StringComparison.OrdinalIgnoreCase));
        }

        public void Write(string folder)
        {
            Directory.CreateDirectory(folder);
            if (!completedAtUtc.HasValue)
            {
                completedAtUtc = DateTime.UtcNow;
            }
            File.WriteAllText(Path.Combine(folder, "component_details.csv"), BuildDetailsCsv(), Encoding.UTF8);
            File.WriteAllText(Path.Combine(folder, "component_statistics.csv"), BuildStatisticsCsv(), Encoding.UTF8);
            File.WriteAllText(Path.Combine(folder, "revit_execution_report.json"), BuildJson(), Encoding.UTF8);
            File.WriteAllText(Path.Combine(folder, "revit_execution_summary.txt"), BuildReadableSummary(folder), Encoding.UTF8);
        }

        public string BuildCompletionMessage(string outputFolder)
        {
            int created = rows.Count(r => string.Equals(r.Status, "created", StringComparison.OrdinalIgnoreCase));
            int skipped = rows.Count(r => string.Equals(r.Status, "skipped", StringComparison.OrdinalIgnoreCase));
            int failed = rows.Count(r => r.Success == false);
            return "Created: " + created + "\nSkipped: " + skipped + "\nFailed: " + failed +
                "\n\nFull report written to:\n" + outputFolder;
        }

        public string BuildAiRemediationSummary()
        {
            List<ReportRow> failedRows = rows
                .Where(row => string.Equals(row.Status, "failed", StringComparison.OrdinalIgnoreCase))
                .Take(8)
                .ToList();
            if (failedRows.Count == 0)
            {
                return "There are no failed items that require correction.";
            }
            return string.Join("\n\n", failedRows.Select(row =>
                row.ComponentGroup + "/" + row.ComponentId + "\nError: " + row.FailureReason + "\n" + row.AiCorrectionPlan));
        }

        public static string BuildAiCorrectionPlan(string reason)
        {
            List<string> options = GetAiCorrectionOptions(reason);
            if (options.Count == 1)
            {
                return "AI suggested action: " + options[0];
            }
            return "AI suggested action A: " + options[0] + "\nAI suggested action B: " + options[1];
        }

        public static List<string> GetAiCorrectionOptions(string reason)
        {
            string text = (reason ?? "").ToLowerInvariant();
            if (text.Contains("json") || text.Contains("schema") || text.Contains("parse") || text.Contains("格式"))
            {
                return new List<string> { "Reformat the JSON fields, quotes and schema_version through the input adapter, then generate a new final execution package." };
            }
            if (text.Contains("level") || text.Contains("标高"))
            {
                return new List<string>
                {
                    "Map each component’s level_id to an existing Revit level while preserving its relative elevation offset.",
                    "Create missing levels from component base_z values, then bind the components to those levels."
                };
            }
            if (text.Contains("host") || text.Contains("宿主") || text.Contains("wall not found") || text.Contains("floor not found"))
            {
                return new List<string>
                {
                    "Reconnect the component to an existing wall or floor using host_id, then run it again.",
                    "Find the nearest plausible host from component location and present candidate associations for review."
                };
            }
            if (text.Contains("family") || text.Contains("symbol") || text.Contains("type") || text.Contains("族"))
            {
                return new List<string>
                {
                    "Load a suitable base family from the current library, duplicate its type, set the target dimensions, then model again.",
                    "Create a same-sized DirectShape placeholder and add the missing family to the required-family list."
                };
            }
            if (text.Contains("location") || text.Contains("point") || text.Contains("boundary") || text.Contains("位置") || text.Contains("边界"))
            {
                return new List<string>
                {
                    "Rebuild component location from center_x/center_y or start/end fields and validate millimetre units.",
                    "Re-extract the closed boundary from the source CAD geometry, retaining the current record for comparison."
                };
            }
            if (text.Contains("approved") || text.Contains("rejected") || text.Contains("审批"))
            {
                return new List<string> { "Regenerate the human_approval list and write only confirmed model-ready components to approved_component_ids." };
            }
            return new List<string>
            {
                "Retain the failure record, correct the component’s missing or invalid input values, then rerun only failed components.",
                "Use a visible placeholder to complete the overall model, then replace it with a native Revit component after review."
            };
        }

        private void AddPending<T>(string group, List<T> items) where T : ComponentBase
        {
            foreach (T item in items ?? new List<T>())
            {
                if (rows.Any(row => row.ComponentGroup == group && row.ComponentId == item.Id))
                {
                    continue;
                }
                rows.Add(new ReportRow
                {
                    ComponentGroup = group,
                    ComponentId = item.Id,
                    Status = "pending",
                    ManualReviewStatus = item.ReviewStatus
                });
            }
        }

        private ReportRow Find(string group, string id, string reviewStatus)
        {
            ReportRow row = rows.FirstOrDefault(r => r.ComponentGroup == group && r.ComponentId == id);
            if (row == null)
            {
                row = new ReportRow { ComponentGroup = group, ComponentId = id, ManualReviewStatus = reviewStatus };
                rows.Add(row);
            }
            return row;
        }

        private string BuildDetailsCsv()
        {
            StringBuilder sb = new StringBuilder();
            sb.AppendLine("component_group,component_id,status,success,revit_element_id,failure_reason,ai_correction_plan,default_values_used,manual_review_status");
            foreach (ReportRow row in rows)
            {
                sb.AppendLine(string.Join(",", Escape(row.ComponentGroup), Escape(row.ComponentId), Escape(row.Status), Escape(row.SuccessString), Escape(row.RevitElementId), Escape(row.FailureReason), Escape(row.AiCorrectionPlan), Escape(row.DefaultValuesUsed), Escape(row.ManualReviewStatus)));
            }
            return sb.ToString();
        }

        private string BuildReadableSummary(string outputFolder)
        {
            StringBuilder sb = new StringBuilder();
            sb.AppendLine("AI Revit Modelling Execution Report");
            sb.AppendLine("========================");
            sb.AppendLine("Input file: " + inputFile);
            sb.AppendLine("Input mode: " + inputMode);
            sb.AppendLine("Revit project: " + revitDocument);
            sb.AppendLine("Revit version: " + revitVersion);
            sb.AppendLine("Family library: " + (string.IsNullOrWhiteSpace(familyLibraryFolder) ? "provided by the final execution package or not used" : familyLibraryFolder));
            sb.AppendLine("Started UTC: " + startedAtUtc.ToString("o", CultureInfo.InvariantCulture));
            sb.AppendLine("Completed UTC: " + (completedAtUtc ?? DateTime.UtcNow).ToString("o", CultureInfo.InvariantCulture));
            sb.AppendLine("Transaction committed: " + (transactionCommitted.HasValue ? transactionCommitted.Value.ToString() : "not recorded"));
            sb.AppendLine("Execution note: " + executionNote);
            sb.AppendLine("Report folder: " + outputFolder);
            sb.AppendLine();
            sb.AppendLine(BuildCompletionMessage(outputFolder));
            sb.AppendLine();
            sb.AppendLine("Category summary");
            foreach (IGrouping<string, ReportRow> group in rows.GroupBy(row => row.ComponentGroup))
            {
                sb.AppendLine(group.Key + ": input " + group.Count() + ", created " + group.Count(row => row.Status == "created") +
                    ", skipped " + group.Count(row => row.Status == "skipped") + ", failed " + group.Count(row => row.Status == "failed") +
                    ", pending " + group.Count(row => row.Status == "pending"));
            }
            if (FailedCount > 0)
            {
                sb.AppendLine();
                sb.AppendLine("AI suggested actions");
                sb.AppendLine(BuildAiRemediationSummary());
            }
            return sb.ToString();
        }

        private string BuildStatisticsCsv()
        {
            StringBuilder sb = new StringBuilder();
            sb.AppendLine("component_group,input_count,created_count,skipped_count,failed_count,pending_count");
            foreach (IGrouping<string, ReportRow> group in rows.GroupBy(r => r.ComponentGroup))
            {
                sb.AppendLine(string.Join(",",
                    Escape(group.Key),
                    group.Count(),
                    group.Count(r => string.Equals(r.Status, "created", StringComparison.OrdinalIgnoreCase)),
                    group.Count(r => string.Equals(r.Status, "skipped", StringComparison.OrdinalIgnoreCase)),
                    group.Count(r => string.Equals(r.Status, "failed", StringComparison.OrdinalIgnoreCase)),
                    group.Count(r => string.Equals(r.Status, "pending", StringComparison.OrdinalIgnoreCase))));
            }
            return sb.ToString();
        }

        private string BuildJson()
        {
            JsonSerializerOptions options = new JsonSerializerOptions { WriteIndented = true };
            var statistics = rows
                .GroupBy(r => r.ComponentGroup)
                .Select(group => new
                {
                    component_group = group.Key,
                    input_count = group.Count(),
                    created_count = group.Count(r => string.Equals(r.Status, "created", StringComparison.OrdinalIgnoreCase)),
                    skipped_count = group.Count(r => string.Equals(r.Status, "skipped", StringComparison.OrdinalIgnoreCase)),
                    failed_count = group.Count(r => string.Equals(r.Status, "failed", StringComparison.OrdinalIgnoreCase)),
                    pending_count = group.Count(r => string.Equals(r.Status, "pending", StringComparison.OrdinalIgnoreCase))
                })
                .ToList();

            var report = new
            {
                schema_version = "1.0",
                report_type = "revit_execution_report",
                input_mode = inputMode,
                execution_context = new
                {
                    input_file = inputFile,
                    revit_document = revitDocument,
                    revit_version = revitVersion,
                    family_library_folder = familyLibraryFolder,
                    started_at_utc = startedAtUtc.ToString("o", CultureInfo.InvariantCulture),
                    completed_at_utc = (completedAtUtc ?? DateTime.UtcNow).ToString("o", CultureInfo.InvariantCulture),
                    transaction_committed = transactionCommitted,
                    execution_note = executionNote
                },
                generated_at_utc = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
                summary = new
                {
                    input_count = rows.Count,
                    created_count = rows.Count(r => string.Equals(r.Status, "created", StringComparison.OrdinalIgnoreCase)),
                    skipped_count = rows.Count(r => string.Equals(r.Status, "skipped", StringComparison.OrdinalIgnoreCase)),
                    failed_count = rows.Count(r => string.Equals(r.Status, "failed", StringComparison.OrdinalIgnoreCase)),
                    pending_count = rows.Count(r => string.Equals(r.Status, "pending", StringComparison.OrdinalIgnoreCase))
                },
                statistics,
                rows
            };
            return JsonSerializer.Serialize(report, options);
        }

        private static string Escape(string value)
        {
            value = value ?? "";
            return "\"" + value.Replace("\"", "\"\"") + "\"";
        }
    }

    public class ReportRow
    {
        public string ComponentGroup { get; set; }
        public string ComponentId { get; set; }
        public string Status { get; set; }
        public bool? Success { get; set; }
        public string RevitElementId { get; set; }
        public string FailureReason { get; set; }
        public string AiCorrectionPlan { get; set; }
        public string DefaultValuesUsed { get; set; }
        public string ManualReviewStatus { get; set; }
        public string SuccessString { get { return Success.HasValue ? Success.Value.ToString() : ""; } }
    }
}

