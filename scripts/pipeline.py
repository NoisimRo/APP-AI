#!/usr/bin/env python3
"""Unified pipeline for ExpertAP data processing.

Orchestrates the full data pipeline:
  1. IMPORT  — Download new decisions from GCS → database
  2. ANALYZE — Extract ArgumentareCritica via LLM (Gemini)
  3. EMBED   — Generate vector embeddings for RAG search
  4. EXTRACT — Extract obiect_contract from text (regex, instant)
  5. CPV     — Deduce CPV codes via embedding similarity

Each step is idempotent: it skips already-processed records.
Safe to run repeatedly (daily cron, Cloud Scheduler, manual).

Usage:
    python scripts/pipeline.py                    # Full pipeline (all 5 steps)
    python scripts/pipeline.py --step analyze     # Only analysis
    python scripts/pipeline.py --step embed       # Only embeddings
    python scripts/pipeline.py --step extract     # Only obiect_contract extraction
    python scripts/pipeline.py --step cpv         # Only CPV deduction
    python scripts/pipeline.py --daily            # Alias for full pipeline (for cron)
    python scripts/pipeline.py --limit 10         # Limit each step to 10 records (testing)

Future automation (Cloud Run Job + Cloud Scheduler):
    See CLAUDE.md for instructions on setting up daily automated runs.
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent


def run_step(script_name: str, args: list[str], step_label: str) -> tuple[bool, str]:
    """Run a pipeline step as a subprocess, streaming output and capturing it.

    Args:
        script_name: Script filename in scripts/ directory.
        args: Command-line arguments to pass.
        step_label: Human-readable label for output.

    Returns:
        Tuple of (success, captured_output).
    """
    script_path = SCRIPTS_DIR / script_name
    cmd = [sys.executable, "-u", str(script_path)] + args

    print(f"\n{'=' * 60}")
    print(f"STEP: {step_label}")
    print(f"CMD:  {' '.join(cmd)}")
    print(f"{'=' * 60}\n")

    start = time.time()
    captured_lines = []

    # Stream output line-by-line while capturing it
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    for line in proc.stdout:
        print(line, end="", flush=True)
        captured_lines.append(line)
    proc.wait()

    elapsed = time.time() - start
    output = "".join(captured_lines)

    if proc.returncode == 0:
        print(f"\n>>> {step_label} completed in {elapsed:.1f}s")
        return True, output
    else:
        print(f"\n>>> {step_label} FAILED (exit code {proc.returncode}) after {elapsed:.1f}s")
        return False, output


def parse_imported_count(output: str) -> int | None:
    """Parse PIPELINE_NEW_IMPORTED=N from import step output."""
    match = re.search(r"PIPELINE_NEW_IMPORTED=(\d+)", output)
    return int(match.group(1)) if match else None


def main():
    parser = argparse.ArgumentParser(
        description="ExpertAP unified data pipeline: import → analyze → embed → extract → cpv"
    )
    parser.add_argument(
        "--step",
        choices=["import", "analyze", "embed", "extract", "cpv"],
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
        steps_to_run.extend(["analyze", "embed", "extract", "cpv"])

    pipeline_start = time.time()
    results = {}
    new_imported = None  # Track how many decisions were imported

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
        success, output = run_step(
            "import_decisions_from_gcs.py", import_args, "Import decisions from GCS"
        )
        results["import"] = success
        new_imported = parse_imported_count(output)
        if new_imported is not None:
            print(f"\n>>> New decisions imported: {new_imported}")

    # Step 2: LLM Analysis
    if "analyze" in steps_to_run:
        analyze_args = []
        if args.limit:
            analyze_args.extend(["--limit", str(args.limit)])
        elif new_imported is not None and new_imported > 0 and not args.force:
            # Auto-limit to newly imported decisions (avoid processing 1000s of old ones)
            analyze_args.extend(["--limit", str(new_imported)])
            print(f"\n>>> Auto-limiting analysis to {new_imported} newly imported decisions")
        elif new_imported == 0:
            print("\n>>> No new decisions imported — skipping analysis")
            results["analyze"] = True
        if "analyze" not in results:
            if args.force:
                analyze_args.append("--force")
            success, _ = run_step(
                "generate_analysis.py", analyze_args, "LLM Analysis (ArgumentareCritica)"
            )
            results["analyze"] = success

    # Step 3: Embeddings
    if "embed" in steps_to_run:
        embed_args = []
        if args.limit:
            embed_args.extend(["--limit", str(args.limit)])
        if args.force:
            embed_args.append("--force")
        success, _ = run_step(
            "generate_embeddings.py", embed_args, "Generate Embeddings"
        )
        results["embed"] = success

    # Step 4: Extract obiect_contract (regex, instant, no LLM)
    if "extract" in steps_to_run:
        extract_args = []
        if args.limit:
            extract_args.extend(["--limit", str(args.limit)])
        if args.force:
            extract_args.append("--force")
        success, _ = run_step(
            "extract_obiect_contract.py", extract_args, "Extract obiect_contract (regex)"
        )
        results["extract"] = success

    # Step 5: Deduce CPV codes via embedding similarity
    if "cpv" in steps_to_run:
        cpv_args = []
        if args.limit:
            cpv_args.extend(["--limit", str(args.limit)])
        success, _ = run_step(
            "deduce_cpv.py", cpv_args, "Deduce CPV codes (embedding similarity)"
        )
        results["cpv"] = success

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
