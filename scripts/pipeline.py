#!/usr/bin/env python3
"""Unified pipeline for ExpertAP data processing.

Orchestrates the full data pipeline:
  1. IMPORT  — Download new decisions from GCS → database
  2. ANALYZE — Extract ArgumentareCritica via LLM (Gemini)
  3. EMBED   — Generate vector embeddings for RAG search

Each step is idempotent: it skips already-processed records.
Safe to run repeatedly (daily cron, Cloud Scheduler, manual).

Usage:
    python scripts/pipeline.py                    # Full pipeline (import → analyze → embed)
    python scripts/pipeline.py --step analyze     # Only analysis
    python scripts/pipeline.py --step embed       # Only embeddings
    python scripts/pipeline.py --step import      # Only import
    python scripts/pipeline.py --daily            # Alias for full pipeline (for cron)
    python scripts/pipeline.py --limit 10         # Limit each step to 10 records (testing)

Future automation (Cloud Run Job + Cloud Scheduler):
    See CLAUDE.md for instructions on setting up daily automated runs.
"""

import asyncio
import argparse
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent


def run_step(script_name: str, args: list[str], step_label: str) -> bool:
    """Run a pipeline step as a subprocess.

    Args:
        script_name: Script filename in scripts/ directory.
        args: Command-line arguments to pass.
        step_label: Human-readable label for output.

    Returns:
        True if step succeeded, False otherwise.
    """
    script_path = SCRIPTS_DIR / script_name
    cmd = [sys.executable, str(script_path)] + args

    print(f"\n{'=' * 60}")
    print(f"STEP: {step_label}")
    print(f"CMD:  {' '.join(cmd)}")
    print(f"{'=' * 60}\n")

    start = time.time()
    result = subprocess.run(cmd, env=None)  # Inherit parent env (DATABASE_URL, etc.)
    elapsed = time.time() - start

    if result.returncode == 0:
        print(f"\n>>> {step_label} completed in {elapsed:.1f}s")
        return True
    else:
        print(f"\n>>> {step_label} FAILED (exit code {result.returncode}) after {elapsed:.1f}s")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="ExpertAP unified data pipeline: import → analyze → embed"
    )
    parser.add_argument(
        "--step",
        choices=["import", "analyze", "embed"],
        help="Run only a specific step (default: all steps)",
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Run full pipeline (alias for default behavior, useful for cron)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit each step to N records (for testing)",
    )
    parser.add_argument(
        "--skip-import",
        action="store_true",
        help="Skip the import step (useful when GCS is not available)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-processing (re-analyze, re-embed)",
    )

    args = parser.parse_args()

    steps_to_run = []
    if args.step:
        steps_to_run = [args.step]
    else:
        # Full pipeline
        if not args.skip_import:
            steps_to_run.append("import")
        steps_to_run.extend(["analyze", "embed"])

    pipeline_start = time.time()
    results = {}

    print("=" * 60)
    print("ExpertAP Data Pipeline")
    print(f"Steps: {' → '.join(steps_to_run)}")
    if args.limit:
        print(f"Limit: {args.limit} records per step")
    if args.force:
        print(f"Mode: FORCE (re-process existing)")
    print("=" * 60)

    # Step 1: Import from GCS
    if "import" in steps_to_run:
        import_args = ["--skip-embeddings"]
        if args.limit:
            import_args.extend(["--limit", str(args.limit)])
        results["import"] = run_step(
            "import_decisions_from_gcs.py", import_args, "Import decisions from GCS"
        )
        # Import failures are non-fatal (some files may fail but others succeed)

    # Step 2: LLM Analysis
    if "analyze" in steps_to_run:
        analyze_args = []
        if args.limit:
            analyze_args.extend(["--limit", str(args.limit)])
        if args.force:
            analyze_args.append("--force")
        results["analyze"] = run_step(
            "generate_analysis.py", analyze_args, "LLM Analysis (ArgumentareCritica)"
        )

    # Step 3: Embeddings
    if "embed" in steps_to_run:
        embed_args = []
        if args.limit:
            embed_args.extend(["--limit", str(args.limit)])
        if args.force:
            embed_args.append("--force")
        results["embed"] = run_step(
            "generate_embeddings.py", embed_args, "Generate Embeddings"
        )

    # Pipeline summary
    total_elapsed = time.time() - pipeline_start
    print(f"\n{'=' * 60}")
    print("PIPELINE SUMMARY")
    print(f"{'=' * 60}")
    for step, success in results.items():
        status = "OK" if success else "FAILED"
        print(f"  {step:12s} → {status}")
    print(f"\nTotal time: {total_elapsed:.1f}s ({total_elapsed/60:.1f}min)")
    print(f"{'=' * 60}")

    # Exit with error if any step failed
    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
