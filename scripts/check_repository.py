#!/usr/bin/env python3
"""Check the portable source tree and retained evidence without running workers."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys

ROOT = Path(__file__).resolve().parents[1]
GENERATED_DIRS = {".git", ".venv", "venv", "node_modules", ".pnpm-store",
                  "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                  "dist", "var", "runtime", "results", "tmp"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contained_path(base: Path, relative: object) -> Path | None:
    """Accept canonical repository-relative references, including safe symlinks only."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    reference = PurePosixPath(relative)
    if (reference.is_absolute() or PureWindowsPath(relative).drive
            or ".." in reference.parts or reference.as_posix() != relative):
        return None
    try:
        base = base.resolve()
        path = (base / relative).resolve()
        if not base.is_relative_to(ROOT.resolve()) or not path.is_relative_to(base):
            return None
        return path
    except (OSError, RuntimeError, ValueError):
        return None


def inspect_repository() -> dict:
    checks, errors = [], []

    def check(name, condition, detail=""):
        checks.append({"name": name, "passed": bool(condition), "detail": detail})
        if not condition:
            errors.append(name + (": " + detail if detail else ""))

    def read_document(relative, name):
        path = contained_path(ROOT, relative)
        try:
            document = json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else None
        except (OSError, ValueError, UnicodeError, RecursionError):
            document = None
        check(name, isinstance(document, dict), "" if isinstance(document, dict)
              else "Missing, invalid, or unsafe JSON object")
        return document if isinstance(document, dict) else None

    def entries(document, key, name):
        value = document.get(key) if document else None
        valid = isinstance(value, list) and bool(value) and all(isinstance(x, dict) for x in value)
        check(name, valid, "" if valid else "Expected a nonempty list of file records")
        return value if valid else []

    def verify_file(base, entry, name, *, path_key="path", size_key="bytes"):
        entry = entry if isinstance(entry, dict) else {}
        path = contained_path(base, entry.get(path_key))
        digest = entry.get("sha256")
        valid = (isinstance(digest, str) and len(digest) == 64
                 and all(c in "0123456789abcdef" for c in digest))
        if size_key is not None:
            size = entry.get(size_key)
            valid = valid and type(size) is int and size >= 0
        try:
            valid = valid and path is not None and path.is_file() and sha256(path) == digest
            if size_key is not None:
                valid = valid and path.stat().st_size == entry[size_key]
        except OSError:
            valid = False
        check(name, valid, "" if valid else "Unsafe/missing file, invalid metadata, or SHA256/size mismatch")
        return path if valid else None

    required = ["README.md", ".gitignore", ".env.example", "pyproject.toml",
                "scripts/serve_workloads.py", "scripts/run_tests.py",
                "packages/fabric-sim/src/fabric_sim/core.py",
                "services/gateway/src/gateway/simulation_api.py",
                "apps/web/workload.html",
                "agent_demo/__main__.py", "agent_demo/ddp_worker.py",
                "demos/live/server.py", "demos/live/启动演示.command",
                "demos/live/examples/topology_3racks.json",
                "evidence/README.md", "evidence/evidence_manifest.json"]
    missing = [name for name in required if not (ROOT / name).is_file()]
    check("required_entrypoints", not missing, ", ".join(missing))

    candidates = [p for p in ROOT.rglob("*")
             if not any(part in GENERATED_DIRS for part in p.relative_to(ROOT).parts)
             and p.name != "topology_fixtures.json"]
    unsafe = [str(p.relative_to(ROOT)) for p in candidates
              if p.is_symlink() and contained_path(ROOT, p.relative_to(ROOT).as_posix()) is None]
    check("repository_paths_within_root", not unsafe, ", ".join(unsafe))
    files = [p for p in candidates if p.is_file()
             and contained_path(ROOT, p.relative_to(ROOT).as_posix()) is not None]
    invalid_python, invalid_json = [], []
    for path in files:
        try:
            if path.suffix == ".py":
                compile(path.read_bytes(), str(path.relative_to(ROOT)), "exec")
            elif path.suffix == ".json":
                json.loads(path.read_text(encoding="utf-8"))
        except (SyntaxError, ValueError, UnicodeError, RecursionError) as exc:
            target = invalid_python if path.suffix == ".py" else invalid_json
            target.append(f"{path.relative_to(ROOT)}: {exc}")
    check("python_syntax", not invalid_python, "; ".join(invalid_python))
    check("json_documents", not invalid_json, "; ".join(invalid_json))

    sim_manifest = read_document("demos/live/vendor/fabric_sim_source.json", "simulator_source_manifest")
    for index, entry in enumerate(entries(sim_manifest, "files", "simulator_source_entries")):
        label = str(entry.get("name", index))
        for suffix, base in (("vendor", "demos/live/vendor/fabric_sim"),
                             ("primary", "packages/fabric-sim/src/fabric_sim")):
            verify_file(ROOT / base, entry, "simulator_snapshot:" + label + ":" + suffix,
                        path_key="name", size_key=None)

    tcp_vendor = ROOT / "demos/live/vendor/agent_demo"
    tcp_files = (sorted(tcp_vendor.glob("*.py"))
                 if contained_path(ROOT, "demos/live/vendor/agent_demo") is not None else [])
    for vendored in tcp_files:
        primary = contained_path(ROOT / "agent_demo", vendored.name)
        check("tcp_snapshot:" + vendored.name,
              contained_path(tcp_vendor, vendored.name) is not None
              and primary is not None and primary.is_file() and sha256(vendored) == sha256(primary))
    tcp_manifest = read_document("demos/live/tcp_source_provenance.json", "tcp_source_provenance")
    tcp_entries = entries(tcp_manifest, "copied_without_modification", "tcp_provenance_entries")
    tcp_names = [entry.get("file") for entry in tcp_entries]
    check("tcp_provenance_coverage",
          all(isinstance(name, str) for name in tcp_names)
          and len(set(str(name) for name in tcp_names)) == len(tcp_names)
          and set(tcp_names) == {p.name for p in tcp_files if p.is_file()})
    for index, entry in enumerate(tcp_entries):
        verify_file(tcp_vendor, entry, "tcp_provenance:" + str(entry.get("file", index)),
                    path_key="file", size_key=None)

    executed = ROOT / "evidence/gpu-case1/source_executed/agent_demo"
    snapshots = (list(executed.glob("*.py"))
                 if contained_path(ROOT, "evidence/gpu-case1/source_executed/agent_demo") is not None else [])
    check("executed_gpu_source_present", bool(snapshots))
    for snapshot in sorted(snapshots):
        primary = contained_path(ROOT / "agent_demo", snapshot.name)
        check("gpu_public_snapshot_matches_primary:" + snapshot.name,
              contained_path(executed, snapshot.name) is not None
              and primary is not None and primary.is_file() and sha256(snapshot) == sha256(primary))

    evidence_root = ROOT / "evidence"
    manifest = read_document("evidence/evidence_manifest.json", "evidence_manifest")
    evidence_entries = entries(manifest, "files", "evidence_manifest_entries")
    evidence_count = 0
    evidence_candidates = (list(evidence_root.rglob("*"))
                           if contained_path(ROOT, "evidence") is not None else [])
    unsafe_evidence = [p.relative_to(evidence_root).as_posix() for p in evidence_candidates
                       if p.is_symlink() and contained_path(evidence_root, p.relative_to(evidence_root).as_posix()) is None]
    check("evidence_paths_within_root", not unsafe_evidence, ", ".join(unsafe_evidence))
    actual_evidence = {p.relative_to(evidence_root).as_posix(): p for p in evidence_candidates
                       if p.is_file() and p != evidence_root / "evidence_manifest.json"
                       and contained_path(evidence_root, p.relative_to(evidence_root).as_posix()) is not None}
    listed = [entry.get("path") for entry in evidence_entries]
    paths_valid = all(isinstance(path, str) for path in listed)
    listed_paths = set(listed) if paths_valid else set()
    coverage_detail = ("Missing from manifest: " + ", ".join(sorted(set(actual_evidence) - listed_paths))
                       + "; absent/self/invalid entries: " + ", ".join(sorted(listed_paths - set(actual_evidence))))
    coverage_ok = paths_valid and len(listed_paths) == len(listed) and listed_paths == set(actual_evidence)
    check("evidence_manifest_exact_coverage", coverage_ok, "" if coverage_ok else coverage_detail)
    check("evidence_manifest_totals", bool(manifest)
          and manifest.get("file_count") == len(evidence_entries) == len(actual_evidence)
          and manifest.get("total_bytes") == sum(p.stat().st_size for p in actual_evidence.values())
          and manifest.get("self_excluded") == "evidence_manifest.json")
    for index, entry in enumerate(evidence_entries):
        if verify_file(evidence_root, entry, "evidence:" + str(entry.get("path", index)), size_key="size_bytes"):
            evidence_count += 1

    demo_root = ROOT / "demos/live"
    gpu_root = ROOT / "evidence/gpu-case1"
    gpu_manifest = read_document("demos/live/data/gpu_sources.json", "gpu_demo_source_manifest")
    gpu_payload = read_document("demos/live/data/gpu_evidence.json", "gpu_demo_payload")
    source_entries = entries(gpu_manifest, "sources", "gpu_demo_source_entries")
    source_names = [entry.get("path") for entry in source_entries]
    sources_valid = all(isinstance(path, str) for path in source_names)
    check("gpu_demo_source_root", bool(gpu_manifest)
          and gpu_manifest.get("source_root") == "evidence/gpu-case1")
    check("gpu_demo_sources_unique", sources_valid and len(set(source_names)) == len(source_names))
    source_index = {entry["path"]: entry for entry in source_entries} if sources_valid else {}
    for index, entry in enumerate(source_entries):
        verify_file(gpu_root, entry, "gpu_demo_source:" + str(entry.get("path", index)))
    output = gpu_manifest.get("output") if gpu_manifest else None
    check("gpu_demo_output_path", isinstance(output, dict) and output.get("path") == "data/gpu_evidence.json")
    verify_file(demo_root, output, "gpu_demo_output")
    stages = entries(gpu_payload, "stages", "gpu_demo_stages")
    for stage_index, stage in enumerate(stages):
        references = entries(stage, "evidence", f"gpu_demo_stage_references:{stage_index + 1}")
        for ref_index, reference in enumerate(references):
            label = f"gpu_demo_stage:{stage_index + 1}:reference:{ref_index + 1}"
            verify_file(gpu_root, reference, label)
            source_path = reference.get("path")
            source = source_index.get(source_path) if isinstance(source_path, str) else None
            check(label + ":in_source_manifest", isinstance(source, dict)
                  and all(reference.get(key) == source.get(key) for key in ("path", "sha256", "bytes")))

    # Public-copy digests are current file checksums. Historical source/import
    # records and checkpoint digests are deliberately not rewritten or rehashed.
    release_name = "evidence/public_release.json"
    if (ROOT / release_name).exists():
        release = read_document(release_name, "public_release_marker")
        public_copy = bool(release) and release.get("status") == "sanitized_public_copy"
        check("public_release_status", public_copy,
              "" if public_copy else "Present release marker must declare sanitized_public_copy")
        if public_copy:
            payload_privacy = gpu_payload.get("privacy") if gpu_payload else None
            manifest_privacy = gpu_manifest.get("privacy") if gpu_manifest else None
            check("gpu_demo_privacy_matches", isinstance(payload_privacy, dict)
                  and payload_privacy == manifest_privacy)
            expected_privacy = {
                "status": "sanitized_public_copy",
                "release_marker": release_name,
                "pseudonymized_fields": ["personal_paths", "host_identifiers", "gpu_uuids", "pci_addresses"],
                "execution_checks_scope": "historical_original_execution_checks",
                "source_hash_scope": "sanitized_public_copies",
                "checkpoint_digest_scope": "retained_historical_digests_not_rehashed",
            }
            privacy = payload_privacy if isinstance(payload_privacy, dict) else {}
            check("gpu_demo_privacy_scope", all(privacy.get(key) == value for key, value in expected_privacy.items())
                  and contained_path(ROOT, privacy.get("release_marker")) == (ROOT / release_name).resolve())
            disclosure = privacy.get("disclosure")
            boundaries = gpu_payload.get("boundaries") if gpu_payload else None
            check("gpu_demo_privacy_disclosure", isinstance(disclosure, str) and bool(disclosure.strip())
                  and bool(gpu_manifest) and gpu_manifest.get("disclosure") == disclosure
                  and bool(gpu_payload) and isinstance(gpu_payload.get("disclosure"), str)
                  and disclosure in gpu_payload["disclosure"]
                  and isinstance(boundaries, list) and disclosure in boundaries)
            verification = gpu_payload.get("verification", {}) if gpu_payload else {}
            check("gpu_demo_historical_verification_scope", bool(gpu_manifest)
                  and gpu_manifest.get("validation_scope") == "export_consistency_of_sanitized_copies_not_hardware_reverification"
                  and isinstance(verification, dict)
                  and verification.get("checks_scope") == "historical_original_execution_checks"
                  and gpu_manifest.get("checkpoint_blob_copied") is False
                  and gpu_manifest.get("checkpoint_blob_rehashed_by_this_export") is False)

    unwanted = [str(p.relative_to(ROOT)) for p in files
                if p.name == ".env" or p.suffix in {".pt", ".pth", ".safetensors", ".pem", ".key"}
                or p.name in {"auth.json", "credentials.json"}]
    check("no_local_credentials_or_weights", not unwanted, ", ".join(unwanted))
    large = [str(p.relative_to(ROOT)) for p in files if p.stat().st_size >= 10 * 1024**2]
    check("source_files_under_10MiB", not large, ", ".join(large))
    return {"status": "passed" if not errors else "failed",
            "scope": "File/syntax/hash checks only; no HTTP listener, training, or fault injection.",
            "files_inspected": len(files), "evidence_files_verified": evidence_count,
            "checks_passed": sum(c["passed"] for c in checks),
            "checks_total": len(checks), "errors": errors, "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="Optional path for the detailed check report")
    args = parser.parse_args()
    report = inspect_repository()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{report['status']}: {report['checks_passed']}/{report['checks_total']} checks; "
          f"{report['files_inspected']} files; {report['evidence_files_verified']} evidence hashes")
    for error in report["errors"]:
        print(error, file=sys.stderr)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
