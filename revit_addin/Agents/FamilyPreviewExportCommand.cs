using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace AiRevitModeling
{
    [Transaction(TransactionMode.Manual)]
    public class FamilyPreviewExportCommand : IExternalCommand
    {
        private static readonly string DefaultOutputFolder = Environment.GetEnvironmentVariable("AI_FAMILY_PREVIEW_OUTPUT") ?? "";

        public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
        {
            UIApplication uiapp = commandData.Application;
            string familyRoot = PickFamilyLibraryFolder();
            if (string.IsNullOrWhiteSpace(familyRoot))
            {
                return Result.Cancelled;
            }

            string outputFolder = PickOutputFolder();
            if (string.IsNullOrWhiteSpace(outputFolder))
            {
                return Result.Cancelled;
            }
            Directory.CreateDirectory(outputFolder);

            FamilyPreviewIndex index = new FamilyPreviewIndex
            {
                SchemaVersion = "1.0",
                FamilyLibraryFolder = familyRoot,
                OutputFolder = outputFolder,
                GeneratedAt = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture),
                Previews = new List<FamilyPreviewEntry>(),
                Failures = new List<FamilyPreviewFailure>()
            };

            List<string> familyFiles = Directory.GetFiles(familyRoot, "*.rfa", SearchOption.AllDirectories)
                .OrderBy(path => path)
                .ToList();

            foreach (string familyFile in familyFiles)
            {
                ExportFamilyPreviewImages(uiapp, familyRoot, familyFile, outputFolder, index);
            }

            string indexPath = Path.Combine(outputFolder, "family_preview_index.json");
            JsonSerializerOptions options = new JsonSerializerOptions { WriteIndented = true };
            File.WriteAllText(indexPath, JsonSerializer.Serialize(index, options), Encoding.UTF8);

            TaskDialog.Show(
                "AI Family Preview Export",
                "Family preview export finished.\n\nFamilies scanned: " + familyFiles.Count.ToString(CultureInfo.InvariantCulture) +
                "\nPreview images: " + index.Previews.Count.ToString(CultureInfo.InvariantCulture) +
                "\nFailures: " + index.Failures.Count.ToString(CultureInfo.InvariantCulture) +
                "\n\nOutput folder:\n" + outputFolder);

            return Result.Succeeded;
        }

        private static string PickFamilyLibraryFolder()
        {
            using (System.Windows.Forms.FolderBrowserDialog picker = new System.Windows.Forms.FolderBrowserDialog())
            {
                picker.Description = "Select the family library folder. All .rfa files under this folder will be exported.";
                string configuredFamilyLibrary = Environment.GetEnvironmentVariable("AI_REVIT_FAMILY_LIBRARY") ?? "";
                picker.SelectedPath = Directory.Exists(configuredFamilyLibrary)
                    ? configuredFamilyLibrary
                    : Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                picker.ShowNewFolderButton = false;

                return picker.ShowDialog() == System.Windows.Forms.DialogResult.OK && Directory.Exists(picker.SelectedPath)
                    ? picker.SelectedPath
                    : null;
            }
        }

        private static string PickOutputFolder()
        {
            using (System.Windows.Forms.FolderBrowserDialog picker = new System.Windows.Forms.FolderBrowserDialog())
            {
                picker.Description = "Select the output folder for exported family preview images and family_preview_index.json.";
                picker.SelectedPath = Directory.Exists(DefaultOutputFolder)
                    ? DefaultOutputFolder
                    : Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                picker.ShowNewFolderButton = true;

                return picker.ShowDialog() == System.Windows.Forms.DialogResult.OK && !string.IsNullOrWhiteSpace(picker.SelectedPath)
                    ? picker.SelectedPath
                    : null;
            }
        }

        private static void ExportFamilyPreviewImages(UIApplication uiapp, string familyRoot, string familyFile, string outputFolder, FamilyPreviewIndex index)
        {
            Document familyDoc = null;
            try
            {
                familyDoc = uiapp.Application.OpenDocumentFile(familyFile);
                List<FamilySymbol> symbols = new FilteredElementCollector(familyDoc)
                    .OfClass(typeof(FamilySymbol))
                    .Cast<FamilySymbol>()
                    .OrderBy(symbol => symbol.FamilyName)
                    .ThenBy(symbol => symbol.Name)
                    .ToList();

                if (symbols.Count == 0)
                {
                    index.Failures.Add(new FamilyPreviewFailure
                    {
                        FamilyFile = familyFile,
                        RelativePath = MakeRelativePath(familyRoot, familyFile),
                        Reason = "No FamilySymbol preview type was found in this .rfa file."
                    });
                    return;
                }

                foreach (FamilySymbol symbol in symbols)
                {
                    ExportSymbolPreview(familyDoc, familyRoot, familyFile, outputFolder, symbol, index);
                }
            }
            catch (Exception ex)
            {
                index.Failures.Add(new FamilyPreviewFailure
                {
                    FamilyFile = familyFile,
                    RelativePath = MakeRelativePath(familyRoot, familyFile),
                    Reason = ex.Message
                });
            }
            finally
            {
                if (familyDoc != null)
                {
                    try
                    {
                        familyDoc.Close(false);
                    }
                    catch
                    {
                    }
                }
            }
        }

        private static void ExportSymbolPreview(Document familyDoc, string familyRoot, string familyFile, string outputFolder, FamilySymbol symbol, FamilyPreviewIndex index)
        {
            Bitmap preview = null;
            try
            {
                preview = symbol.GetPreviewImage(new Size(512, 512));
                if (preview == null)
                {
                    if (TryExportFamilyViewPreview(familyDoc, familyRoot, familyFile, outputFolder, symbol, index))
                    {
                        return;
                    }

                    AddFailure(index, familyRoot, familyFile, symbol, "Revit returned no preview image and no exportable family view was found.");
                    return;
                }

                string relativePath = MakeRelativePath(familyRoot, familyFile);
                string categoryFolder = GuessPreviewCategoryFolder(relativePath, symbol);
                string imageFolder = Path.Combine(outputFolder, categoryFolder);
                Directory.CreateDirectory(imageFolder);

                string imageFileName = BuildPreviewImageFileName(relativePath, symbol);
                string imagePath = Path.Combine(imageFolder, imageFileName);
                preview.Save(imagePath, ImageFormat.Png);

                index.Previews.Add(new FamilyPreviewEntry
                {
                    PreviewId = Path.GetFileNameWithoutExtension(imageFileName),
                    FamilyFile = familyFile,
                    RelativeFamilyPath = relativePath,
                    FamilyName = symbol.FamilyName,
                    TypeName = symbol.Name,
                    Category = categoryFolder,
                    ImagePath = imagePath,
                    RelativeImagePath = MakeRelativePath(outputFolder, imagePath),
                    WidthPx = preview.Width,
                    HeightPx = preview.Height,
                    ExportMethod = "symbol_preview_image",
                    ViewName = ""
                });
            }
            catch (Exception ex)
            {
                if (!TryExportFamilyViewPreview(familyDoc, familyRoot, familyFile, outputFolder, symbol, index))
                {
                    AddFailure(index, familyRoot, familyFile, symbol, ex.Message);
                }
            }
            finally
            {
                if (preview != null)
                {
                    preview.Dispose();
                }
            }
        }

        private static bool TryExportFamilyViewPreview(Document familyDoc, string familyRoot, string familyFile, string outputFolder, FamilySymbol symbol, FamilyPreviewIndex index)
        {
            try
            {
                View view = FindBestExportView(familyDoc);
                if (view == null)
                {
                    return false;
                }

                string relativePath = MakeRelativePath(familyRoot, familyFile);
                string categoryFolder = GuessPreviewCategoryFolder(relativePath, symbol);
                string imageFolder = Path.Combine(outputFolder, categoryFolder);
                Directory.CreateDirectory(imageFolder);

                string imageFileName = BuildPreviewImageFileName(relativePath, symbol);
                string imagePath = Path.Combine(imageFolder, imageFileName);
                string exportBasePath = Path.Combine(imageFolder, Path.GetFileNameWithoutExtension(imageFileName));

                DeleteExistingExportImages(imageFolder, Path.GetFileNameWithoutExtension(imageFileName));

                ImageExportOptions options = new ImageExportOptions
                {
                    ExportRange = ExportRange.SetOfViews,
                    FilePath = exportBasePath,
                    FitDirection = FitDirectionType.Horizontal,
                    HLRandWFViewsFileType = ImageFileType.PNG,
                    ImageResolution = ImageResolution.DPI_150,
                    PixelSize = 512,
                    ShadowViewsFileType = ImageFileType.PNG,
                    ZoomType = ZoomFitType.FitToPage
                };
                options.SetViewsAndSheets(new List<ElementId> { view.Id });
                familyDoc.ExportImage(options);

                string exportedImage = FindNewestExportedImage(imageFolder, Path.GetFileNameWithoutExtension(imageFileName));
                if (string.IsNullOrWhiteSpace(exportedImage) || !File.Exists(exportedImage))
                {
                    return false;
                }

                if (!string.Equals(exportedImage, imagePath, StringComparison.OrdinalIgnoreCase))
                {
                    if (File.Exists(imagePath))
                    {
                        File.Delete(imagePath);
                    }
                    File.Move(exportedImage, imagePath);
                }

                index.Previews.Add(new FamilyPreviewEntry
                {
                    PreviewId = Path.GetFileNameWithoutExtension(imageFileName),
                    FamilyFile = familyFile,
                    RelativeFamilyPath = relativePath,
                    FamilyName = symbol.FamilyName,
                    TypeName = symbol.Name,
                    Category = categoryFolder,
                    ImagePath = imagePath,
                    RelativeImagePath = MakeRelativePath(outputFolder, imagePath),
                    WidthPx = 512,
                    HeightPx = 512,
                    ExportMethod = "family_view_export",
                    ViewName = view.Name
                });
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static View FindBestExportView(Document familyDoc)
        {
            View view3d = new FilteredElementCollector(familyDoc)
                .OfClass(typeof(View3D))
                .Cast<View3D>()
                .Where(view => !view.IsTemplate && view.CanBePrinted)
                .OrderBy(view => view.Name)
                .FirstOrDefault();
            if (view3d != null)
            {
                return view3d;
            }

            return new FilteredElementCollector(familyDoc)
                .OfClass(typeof(View))
                .Cast<View>()
                .Where(view => !view.IsTemplate && view.CanBePrinted && view.ViewType != ViewType.Schedule && view.ViewType != ViewType.Legend)
                .OrderBy(view => view.ViewType == ViewType.FloorPlan ? 0 : 1)
                .ThenBy(view => view.Name)
                .FirstOrDefault();
        }

        private static void DeleteExistingExportImages(string folder, string baseName)
        {
            foreach (string file in Directory.GetFiles(folder, baseName + "*.png"))
            {
                try
                {
                    File.Delete(file);
                }
                catch
                {
                }
            }
        }

        private static string FindNewestExportedImage(string folder, string baseName)
        {
            return Directory.GetFiles(folder, baseName + "*.png")
                .OrderByDescending(File.GetLastWriteTimeUtc)
                .FirstOrDefault();
        }

        private static void AddFailure(FamilyPreviewIndex index, string familyRoot, string familyFile, FamilySymbol symbol, string reason)
        {
            index.Failures.Add(new FamilyPreviewFailure
            {
                FamilyFile = familyFile,
                RelativePath = MakeRelativePath(familyRoot, familyFile),
                FamilyName = symbol.FamilyName,
                TypeName = symbol.Name,
                Reason = reason
            });
        }

        private static string GuessPreviewCategoryFolder(string relativePath, FamilySymbol symbol)
        {
            string text = ((relativePath ?? "") + " " + (symbol.FamilyName ?? "") + " " + (symbol.Name ?? "")).ToLowerInvariant();
            if (ContainsAny(text, "stair", "stairs", "楼梯")) return "stairs";
            if (ContainsAny(text, "column", "columns", "柱")) return "columns";
            if (ContainsAny(text, "wall", "walls", "墙")) return "walls";
            if (ContainsAny(text, "door", "doors", "门")) return "doors";
            if (ContainsAny(text, "window", "windows", "窗")) return "windows";
            if (ContainsAny(text, "rail", "railing", "railings", "handrail", "guardrail", "扶手", "栏杆")) return "railings";
            return "others";
        }

        private static bool ContainsAny(string text, params string[] values)
        {
            return values.Any(value => text.Contains(value));
        }

        private static string BuildPreviewImageFileName(string relativeFamilyPath, FamilySymbol symbol)
        {
            string familyPart = Path.GetFileNameWithoutExtension(relativeFamilyPath);
            string name = SanitizeFileName(familyPart + "__" + symbol.Name);
            if (name.Length > 120)
            {
                name = name.Substring(0, 120);
            }
            return name + ".png";
        }

        private static string SanitizeFileName(string value)
        {
            string result = value ?? "family_preview";
            foreach (char c in Path.GetInvalidFileNameChars())
            {
                result = result.Replace(c, '_');
            }
            return result.Replace(' ', '_');
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
    }

    public class FamilyPreviewIndex
    {
        [JsonPropertyName("schema_version")]
        public string SchemaVersion { get; set; }
        [JsonPropertyName("family_library_folder")]
        public string FamilyLibraryFolder { get; set; }
        [JsonPropertyName("output_folder")]
        public string OutputFolder { get; set; }
        [JsonPropertyName("generated_at")]
        public string GeneratedAt { get; set; }
        [JsonPropertyName("previews")]
        public List<FamilyPreviewEntry> Previews { get; set; }
        [JsonPropertyName("failures")]
        public List<FamilyPreviewFailure> Failures { get; set; }
    }

    public class FamilyPreviewEntry
    {
        [JsonPropertyName("preview_id")]
        public string PreviewId { get; set; }
        [JsonPropertyName("family_file")]
        public string FamilyFile { get; set; }
        [JsonPropertyName("relative_family_path")]
        public string RelativeFamilyPath { get; set; }
        [JsonPropertyName("family_name")]
        public string FamilyName { get; set; }
        [JsonPropertyName("type_name")]
        public string TypeName { get; set; }
        [JsonPropertyName("category")]
        public string Category { get; set; }
        [JsonPropertyName("image_path")]
        public string ImagePath { get; set; }
        [JsonPropertyName("relative_image_path")]
        public string RelativeImagePath { get; set; }
        [JsonPropertyName("width_px")]
        public int WidthPx { get; set; }
        [JsonPropertyName("height_px")]
        public int HeightPx { get; set; }
        [JsonPropertyName("export_method")]
        public string ExportMethod { get; set; }
        [JsonPropertyName("view_name")]
        public string ViewName { get; set; }
    }

    public class FamilyPreviewFailure
    {
        [JsonPropertyName("family_file")]
        public string FamilyFile { get; set; }
        [JsonPropertyName("relative_path")]
        public string RelativePath { get; set; }
        [JsonPropertyName("family_name")]
        public string FamilyName { get; set; }
        [JsonPropertyName("type_name")]
        public string TypeName { get; set; }
        [JsonPropertyName("reason")]
        public string Reason { get; set; }
    }
}

