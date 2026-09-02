using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Microsoft.Win32;

namespace AiRevitModeling
{
    public partial class AiRevitModelingCommand
    {
        private static readonly string EnglishFamilyLibraryRoot = Environment.GetEnvironmentVariable("AI_REVIT_ENGLISH_FAMILY_LIBRARY") ?? "";

        public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
        {
            UIApplication uiapp = commandData.Application;
            UIDocument uidoc = uiapp.ActiveUIDocument;
            Document doc = uidoc.Document;

            string jsonPath = PickJsonFile();
            if (string.IsNullOrWhiteSpace(jsonPath))
            {
                return Result.Cancelled;
            }

            RevitModelingInput input;
            try
            {
                input = LoadModelingInput(jsonPath);
            }
            catch (Exception ex)
            {
                message = ex.Message;
                ShowErrorWithAiAdvice("Input File Could Not Be Read", ex.Message);
                return Result.Failed;
            }

            StandardModel model = input.Model;
            List<string> validationErrors = ValidateModel(model);
            if (validationErrors.Count > 0)
            {
                string validationMessage = string.Join("\n", validationErrors.Take(12));
                ShowErrorWithAiAdvice("The Input Data Cannot Be Modelled Yet", validationMessage);
                return Result.Failed;
            }

            string outputFolder = Path.GetDirectoryName(jsonPath);
            string reportSource = input.IsExecutionPackage
                ? (string.IsNullOrWhiteSpace(input.Package?.PackageType) ? "final_revit_execution_package" : input.Package.PackageType)
                : "revit_model_input";
            ModelingReport report = new ModelingReport(model, reportSource);
            report.SetExecutionContext(jsonPath, doc.Title, uiapp.Application.VersionName, "");

            if (input.IsExecutionPackage)
            {
                try
                {
                    ApplyFinalExecutionFilters(model, input.Package, report);
                    LoadFamiliesFromExecutionPackage(doc, input.Package);
                }
                catch (Exception ex)
                {
                    message = ex.Message;
                    ShowErrorWithAiAdvice("Final Execution Package Validation Failed", ex.Message);
                    return Result.Failed;
                }

                if (!ShowExecutionPackagePreview(model, input.Package, report))
                {
                    return Result.Cancelled;
                }
            }
            else
            {
                string familyLibraryFolder = PickFamilyLibraryFolder(LoadLastFamilyLibraryFolder());
                if (string.IsNullOrWhiteSpace(familyLibraryFolder))
                {
                    return Result.Cancelled;
                }
                SaveLastFamilyLibraryFolder(familyLibraryFolder);
                report.SetExecutionContext(jsonPath, doc.Title, uiapp.Application.VersionName, familyLibraryFolder);
                List<FamilyLoadCandidate> familyCandidates = FindFamilyLoadCandidates(doc, model, familyLibraryFolder);
                FamilyAssignmentPlan familyPlan = BuildFamilyAssignmentPlan(doc, model, familyLibraryFolder, familyCandidates);
                familyPlan.Write(outputFolder);
                if (!ShowFamilyAssignmentReview(familyPlan))
                {
                    return Result.Cancelled;
                }

                if (LoadCandidateFamiliesIfConfirmed(doc, familyCandidates))
                {
                    ApplyAcceptedColumnFamilyCandidates(model, familyCandidates);
                }
                ComplianceReview complianceReview = BuildComplianceReview(doc, model, familyPlan);
                complianceReview.Write(outputFolder);
                if (!ShowComplianceReview(complianceReview))
                {
                    return Result.Cancelled;
                }

                if (!ShowPreview(doc, model, familyLibraryFolder, familyCandidates))
                {
                    return Result.Cancelled;
                }
            }

            try
            {
                ReportUnsupportedModelGroups(model, report);
                Dictionary<string, Level> levels;
                HashSet<string> createdFloorOpeningIds;
                using (Transaction tx = new Transaction(doc, "AI JSON automatic modeling"))
                {
                    tx.Start();
                    PrepareExistingLevelsForJson(doc, model);
                    levels = CreateLevels(doc, model, report);
                    Dictionary<string, Grid> grids = CreateGrids(doc, model, report);
                    Dictionary<string, FamilyInstance> columns = CreateColumns(doc, model, levels, report);
                    Dictionary<string, Wall> walls = CreateWalls(doc, model, levels, report);
                    CreateParapets(doc, model, levels, walls, report);
                    Dictionary<string, Floor> slabs = CreateSlabs(doc, model, levels, report, out createdFloorOpeningIds);
                    CreateOpenings(doc, model.Components.Doors, BuiltInCategory.OST_Doors, walls, levels, report);
                    CreateOpenings(doc, model.Components.Windows, BuiltInCategory.OST_Windows, walls, levels, report);
                    tx.Commit();
                }
                CreateNativeRooms(doc, model, levels, report);
                CreateStairs(doc, model, levels, createdFloorOpeningIds, report);
                CreateNativeRailings(doc, model, levels, report);
                report.MarkExecutionCompleted(
                    true,
                    report.FailedCount == 0
                        ? "Revit modeling transactions, native rooms, stair edit scopes, and native railings committed. Every executable component completed successfully."
                        : "Revit transactions committed, but one or more components failed. Review failed rows; rooms require a closed Room Bounding enclosure, stairs require their bound floor opening, and railings require a valid level and path."
                );
            }
            catch (Exception ex)
            {
                report.Failure("system", "REVIT_TRANSACTION", "", ex.Message);
                report.MarkExecutionCompleted(false, ex.Message);
                report.Write(outputFolder);
                ShowReportCompletionDialog(report, outputFolder, true);
                message = ex.Message;
                return Result.Failed;
            }

            report.Write(outputFolder);
            ShowReportCompletionDialog(report, outputFolder, report.FailedCount > 0);
            return Result.Succeeded;
        }

