from __future__ import annotations

import argparse
from pathlib import Path

from .bim.agent_controller import run_bim_agent


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the memory-augmented BIM modeling agent."
    )
    parser.add_argument("excel", help="Fixed-format .xlsx workbook, CSV file, or folder of CSV sheets.")
    parser.add_argument("notes", help="Design/general notes text file.")
    parser.add_argument("--out", default="outputs/bim_agent_run", help="Output folder.")
    parser.add_argument("--memory", default="agent_memory", help="Agent memory folder.")
    parser.add_argument("--retrieve-limit", type=int, default=3, help="Number of prior cases to retrieve.")
    parser.add_argument("--api-key", default=None, help="API key. Defaults to DEEPSEEK_API_KEY or OPENAI_API_KEY.")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL. Defaults to DeepSeek v1.")
    parser.add_argument("--model", default=None, help="Model name. Defaults to deepseek-v4-pro.")
    args = parser.parse_args()

    try:
        result = run_bim_agent(
            args.excel,
            args.notes,
            args.out,
            memory_dir=args.memory,
            retrieve_limit=args.retrieve_limit,
            api_key=args.api_key,
            base_url=args.base_url,
            model=args.model,
        )
    except RuntimeError as exc:
        print(f"BIM modeling agent stopped: {exc}")
        return 1

    counts = {key: len(value) for key, value in result.get("components", {}).items()}
    agent = result.get("agent", {})
    print("BIM modeling agent finished.")
    print(f"Agent case id: {agent.get('case_id', '')}")
    print(f"Retrieved memory cases: {agent.get('memory_context', {}).get('case_count', 0)}")
    print(f"Components: {counts}")
    print(f"Output folder: {Path(args.out).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
