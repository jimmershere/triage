"""Load Test Runner.

Generates X12 EDI test files, uploads them to the ingest endpoint, polls for
completion, and records timing/result data. Supports concurrent uploads via
ThreadPoolExecutor.

Can be used standalone via CLI:
    python -m tests.loadtest.runner --profile smoke --base-url http://localhost:8000
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("loadtest.runner")

# ---------------------------------------------------------------------------
# Size constants
# ---------------------------------------------------------------------------

SIZE_BYTES: Dict[str, int] = {
    "512k": 512 * 1024,
    "1mb": 1 * 1024 * 1024,
    "5mb": 5 * 1024 * 1024,
    "10mb": 10 * 1024 * 1024,
    "50mb": 50 * 1024 * 1024,
    "100mb": 100 * 1024 * 1024,
    "1gb": 1 * 1024 * 1024 * 1024,
    "5gb": 5 * 1024 * 1024 * 1024,
}

PROFILE_SIZES: Dict[str, List[str]] = {
    "smoke": ["512k", "1mb", "5mb"],
    "standard": ["512k", "1mb", "5mb", "10mb", "50mb", "100mb"],
    "full": ["512k", "1mb", "5mb", "10mb", "50mb", "100mb", "1gb", "5gb"],
}

VALID_TYPES = {"837p", "837i", "837d", "835", "270", "271", "276", "278"}


# ---------------------------------------------------------------------------
# LoadTestRunner
# ---------------------------------------------------------------------------


class LoadTestRunner:
    """Runs load tests against the Triage ingest pipeline.

    Args:
        base_url: Base URL of the Triage API (e.g. http://localhost:8000)
        concurrency: Max concurrent uploads
        cancel_event: Threading event to signal cancellation
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        concurrency: int = 3,
        cancel_event: Optional[threading.Event] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.concurrency = max(1, min(concurrency, 20))
        self.cancel_event = cancel_event or threading.Event()

        # Shared state dict for the API to read progress
        self.state: Dict[str, Any] = {
            "status": "idle",
            "progress": {"completed": 0, "total": 0, "pct": 0.0},
            "results": [],
            "summary": None,
        }
        self._lock = threading.Lock()

    def run_single(
        self,
        tx_type: str,
        size_label: str,
        size_bytes: int,
        adversarial: bool = False,
    ) -> Dict[str, Any]:
        """Run a single test: generate → upload → poll → record.

        Returns a dict with upload_ms, processing_ms, result, error keys.
        """
        import io

        # Import generator (from tests.generators stub or real implementation)
        try:
            from tests.generators import generate_file
        except ImportError:
            logger.warning("tests.generators not available; using inline stub")
            generate_file = self._stub_generate

        # Generate the file
        try:
            content = generate_file(
                tx_type=tx_type,
                target_bytes=size_bytes,
                adversarial=adversarial,
            )
        except Exception as exc:
            return {
                "upload_ms": None,
                "processing_ms": None,
                "result": "fail",
                "error": f"Generation failed: {exc}",
            }

        filename = f"{tx_type}_{size_label}{'_adversarial' if adversarial else ''}.x12"

        # Upload to /ingest
        upload_start = time.perf_counter()
        try:
            import urllib.request
            import urllib.error

            # Build multipart form data manually to avoid requests dependency
            boundary = "----LoadTestBoundary"
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8") + content + (
                f"\r\n--{boundary}--\r\n"
            ).encode("utf-8")

            req = urllib.request.Request(
                f"{self.base_url}/ingest",
                data=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                upload_ms = (time.perf_counter() - upload_start) * 1000
                resp_data = json.loads(resp.read().decode("utf-8"))
                job_id = resp_data.get("job_id")
        except urllib.error.HTTPError as exc:
            upload_ms = (time.perf_counter() - upload_start) * 1000
            if adversarial and exc.code in (400, 413, 422):
                # Expected rejection for adversarial files
                return {
                    "upload_ms": round(upload_ms, 1),
                    "processing_ms": None,
                    "result": "pass",
                    "error": None,
                }
            return {
                "upload_ms": round(upload_ms, 1),
                "processing_ms": None,
                "result": "fail",
                "error": f"Upload HTTP {exc.code}: {exc.reason}",
            }
        except Exception as exc:
            upload_ms = (time.perf_counter() - upload_start) * 1000
            return {
                "upload_ms": round(upload_ms, 1),
                "processing_ms": None,
                "result": "fail",
                "error": f"Upload failed: {exc}",
            }

        if not job_id:
            return {
                "upload_ms": round(upload_ms, 1),
                "processing_ms": None,
                "result": "fail",
                "error": "No job_id in response",
            }

        # Poll /jobs/{job_id} for completion
        processing_start = time.perf_counter()
        poll_url = f"{self.base_url}/jobs/{job_id}"
        max_poll_seconds = 600  # 10 min timeout
        poll_interval = 2

        while (time.perf_counter() - processing_start) < max_poll_seconds:
            if self.cancel_event.is_set():
                return {
                    "upload_ms": round(upload_ms, 1),
                    "processing_ms": round((time.perf_counter() - processing_start) * 1000, 1),
                    "result": "fail",
                    "error": "Cancelled",
                }

            try:
                req = urllib.request.Request(poll_url)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    job_data = json.loads(resp.read().decode("utf-8"))
                    status = job_data.get("status", "")

                    if status in ("processed", "complete", "done"):
                        processing_ms = (time.perf_counter() - processing_start) * 1000
                        validation = job_data.get("validation_status", "")
                        if adversarial and validation == "invalid":
                            result = "pass"  # correctly rejected
                        elif adversarial and validation != "invalid":
                            result = "fail"  # should have been rejected
                        else:
                            result = "pass" if validation != "invalid" else "fail"

                        return {
                            "upload_ms": round(upload_ms, 1),
                            "processing_ms": round(processing_ms, 1),
                            "result": result,
                            "error": None,
                        }

                    if status in ("error", "failed"):
                        processing_ms = (time.perf_counter() - processing_start) * 1000
                        if adversarial:
                            return {
                                "upload_ms": round(upload_ms, 1),
                                "processing_ms": round(processing_ms, 1),
                                "result": "pass",  # adversarial correctly failed
                                "error": None,
                            }
                        return {
                            "upload_ms": round(upload_ms, 1),
                            "processing_ms": round(processing_ms, 1),
                            "result": "fail",
                            "error": f"Job failed with status: {status}",
                        }

            except Exception:
                pass  # continue polling

            time.sleep(poll_interval)

        processing_ms = (time.perf_counter() - processing_start) * 1000
        return {
            "upload_ms": round(upload_ms, 1),
            "processing_ms": round(processing_ms, 1),
            "result": "fail",
            "error": "Polling timeout exceeded",
        }

    def run_batch(
        self,
        types: List[str],
        sizes: List[str],
        include_adversarial: bool = True,
        include_multi_part: bool = True,
    ) -> Dict[str, Any]:
        """Run a full batch of tests using ThreadPoolExecutor.

        Returns the final state dict with all results and summary.
        """
        # Build test matrix
        test_items = []
        for tx_type in types:
            for size in sizes:
                test_items.append({
                    "file": f"{tx_type}_{size}.x12",
                    "type": tx_type,
                    "size_bytes": SIZE_BYTES.get(size, 0),
                    "size_label": size,
                    "status": "queued",
                    "upload_ms": None,
                    "processing_ms": None,
                    "result": None,
                    "error": None,
                    "adversarial": False,
                })

        if include_adversarial:
            for tx_type in types[:3]:
                for variant in ("truncated", "bad_delimiters"):
                    test_items.append({
                        "file": f"{tx_type}_adversarial_{variant}.x12",
                        "type": tx_type,
                        "size_bytes": 1024 if variant == "truncated" else 2048,
                        "size_label": "1k" if variant == "truncated" else "2k",
                        "status": "queued",
                        "upload_ms": None,
                        "processing_ms": None,
                        "result": None,
                        "error": None,
                        "adversarial": True,
                    })

        total = len(test_items)
        with self._lock:
            self.state["status"] = "running"
            self.state["progress"] = {"completed": 0, "total": total, "pct": 0.0}
            self.state["results"] = test_items

        completed_count = 0

        def _run_item(item: Dict[str, Any]) -> None:
            nonlocal completed_count
            if self.cancel_event.is_set():
                item["status"] = "queued"
                return

            item["status"] = "uploading"
            try:
                result = self.run_single(
                    tx_type=item["type"],
                    size_label=item["size_label"],
                    size_bytes=item["size_bytes"],
                    adversarial=item["adversarial"],
                )
                item["upload_ms"] = result.get("upload_ms")
                item["processing_ms"] = result.get("processing_ms")
                item["result"] = result.get("result", "pass")
                item["error"] = result.get("error")
                item["status"] = "done" if not result.get("error") else "failed"
            except Exception as exc:
                item["status"] = "failed"
                item["result"] = "fail"
                item["error"] = str(exc)

            with self._lock:
                completed_count += 1
                self.state["progress"] = {
                    "completed": completed_count,
                    "total": total,
                    "pct": round((completed_count / total) * 100, 1) if total else 0,
                }

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            futures = {executor.submit(_run_item, item): item for item in test_items}
            for future in as_completed(futures):
                if self.cancel_event.is_set():
                    break
                future.result()  # propagate exceptions

        with self._lock:
            self.state["status"] = "stopped" if self.cancel_event.is_set() else "complete"

        self._compute_summary()
        return dict(self.state)

    def _compute_summary(self) -> None:
        """Compute summary stats from results."""
        results = self.state.get("results", [])
        if not results:
            return

        passed = sum(1 for r in results if r.get("result") == "pass")
        failed = sum(1 for r in results if r.get("result") == "fail")
        total = len(results)

        total_bytes = 0
        total_time_s = 0
        for r in results:
            if r.get("upload_ms") and r.get("size_bytes"):
                total_bytes += r["size_bytes"]
                total_time_s += (r["upload_ms"] + (r.get("processing_ms") or 0)) / 1000

        avg_throughput = (total_bytes / (1024 * 1024)) / total_time_s if total_time_s > 0 else 0

        by_type: Dict[str, Dict[str, int]] = {}
        for r in results:
            if r.get("adversarial"):
                continue
            t = r["type"]
            if t not in by_type:
                by_type[t] = {"passed": 0, "failed": 0}
            key = "passed" if r.get("result") == "pass" else "failed"
            by_type[t][key] += 1

        by_size: Dict[str, Dict[str, int]] = {}
        for r in results:
            if r.get("adversarial"):
                continue
            s = r.get("size_label", "unknown")
            if s not in by_size:
                by_size[s] = {"passed": 0, "failed": 0}
            key = "passed" if r.get("result") == "pass" else "failed"
            by_size[s][key] += 1

        adv_results = [r for r in results if r.get("adversarial")]
        adversarial = {}
        if adv_results:
            adversarial = {
                "expected_rejections": len(adv_results),
                "correctly_rejected": sum(1 for r in adv_results if r.get("result") == "pass"),
                "incorrectly_accepted": sum(1 for r in adv_results if r.get("result") == "fail"),
            }

        with self._lock:
            self.state["summary"] = {
                "total": total,
                "passed": passed,
                "failed": failed,
                "avg_throughput_mbps": round(avg_throughput, 2),
                "by_type": by_type,
                "by_size": by_size,
                "adversarial": adversarial,
            }

    @staticmethod
    def _stub_generate(tx_type: str, target_bytes: int, adversarial: bool = False) -> bytes:
        """Fallback generator when tests.generators is not available."""
        header = f"ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       *230101*1200*^*00501*000000001*0*P*:~\n"
        if adversarial:
            return header[:min(len(header), target_bytes)].encode("ascii")
        content = header + ("NTE*ADD*" + "X" * 76 + "~\n") * max(1, target_bytes // 85)
        return content[:target_bytes].encode("ascii")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Triage Load Test Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--profile",
        choices=["smoke", "standard", "full", "custom"],
        default="smoke",
        help="Test profile (default: smoke)",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("TRIAGE_LOADTEST_BASE_URL", "http://localhost:8000"),
        help="Base URL of the Triage API",
    )
    parser.add_argument(
        "--types",
        nargs="+",
        default=list(VALID_TYPES),
        help="Transaction types to test",
    )
    parser.add_argument(
        "--sizes",
        nargs="+",
        default=None,
        help="Size steps (for custom profile)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Max concurrent uploads (default: 3)",
    )
    parser.add_argument(
        "--no-adversarial",
        action="store_true",
        help="Skip adversarial tests",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write results JSON to this file",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Resolve sizes
    if args.profile == "custom" and args.sizes:
        sizes = [s for s in args.sizes if s in SIZE_BYTES]
    else:
        sizes = PROFILE_SIZES.get(args.profile, PROFILE_SIZES["smoke"])

    types = [t.lower() for t in args.types if t.lower() in VALID_TYPES]

    logger.info(
        "Starting load test: profile=%s types=%s sizes=%s concurrency=%d base_url=%s",
        args.profile, types, sizes, args.concurrency, args.base_url,
    )

    runner = LoadTestRunner(
        base_url=args.base_url,
        concurrency=args.concurrency,
    )

    results = runner.run_batch(
        types=types,
        sizes=sizes,
        include_adversarial=not args.no_adversarial,
    )

    # Print summary
    summary = results.get("summary", {})
    print(f"\n{'='*60}")
    print(f"Load Test Complete")
    print(f"{'='*60}")
    print(f"Total: {summary.get('total', 0)}  "
          f"Passed: {summary.get('passed', 0)}  "
          f"Failed: {summary.get('failed', 0)}")
    print(f"Avg throughput: {summary.get('avg_throughput_mbps', 0):.2f} MB/s")

    if summary.get("adversarial"):
        adv = summary["adversarial"]
        print(f"Adversarial: {adv.get('correctly_rejected', 0)}/{adv.get('expected_rejections', 0)} correctly rejected")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {args.output}")

    # Exit with non-zero if any failures
    if summary.get("failed", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