        private static string PickJsonFile()
        {
            System.Windows.Window window = new System.Windows.Window
            {
                Title = "Select Revit Modelling Input JSON",
                Width = 820,
                MinWidth = 680,
                SizeToContent = System.Windows.SizeToContent.Height,
                WindowStartupLocation = System.Windows.WindowStartupLocation.CenterScreen,
                ResizeMode = System.Windows.ResizeMode.CanResize,
                ShowInTaskbar = false,
                FontFamily = new System.Windows.Media.FontFamily("Microsoft YaHei UI"),
                FontSize = 14
            };

            System.Windows.Controls.Grid layout = new System.Windows.Controls.Grid
            {
                Margin = new System.Windows.Thickness(24)
            };
            layout.RowDefinitions.Add(new System.Windows.Controls.RowDefinition { Height = System.Windows.GridLength.Auto });
            layout.RowDefinitions.Add(new System.Windows.Controls.RowDefinition { Height = System.Windows.GridLength.Auto });
            layout.RowDefinitions.Add(new System.Windows.Controls.RowDefinition { Height = System.Windows.GridLength.Auto });
            layout.RowDefinitions.Add(new System.Windows.Controls.RowDefinition { Height = System.Windows.GridLength.Auto });

            System.Windows.Controls.TextBlock label = new System.Windows.Controls.TextBlock
            {
                Text = "Paste the full JSON file path, or click Browse to select a file:",
                TextWrapping = System.Windows.TextWrapping.Wrap,
                Margin = new System.Windows.Thickness(0, 0, 0, 12)
            };

            System.Windows.Controls.Grid inputRow = new System.Windows.Controls.Grid
            {
                Margin = new System.Windows.Thickness(0, 0, 0, 10)
            };
            inputRow.ColumnDefinitions.Add(new System.Windows.Controls.ColumnDefinition { Width = new System.Windows.GridLength(1, System.Windows.GridUnitType.Star) });
            inputRow.ColumnDefinitions.Add(new System.Windows.Controls.ColumnDefinition { Width = new System.Windows.GridLength(104) });
            System.Windows.Controls.TextBox pathBox = new System.Windows.Controls.TextBox
            {
                MinHeight = 36,
                Padding = new System.Windows.Thickness(8, 5, 8, 5),
                Margin = new System.Windows.Thickness(0, 0, 12, 0),
                VerticalContentAlignment = System.Windows.VerticalAlignment.Center
            };
            System.Windows.Controls.Button browse = new System.Windows.Controls.Button
            {
                Content = "Browse...",
                MinHeight = 36,
                Padding = new System.Windows.Thickness(12, 4, 12, 4)
            };
            System.Windows.Controls.TextBlock hint = new System.Windows.Controls.TextBlock
            {
                Text = "Supported files: revit_model_input.json and final_revit_execution_package.json",
                Foreground = System.Windows.Media.Brushes.DimGray,
                FontSize = 12,
                TextWrapping = System.Windows.TextWrapping.Wrap,
                Margin = new System.Windows.Thickness(0, 0, 0, 20)
            };
            System.Windows.Controls.StackPanel buttonRow = new System.Windows.Controls.StackPanel
            {
                Orientation = System.Windows.Controls.Orientation.Horizontal,
                HorizontalAlignment = System.Windows.HorizontalAlignment.Right
            };
            System.Windows.Controls.Button confirm = new System.Windows.Controls.Button
            {
                Content = "Load JSON",
                Width = 108,
                MinHeight = 38,
                IsDefault = true,
                Margin = new System.Windows.Thickness(0, 0, 12, 0)
            };
            System.Windows.Controls.Button cancel = new System.Windows.Controls.Button
            {
                Content = "Cancel",
                Width = 88,
                MinHeight = 38,
                IsCancel = true
            };

            browse.Click += (sender, args) =>
            {
                OpenFileDialog dialog = new OpenFileDialog
                {
                    Title = "Select final_revit_execution_package.json or revit_model_input.json",
                    Filter = "JSON files (*.json)|*.json|All files (*.*)|*.*"
                };
                if (dialog.ShowDialog() == true)
                {
                    pathBox.Text = dialog.FileName;
                    pathBox.CaretIndex = pathBox.Text.Length;
                }
            };
            confirm.Click += (sender, args) =>
            {
                string candidate = (pathBox.Text ?? "").Trim().Trim('"');
                if (!File.Exists(candidate) || !string.Equals(Path.GetExtension(candidate), ".json", StringComparison.OrdinalIgnoreCase))
                {
                    System.Windows.MessageBox.Show(
                        window,
                        "Please paste the full path to an existing .json file, or use Browse to select it again.",
                        "Input Path Is Unavailable",
                        System.Windows.MessageBoxButton.OK,
                        System.Windows.MessageBoxImage.Warning);
                    return;
                }
                pathBox.Text = candidate;
                window.DialogResult = true;
            };
            cancel.Click += (sender, args) => window.DialogResult = false;

            System.Windows.Controls.Grid.SetColumn(pathBox, 0);
            System.Windows.Controls.Grid.SetColumn(browse, 1);
            inputRow.Children.Add(pathBox);
            inputRow.Children.Add(browse);
            buttonRow.Children.Add(confirm);
            buttonRow.Children.Add(cancel);
            System.Windows.Controls.Grid.SetRow(label, 0);
            System.Windows.Controls.Grid.SetRow(inputRow, 1);
            System.Windows.Controls.Grid.SetRow(hint, 2);
            System.Windows.Controls.Grid.SetRow(buttonRow, 3);
            layout.Children.Add(label);
            layout.Children.Add(inputRow);
            layout.Children.Add(hint);
            layout.Children.Add(buttonRow);
            window.Content = layout;
            window.Loaded += (sender, args) => pathBox.Focus();
            return window.ShowDialog() == true ? pathBox.Text : null;
        }

