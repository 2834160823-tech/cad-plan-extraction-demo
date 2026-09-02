from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from cad_plan_demo.dxf_parser import CadEntity
from cad_plan_demo.frames import detect_drawing_frames, is_frame_layer
from cad_plan_demo.notes import analyze_text_and_notes
from cad_plan_demo.standard_export import infer_level_from_title, write_standard_project_outputs


class EnglishDxfRecognitionTests(unittest.TestCase):
    def test_drawing_border_layer_is_recognized_as_frame(self) -> None:
        entities = [
            CadEntity(
                "LWPOLYLINE",
                "Drawing Border",
                {
                    "points": [(0, 0), (12000, 0), (12000, 8000), (0, 8000)],
                    "closed": True,
                },
            )
        ]

        frames = detect_drawing_frames(entities)

        self.assertEqual(1, len(frames))
        self.assertEqual("Drawing Border", frames[0].layer)
        self.assertTrue(is_frame_layer("DRAWING_BORDER"))

    def test_ground_floor_plan_keeps_title_and_maps_to_first_level(self) -> None:
        entities = [
            CadEntity(
                "TEXT",
                "Drawing Title",
                {"point": (1000, 1000), "height": 300, "text": "Ground Floor Plan"},
            )
        ]

        notes = analyze_text_and_notes(entities, {"walls": 4, "openings": 2, "axes": 2})
        level = infer_level_from_title(notes["drawing_title"])

        self.assertEqual("architectural_plan", notes["drawing_type"])
        self.assertEqual("Ground Floor Plan", notes["drawing_title"])
        self.assertEqual("L1", level["key"])
        self.assertEqual("\u4e00\u5c42", level["name"])
        self.assertEqual(1, level["number"])

    def test_existing_chinese_frame_and_ground_floor_rules_remain_supported(self) -> None:
        self.assertTrue(is_frame_layer("\u56fe\u6846"))
        level = infer_level_from_title("\u9996\u5c42\u5e73\u9762\u56fe")
        self.assertIsNotNone(level)
        self.assertEqual("L1", level["key"])
        self.assertEqual("\u4e00\u5c42", level["name"])

    def test_standard_export_accepts_console_language_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            export = write_standard_project_outputs(
                tmp,
                [],
                Path("english_input.dxf"),
                language="en",
                translation_api_key="test-key",
                translation_base_url="https://example.invalid/v1",
                translation_model="test-model",
            )

            self.assertTrue(export.ai_model.exists())
            self.assertTrue(export.human_report.exists())


if __name__ == "__main__":
    unittest.main()
