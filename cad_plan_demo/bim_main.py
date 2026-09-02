from __future__ import annotations

import argparse
from pathlib import Path

from .bim.pipeline import run_bim_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create standardized BIM JSON from a fixed Excel workbook and design notes."
    )
    parser.add_argument("excel", help="Fixed-format .xlsx workbook, CSV file, or folder of CSV sheets.")
    parser.add_argument("notes", help="Design/general notes text file.")
    parser.add_argument("--out", default="outputs/bim_modeling", help="Output folder.")
    parser.add_argument("--api-key", default=None, help="API key. Defaults to DEEPSEEK_API_KEY or OPENAI_API_KEY.")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL. Defaults to DeepSeek v1.")
    parser.add_argument("--model", default=None, help="Model name. Defaults to deepseek-v4-pro.")
    args = parser.parse_args()

    try:
        model = run_bim_pipeline(
            args.excel,
            args.notes,
            args.out,
            api_key=args.api_key,
            base_url=args.base_url,
            model=args.model,
        )
    except RuntimeError as exc:
        print(f"BIM modeling stopped: {exc}")
        return 1
    counts = {key: len(value) for key, value in model.get("components", {}).items()}
    print("BIM standardization finished.")
    print("DeepSeek stages finished: Excel/notes understanding and Revit execution planning.")
    print(f"Components: {counts}")
    print(f"Requires human confirmation: {model.get('validation', {}).get('requires_human_confirmation')}")
    print(f"Output folder: {Path(args.out).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