        private static string PickFamilyLibraryFolder(string defaultFolder)
        {
            if (string.IsNullOrWhiteSpace(defaultFolder) || !Directory.Exists(defaultFolder))
            {
                defaultFolder = Directory.Exists(DefaultFamilyLibraryFolder)
                    ? DefaultFamilyLibraryFolder
                    : Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            }
            TaskDialog dialog = new TaskDialog("Family Library Folder");
            dialog.MainInstruction = "Confirm the Revit Family Library for This Run";
            dialog.MainContent =
                "Use the previously selected folder by default:\n" + defaultFolder;
            dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "Use This Folder");
            dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink2, "Choose Another Folder");
            dialog.CommonButtons = TaskDialogCommonButtons.Cancel;
            dialog.DefaultButton = TaskDialogResult.CommandLink1;

            TaskDialogResult result = dialog.Show();
            if (result == TaskDialogResult.CommandLink2)
            {
                using (System.Windows.Forms.FolderBrowserDialog picker = new System.Windows.Forms.FolderBrowserDialog())
                {
                    picker.Description = "Choose the family library folder that contains Revit .rfa files.";
                    picker.UseDescriptionForTitle = true;
                    picker.ShowNewFolderButton = false;
                    picker.SelectedPath = Directory.Exists(defaultFolder) ? defaultFolder : Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                    if (picker.ShowDialog() == System.Windows.Forms.DialogResult.OK && Directory.Exists(picker.SelectedPath))
                    {
                        return picker.SelectedPath;
                    }
                }
            }
            else if (result == TaskDialogResult.Cancel)
            {
                return null;
            }

            return defaultFolder;
        }

        private static string LoadLastFamilyLibraryFolder()
        {
            try
            {
                string settingsPath = GetOutputAgentSettingsPath();
                if (File.Exists(settingsPath))
                {
                    RevitOutputAgentSettings settings = JsonSerializer.Deserialize<RevitOutputAgentSettings>(File.ReadAllText(settingsPath, Encoding.UTF8));
                    if (settings != null && Directory.Exists(settings.LastFamilyLibraryFolder))
                    {
                        return settings.LastFamilyLibraryFolder;
                    }
                }
            }
            catch
            {
                // Invalid local preferences should never block modeling.
            }
            return DefaultFamilyLibraryFolder;
        }

        private static void SaveLastFamilyLibraryFolder(string folder)
        {
            try
            {
                string settingsPath = GetOutputAgentSettingsPath();
                Directory.CreateDirectory(Path.GetDirectoryName(settingsPath));
                string json = JsonSerializer.Serialize(new RevitOutputAgentSettings
                {
                    LastFamilyLibraryFolder = folder
                }, new JsonSerializerOptions { WriteIndented = true });
                File.WriteAllText(settingsPath, json, Encoding.UTF8);
            }
            catch
            {
                // Preference persistence is optional; execution can continue.
            }
        }

        private static string GetOutputAgentSettingsPath()
        {
            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "AiRevitModeling",
                "revit_output_agent_settings.json");
        }

        private static void ShowErrorWithAiAdvice(string title, string error)
        {
            List<string> options = ModelingReport.GetAiCorrectionOptions(error);
            TaskDialog dialog = new TaskDialog("AI Revit Modelling Diagnostics");
            dialog.MainInstruction = title;
            dialog.MainContent = (error ?? "Unknown error") + "\n\n" +
                (options.Count > 1 ? "AI could not determine one unique cause. Choose the option that best matches this project:" : "AI suggested correction:\n" + options[0]);
            if (options.Count > 1)
            {
                dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "Option A: " + options[0]);
                dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink2, "Option B: " + options[1]);
            }
            dialog.CommonButtons = TaskDialogCommonButtons.Close;
            TaskDialogResult result = dialog.Show();
            if (result == TaskDialogResult.CommandLink1 || result == TaskDialogResult.CommandLink2)
            {
                string selected = result == TaskDialogResult.CommandLink1 ? options[0] : options[1];
                TaskDialog.Show("AI Correction Option Selected", selected);
            }
        }

        private static void ShowReportCompletionDialog(ModelingReport report, string outputFolder, bool hasErrors)
        {
            TaskDialog dialog = new TaskDialog("AI Revit Modelling Report");
            dialog.MainInstruction = hasErrors ? "Modelling Completed with Items Requiring Attention" : "Modelling and Report Generation Completed";
            dialog.MainContent = report.BuildCompletionMessage(outputFolder);
            if (hasErrors)
            {
                dialog.ExpandedContent = report.BuildAiRemediationSummary();
            }
            dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "Open Report Folder");
            dialog.CommonButtons = TaskDialogCommonButtons.Close;
            if (dialog.Show() == TaskDialogResult.CommandLink1)
            {
                try
                {
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = "explorer.exe",
                        Arguments = "\"" + outputFolder + "\"",
                        UseShellExecute = true
                    });
                }
                catch (Exception ex)
                {
                    ShowErrorWithAiAdvice("Unable to Open Report Folder", ex.Message + "\nReport location: " + outputFolder);
                }
            }
        }

        private static RevitModelingInput LoadModelingInput(string jsonPath)
        {
            string json = File.ReadAllText(jsonPath, Encoding.UTF8);
            JsonSerializerOptions options = new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true,
                ReadCommentHandling = JsonCommentHandling.Skip,
                AllowTrailingCommas = true
            };

            using (JsonDocument document = JsonDocument.Parse(json, new JsonDocumentOptions
            {
                AllowTrailingCommas = true,
                CommentHandling = JsonCommentHandling.Skip
            }))
            {
                if (document.RootElement.ValueKind == JsonValueKind.Object && document.RootElement.TryGetProperty("source_model", out _))
                {
                    FinalRevitExecutionPackage package = JsonSerializer.Deserialize<FinalRevitExecutionPackage>(json, options);
                    if (package == null || package.SourceModel == null)
                    {
                        throw new InvalidOperationException("final_revit_execution_package.json is missing source_model.");
                    }
                    if (package.HumanApproval == null || (package.HumanApproval.ExecutionAllowed != true && package.HumanApproval.Approved != true))
                    {
                        throw new InvalidOperationException("The final execution package is not execution-allowed by the completeness policy.");
                    }

                    NormalizeModel(package.SourceModel);
                    package.ExecutionOptions = package.ExecutionOptions ?? new RevitExecutionOptions();
                    package.HumanApproval = package.HumanApproval ?? new HumanApproval();
                    return new RevitModelingInput
                    {
                        IsExecutionPackage = true,
                        Model = package.SourceModel,
                        Package = package
                    };
                }
            }

            StandardModel model = JsonSerializer.Deserialize<StandardModel>(json, options);
            if (model == null)
            {
                throw new InvalidOperationException("JSON could not be parsed into StandardModel.");
            }
            NormalizeModel(model);
            return new RevitModelingInput
            {
                IsExecutionPackage = false,
                Model = model
            };
        }

        private static void NormalizeModel(StandardModel model)
        {
            model.Components = model.Components ?? new ComponentSet();
            model.Components.Levels = model.Components.Levels ?? new List<LevelComponent>();
            model.Components.Grids = model.Components.Grids ?? new List<GridComponent>();
            model.Components.Columns = model.Components.Columns ?? new List<ColumnComponent>();
            model.Components.Walls = model.Components.Walls ?? new List<WallComponent>();
            model.Components.Slabs = model.Components.Slabs ?? new List<SlabComponent>();
            model.Components.FloorOpenings = model.Components.FloorOpenings ?? new List<FloorOpeningComponent>();
            model.Components.Doors = model.Components.Doors ?? new List<OpeningComponent>();
            model.Components.Windows = model.Components.Windows ?? new List<OpeningComponent>();
            model.Components.Rooms = model.Components.Rooms ?? new List<RoomComponent>();
            model.Components.Stairs = model.Components.Stairs ?? new List<GenericModelComponent>();
            model.Components.Railings = model.Components.Railings ?? new List<GenericModelComponent>();
            model.Components.Roofs = model.Components.Roofs ?? new List<GenericModelComponent>();
            model.Rooms = model.Rooms ?? new List<RoomComponent>();
            NormalizeColumns(model.Components.Columns);
            NormalizeGenericComponents(model.Components.Stairs);
            NormalizeGenericComponents(model.Components.Railings);
            NormalizeGenericComponents(model.Components.Roofs);
            model.Validation = model.Validation ?? new ValidationInfo();
            model.Validation.Issues = model.Validation.Issues ?? new List<ValidationIssue>();
        }

        private static void NormalizeColumns(List<ColumnComponent> items)
        {
            foreach (ColumnComponent item in items ?? new List<ColumnComponent>())
            {
                if (item.Location == null && item.Center != null)
                {
                    item.Location = item.Center;
                }
                if (item.Location == null && item.CenterX.HasValue && item.CenterY.HasValue)
                {
                    item.Location = new Point3
                    {
                        X = item.CenterX.Value,
                        Y = item.CenterY.Value,
                        Z = item.CenterZ ?? 0
                    };
                }
                if (item.HeightMm == null && item.TopZMm.HasValue && item.BaseZMm.HasValue)
                {
                    item.HeightMm = Math.Abs(item.TopZMm.Value - item.BaseZMm.Value);
                }
            }
        }

        private static void NormalizeGenericComponents(List<GenericModelComponent> items)
        {
            foreach (GenericModelComponent item in items ?? new List<GenericModelComponent>())
            {
                if (item.Start == null && item.StartX.HasValue && item.StartY.HasValue)
                {
                    item.Start = new Point3
                    {
                        X = item.StartX.Value,
                        Y = item.StartY.Value,
                        Z = item.StartZ ?? 0
                    };
                }
                if (item.End == null && item.EndX.HasValue && item.EndY.HasValue)
                {
                    item.End = new Point3
                    {
                        X = item.EndX.Value,
                        Y = item.EndY.Value,
                        Z = item.EndZ ?? 0
                    };
                }
                if ((item.Boundary == null || item.Boundary.Count == 0) && !string.IsNullOrWhiteSpace(item.BoundaryPointsText))
                {
                    item.Boundary = ParseBoundaryPoints(item.BoundaryPointsText);
                }
            }
        }

        private static List<Point3> ParseBoundaryPoints(string text)
        {
            List<Point3> points = new List<Point3>();
            try
            {
                using (JsonDocument document = JsonDocument.Parse(text))
                {
                    if (document.RootElement.ValueKind != JsonValueKind.Array)
                    {
                        return points;
                    }
                    foreach (JsonElement item in document.RootElement.EnumerateArray())
                    {
                        if (item.ValueKind == JsonValueKind.Array && item.GetArrayLength() >= 2)
                        {
                            points.Add(new Point3
                            {
                                X = item[0].GetDouble(),
                                Y = item[1].GetDouble(),
                                Z = item.GetArrayLength() >= 3 ? item[2].GetDouble() : 0
                            });
                        }
                    }
                }
            }
            catch
            {
                return new List<Point3>();
            }
            return points;
        }

        private static List<string> ValidateModel(StandardModel model)
        {
            List<string> errors = new List<string>();
            if (model.SchemaVersion != SupportedSchemaVersion)
            {
                errors.Add("Unsupported schema_version: " + model.SchemaVersion);
            }
            if (model.Components == null)
            {
                errors.Add("Missing components object.");
                return errors;
            }
            if (model.Components.Levels == null || model.Components.Levels.Count == 0)
            {
                errors.Add("At least one level is required.");
            }
            return errors;
        }

        private static void ReportUnsupportedModelGroups(StandardModel model, ModelingReport report)
        {
            SkipUnsupported(report, "roofs", model.Components.Roofs, "Roof creation is not implemented in the current Revit output agent.");
        }

        private static void SkipUnsupported<T>(ModelingReport report, string group, List<T> items, string reason) where T : ComponentBase
        {
            foreach (T item in items ?? new List<T>())
            {
                report.Skip(group, item.Id, item.ReviewStatus, reason);
            }
        }

        private static void ApplyFinalExecutionFilters(StandardModel model, FinalRevitExecutionPackage package, ModelingReport report)
        {
            HumanApproval approval = package.HumanApproval ?? new HumanApproval();
            RevitExecutionOptions options = package.ExecutionOptions ?? new RevitExecutionOptions();
            HashSet<string> approvedIds = ToIdSet(approval.ExecutionAllowedComponentIds);
            if (approvedIds.Count == 0)
            {
                approvedIds = ToIdSet(approval.ApprovedComponentIds);
            }
            HashSet<string> skippedIds = ToIdSet(approval.SkippedComponentIds);
            HashSet<string> rejectedIds = ToIdSet(approval.RejectedComponentIds);
            bool requireExplicitApproval = approvedIds.Count > 0;

            model.Components.Levels = FilterExecutable("levels", model.Components.Levels, options.CreateLevels, approvedIds, skippedIds, rejectedIds, requireExplicitApproval, report);
            model.Components.Grids = FilterExecutable("grids", model.Components.Grids, options.CreateGrids, approvedIds, skippedIds, rejectedIds, requireExplicitApproval, report);
            model.Components.Columns = FilterExecutable("columns", model.Components.Columns, options.CreateColumns, approvedIds, skippedIds, rejectedIds, requireExplicitApproval, report);
            model.Components.Walls = FilterExecutable("walls", model.Components.Walls, options.CreateWalls, approvedIds, skippedIds, rejectedIds, requireExplicitApproval, report);
            model.Components.Slabs = FilterExecutable("slabs", model.Components.Slabs, options.CreateSlabs, approvedIds, skippedIds, rejectedIds, requireExplicitApproval, report);
            model.Components.FloorOpenings = FilterExecutable("floor_openings", model.Components.FloorOpenings, options.CreateSlabs, approvedIds, skippedIds, rejectedIds, requireExplicitApproval, report);
            model.Components.Doors = FilterExecutable("doors", model.Components.Doors, options.CreateDoors, approvedIds, skippedIds, rejectedIds, requireExplicitApproval, report);
            model.Components.Windows = FilterExecutable("windows", model.Components.Windows, options.CreateWindows, approvedIds, skippedIds, rejectedIds, requireExplicitApproval, report);
            model.Components.Rooms = FilterExecutable("rooms", model.Components.Rooms, options.CreateRooms, approvedIds, skippedIds, rejectedIds, requireExplicitApproval, report);
            model.Rooms = FilterExecutable("rooms", model.Rooms, options.CreateRooms, approvedIds, skippedIds, rejectedIds, requireExplicitApproval, report);
            model.Components.Stairs = FilterExecutable("stairs", model.Components.Stairs, options.CreateStairs, approvedIds, skippedIds, rejectedIds, requireExplicitApproval, report);
            model.Components.Railings = FilterExecutable("railings", model.Components.Railings, options.CreateRailings, approvedIds, skippedIds, rejectedIds, requireExplicitApproval, report);
            model.Components.Roofs = FilterExecutable("roofs", model.Components.Roofs, options.CreateRoofs, approvedIds, skippedIds, rejectedIds, requireExplicitApproval, report);
            model.Components.Parapets = FilterExecutable("parapets", model.Components.Parapets, options.CreateParapets, approvedIds, skippedIds, rejectedIds, requireExplicitApproval, report);
            AddApprovalOnlySkips(report, skippedIds, "Skipped by human_approval.skipped_component_ids.");
            AddApprovalOnlySkips(report, rejectedIds, "Skipped by human_approval.rejected_component_ids.");
        }

        private static List<T> FilterExecutable<T>(
            string group,
            List<T> items,
            bool enabled,
            HashSet<string> approvedIds,
            HashSet<string> skippedIds,
            HashSet<string> rejectedIds,
            bool requireExplicitApproval,
            ModelingReport report) where T : ComponentBase
        {
            List<T> executable = new List<T>();
            foreach (T item in items ?? new List<T>())
            {
                string reason = GetSkipReason(item, enabled, approvedIds, skippedIds, rejectedIds, requireExplicitApproval);
                if (reason == null)
                {
                    executable.Add(item);
                }
                else
                {
                    report.Skip(group, item.Id, item.ReviewStatus, reason);
                }
            }
            return executable;
        }

        private static string GetSkipReason(
            ComponentBase item,
            bool enabled,
            HashSet<string> approvedIds,
            HashSet<string> skippedIds,
            HashSet<string> rejectedIds,
            bool requireExplicitApproval)
        {
            string id = item == null ? "" : item.Id ?? "";
            if (!enabled)
            {
                return "Skipped because this component group is disabled in execution_options.";
            }
            if (skippedIds.Contains(id))
            {
                return "Skipped by human_approval.skipped_component_ids.";
            }
            if (rejectedIds.Contains(id) || IsRejected(item))
            {
                return GetSkipReason(item) ?? "Skipped because the component was rejected.";
            }
            if (NeedsReview(item) && !approvedIds.Contains(id))
            {
                return "Skipped because review_status is needs_review and the component is not listed as execution-allowed.";
            }
            if (requireExplicitApproval && !approvedIds.Contains(id))
            {
                return "Skipped because it is not listed in human_approval.execution_allowed_component_ids.";
            }
            return null;
        }

        private static HashSet<string> ToIdSet(List<string> ids)
        {
            return new HashSet<string>((ids ?? new List<string>()).Where(id => !string.IsNullOrWhiteSpace(id)), StringComparer.OrdinalIgnoreCase);
        }

        private static void AddApprovalOnlySkips(ModelingReport report, HashSet<string> ids, string reason)
        {
            foreach (string id in ids ?? new HashSet<string>())
            {
                if (!report.ContainsComponent(id))
                {
                    report.Skip(GuessComponentGroupFromId(id), id, "", reason + " The component is not present in source_model, so it was probably filtered out before final execution.");
                }
            }
        }

        private static string GuessComponentGroupFromId(string id)
        {
            string value = (id ?? "").Trim().ToUpperInvariant();
            if (value.StartsWith("LEVEL")) return "levels";
            if (value.StartsWith("GRID")) return "grids";
            if (value.StartsWith("COLUMN")) return "columns";
            if (value.StartsWith("WALL")) return "walls";
            if (value.StartsWith("FLOOROPENING")) return "floor_openings";
            if (value.StartsWith("FLOOR") || value.StartsWith("SLAB")) return "slabs";
            if (value.StartsWith("DOOR")) return "doors";
            if (value.StartsWith("WINDOW")) return "windows";
            if (value.StartsWith("ROOM")) return "rooms";
            if (value.StartsWith("STAIR")) return "stairs";
            if (value.StartsWith("RAILING")) return "railings";
            if (value.StartsWith("ROOF")) return "roofs";
            return "approval_filtered";
        }

        private static void LoadFamiliesFromExecutionPackage(Document doc, FinalRevitExecutionPackage package)
        {
            FamilyAssignmentPlan plan = package.FamilyAssignmentPlan;
            List<string> filePaths = new List<string>();
            foreach (FamilyAssignmentRow row in plan == null ? new List<FamilyAssignmentRow>() : plan.Rows ?? new List<FamilyAssignmentRow>())
            {
                foreach (FamilyAssignmentCandidate candidate in row.Candidates ?? new List<FamilyAssignmentCandidate>())
                {
                    string preferredPath = ResolveEnglishFamilyPath(candidate.FilePath);
                    if (!string.IsNullOrWhiteSpace(preferredPath) && File.Exists(preferredPath))
                    {
                        filePaths.Add(preferredPath);
                    }
                }
            }

            filePaths = filePaths.Distinct(StringComparer.OrdinalIgnoreCase).Take(12).ToList();
            if (filePaths.Count == 0)
            {
                return;
            }

            using (Transaction tx = new Transaction(doc, "Load approved AI families"))
            {
                tx.Start();
                foreach (string filePath in filePaths)
                {
                    try
                    {
                        Family loadedFamily;
                        doc.LoadFamily(filePath, out loadedFamily);
                        RenameLoadedFamilyForEnglishWorkflow(loadedFamily, filePath);
                    }
                    catch
                    {
                        // Missing families are surfaced later as component failures.
                    }
                }
                tx.Commit();
            }
        }

        private static string ResolveEnglishFamilyPath(string originalPath)
        {
            if (string.IsNullOrWhiteSpace(originalPath) || !File.Exists(originalPath))
            {
                return originalPath;
            }

            string chineseLibraryRoot = Environment.GetEnvironmentVariable("AI_REVIT_FAMILY_LIBRARY") ?? "";
            if (!originalPath.StartsWith(chineseLibraryRoot, StringComparison.OrdinalIgnoreCase))
            {
                return originalPath;
            }

            string relativePath = originalPath.Substring(chineseLibraryRoot.Length).TrimStart(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string preferredPath = Path.Combine(EnglishFamilyLibraryRoot, relativePath);
            if (File.Exists(preferredPath))
            {
                return preferredPath;
            }

            string folder = Path.GetDirectoryName(preferredPath);
            string sourceFileName = Path.GetFileNameWithoutExtension(originalPath);
            string englishFile = EnglishFamilyFileName(sourceFileName);
            string mappedPath = string.IsNullOrWhiteSpace(folder) || string.IsNullOrWhiteSpace(englishFile)
                ? null
                : Path.Combine(folder, englishFile + ".rfa");
            return !string.IsNullOrWhiteSpace(mappedPath) && File.Exists(mappedPath) ? mappedPath : originalPath;
        }

        private static string EnglishFamilyFileName(string sourceName)
        {
            Dictionary<string, string> names = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                { "倒角柱", "Chamfered Column" }, { "圆柱", "Round Column" }, { "矩形柱", "Rectangular Column" },
                { "单嵌板亮窗玻璃门 2", "Single Panel Glass Door with Transom 2" }, { "单嵌板木门 1", "Single Panel Wood Door 1" },
                { "单嵌板木门 18 百叶窗式", "Single Panel Louvered Wood Door 18" }, { "单嵌板格栅门", "Single Panel Grille Door" },
                { "单嵌板玻璃门 1", "Single Panel Glass Door 1" }, { "单嵌板连窗玻璃门 1", "Single Panel Glass Door with Sidelight 1" },
                { "单嵌板镶玻璃门 1", "Single Panel Glazed Door 1" }, { "单嵌板镶玻璃门 12 - 带圆顶", "Single Panel Glazed Door 12 - Arched Top" },
                { "单扇推拉门 - 墙中1", "Single Sliding Door - In Wall 1" }, { "双扇推拉门1", "Double Sliding Door 1" },
                { "双面嵌板木门 1", "Double Panel Wood Door 1" }, { "双面嵌板格栅门 1", "Double Panel Grille Door 1" },
                { "双面嵌板玻璃门", "Double Panel Glass Door" }, { "双面嵌板镶玻璃门 3 - 带亮窗", "Double Panel Glazed Door 3 - With Transom" },
                { "四扇推拉门 1", "Four Panel Sliding Door 1" }, { "L型转角扁钢栏杆", "L-Shaped Angle Steel Railing" },
                { "木栏杆1", "Timber Railing 1" }, { "玻璃栏板 - 带边槽钢", "Glass Balustrade - Edge Channel" },
                { "金属栏杆", "Metal Railing" }, { "钢筋混凝土栏杆", "Reinforced Concrete Railing" },
                { "上下拉窗1", "Double Hung Window 1" }, { "上下拉窗2 - 带贴面", "Double Hung Window 2 - With Trim" },
                { "上下拉窗3 - 带贴面", "Double Hung Window 3 - With Trim" }, { "单扇平开窗1 - 带贴面", "Single Casement Window 1 - With Trim" },
                { "单扇平开窗2 - 带贴面", "Single Casement Window 2 - With Trim" }, { "双扇平开 - 带贴面", "Double Casement Window - With Trim" },
                { "推拉窗1 - 带贴面", "Sliding Window 1 - With Trim" }, { "推拉窗2 - 带贴面", "Sliding Window 2 - With Trim" },
                { "推拉窗3 - 带贴面", "Sliding Window 3 - With Trim" }, { "推拉窗4 - 带贴面", "Sliding Window 4 - With Trim" },
                { "推拉窗5 - 带贴面", "Sliding Window 5 - With Trim" }, { "推拉窗6", "Sliding Window 6" }, { "推拉窗7 - 带贴面", "Sliding Window 7 - With Trim" }
            };
            string result;
            return names.TryGetValue(sourceName ?? "", out result) ? result : null;
        }

        private static void RenameLoadedFamilyForEnglishWorkflow(Family family, string filePath)
        {
            if (family == null || string.IsNullOrWhiteSpace(filePath))
            {
                return;
            }

            string englishName = Path.GetFileNameWithoutExtension(filePath);
            if (string.IsNullOrWhiteSpace(englishName) || string.Equals(family.Name, englishName, StringComparison.Ordinal))
            {
                return;
            }

            try
            {
                family.Name = englishName;
            }
            catch
            {
                // A pre-existing protected family may retain its current Revit name.
                // The English file path is still used and reported for this run.
            }
        }

        private static bool ShowExecutionPackagePreview(StandardModel model, FinalRevitExecutionPackage package, ModelingReport report)
        {
            bool isTemporaryPreview = string.Equals(package?.PackageType, "preview_revit_execution_package", StringComparison.OrdinalIgnoreCase);
            string preview =
                "Base: Levels " + Count(model.Components.Levels) + ", Grids " + Count(model.Components.Grids) + "\n" +
                "Primary: Columns " + Count(model.Components.Columns) + ", Walls " + Count(model.Components.Walls) + ", Slabs " + Count(model.Components.Slabs) + "\n" +
                "Secondary: Openings " + Count(model.Components.FloorOpenings) + ", Doors " + Count(model.Components.Doors) + ", Windows " + Count(model.Components.Windows) + ", Stairs " + Count(model.Components.Stairs) + "\n" +
                "Skipped after policy filtering: " + report.SkippedCount + "\n\n" +
                (isTemporaryPreview
                    ? "This package contains Level 3 temporary preview items. Confirmation creates a preview model only; it is not final human approval."
                    : "After confirmation, final Revit API modelling will start immediately.");

            TaskDialog dialog = new TaskDialog(isTemporaryPreview ? "AI Revit Temporary Preview" : "AI Revit Final Execution");
            dialog.MainInstruction = isTemporaryPreview ? "Confirm Temporary Preview Modelling" : "Confirm Final Modelling Execution";
            dialog.MainContent = preview;
            dialog.CommonButtons = TaskDialogCommonButtons.Ok | TaskDialogCommonButtons.Cancel;
            dialog.DefaultButton = TaskDialogResult.Cancel;
            return dialog.Show() == TaskDialogResult.Ok;
        }

        private static bool ShowPreview(Document doc, StandardModel model, string familyLibraryFolder, List<FamilyLoadCandidate> familyCandidates)
        {
            int issueCount = model.Validation == null || model.Validation.Issues == null ? 0 : model.Validation.Issues.Count;
            string preview =
                "Base: Levels " + Count(model.Components.Levels) + ", Grids " + Count(model.Components.Grids) + "\n" +
                "Primary: Columns " + Count(model.Components.Columns) + ", Walls " + Count(model.Components.Walls) + ", Slabs " + Count(model.Components.Slabs) + "\n" +
                "Secondary: Openings " + Count(model.Components.FloorOpenings) + ", Doors " + Count(model.Components.Doors) + ", Windows " + Count(model.Components.Windows) + ", Stairs " + Count(model.Components.Stairs) + "\n\n" +
                "Ready to model: " + CountModelableItems(model) + ", Requires review: " + CountReviewItems(model) + ", Data issues: " + issueCount + "\n" +
                "Family library: " + familyLibraryFolder;

            TaskDialog dialog = new TaskDialog("AI Revit Modeling Preview");
            dialog.MainInstruction = "Confirm Modelling Execution";
            dialog.MainContent = preview;
            dialog.CommonButtons = TaskDialogCommonButtons.Ok | TaskDialogCommonButtons.Cancel;
            dialog.DefaultButton = TaskDialogResult.Cancel;
            return dialog.Show() == TaskDialogResult.Ok;
        }

        private static string BuildFamilyPreview(Document doc, StandardModel model, string familyLibraryFolder, List<FamilyLoadCandidate> familyCandidates)
        {
            StringBuilder sb = new StringBuilder();
            sb.AppendLine("Family readiness:");
            sb.AppendLine("Family library folder: " + (familyLibraryFolder ?? "(not selected)"));
            AppendColumnPreviewGroup(sb, doc, model.Components.Columns);
            AppendSlabPreviewGroup(sb, doc, model.Components.Slabs, model.Components.FloorOpenings);
            AppendFamilyPreviewGroup(sb, doc, "Doors", BuiltInCategory.OST_Doors, model.Components.Doors, familyCandidates);
            AppendFamilyPreviewGroup(sb, doc, "Windows", BuiltInCategory.OST_Windows, model.Components.Windows, familyCandidates);
            return sb.ToString();
        }

    }

    public class RevitModelingInput
    {
        public bool IsExecutionPackage { get; set; }
        public StandardModel Model { get; set; }
        public FinalRevitExecutionPackage Package { get; set; }
    }

    public class RevitOutputAgentSettings
    {
        public string LastFamilyLibraryFolder { get; set; }
    }

    public class FinalRevitExecutionPackage
    {
        [JsonPropertyName("schema_version")]
        public string SchemaVersion { get; set; }

        [JsonPropertyName("package_type")]
        public string PackageType { get; set; }

        [JsonPropertyName("source_model")]
        public StandardModel SourceModel { get; set; }

        [JsonPropertyName("family_assignment_plan")]
        public FamilyAssignmentPlan FamilyAssignmentPlan { get; set; }

        [JsonPropertyName("compliance_review_report")]
        public ComplianceReview ComplianceReviewReport { get; set; }

        [JsonPropertyName("human_approval")]
        public HumanApproval HumanApproval { get; set; }

        [JsonPropertyName("execution_options")]
        public RevitExecutionOptions ExecutionOptions { get; set; }

        [JsonPropertyName("gate_summary")]
        public Dictionary<string, object> GateSummary { get; set; }
    }

    public class HumanApproval
    {
        public bool Approved { get; set; }

        [JsonPropertyName("system_ready")]
        public bool SystemReady { get; set; }

        [JsonPropertyName("human_approved")]
        public bool HumanApproved { get; set; }

        [JsonPropertyName("execution_allowed")]
        public bool ExecutionAllowed { get; set; }

        [JsonPropertyName("execution_allowed_component_ids")]
        public List<string> ExecutionAllowedComponentIds { get; set; }

        [JsonPropertyName("approved_component_ids")]
        public List<string> ApprovedComponentIds { get; set; }

        [JsonPropertyName("skipped_component_ids")]
        public List<string> SkippedComponentIds { get; set; }

        [JsonPropertyName("rejected_component_ids")]
        public List<string> RejectedComponentIds { get; set; }

        public string Notes { get; set; }
    }

    public class RevitExecutionOptions
    {
        [JsonPropertyName("create_levels")]
        public bool CreateLevels { get; set; } = true;

        [JsonPropertyName("create_grids")]
        public bool CreateGrids { get; set; } = true;

        [JsonPropertyName("create_columns")]
        public bool CreateColumns { get; set; } = true;

        [JsonPropertyName("create_walls")]
        public bool CreateWalls { get; set; } = true;

        [JsonPropertyName("create_slabs")]
        public bool CreateSlabs { get; set; } = true;

        [JsonPropertyName("create_doors")]
        public bool CreateDoors { get; set; } = true;

        [JsonPropertyName("create_windows")]
        public bool CreateWindows { get; set; } = true;

        [JsonPropertyName("create_rooms")]
        public bool CreateRooms { get; set; } = true;

        [JsonPropertyName("create_stairs")]
        public bool CreateStairs { get; set; } = true;

        [JsonPropertyName("create_railings")]
        public bool CreateRailings { get; set; } = false;

        [JsonPropertyName("create_roofs")]
        public bool CreateRoofs { get; set; } = false;

        [JsonPropertyName("create_parapets")]
        public bool CreateParapets { get; set; } = true;
    }
}

