"""Registry concurrency and store-health tests (R-Stage 5)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from filelock import FileLock

from documa.collections.registry import (
    delete_document,
    ingest_document,
    load_registry,
    rebuild_index,
    store_health,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

_WORKER_SCRIPT = """
import sys
from pathlib import Path
from documa.collections.registry import ingest_document

store = sys.argv[1]
prefix = sys.argv[2]
shared_dir = Path(sys.argv[3])
failures = 0
# 25 worker-unique documents + 25 shared ones (contested between workers).
for i in range(25):
    doc = shared_dir / f"{prefix}_{i}.md"
    doc.write_text(f"# {prefix} {i}\\n\\nUnique content {prefix} {i}.\\n", encoding="utf-8")
    result = ingest_document(str(doc), store_dir=store)
    failures += result.get("status") not in ("ok",)
for i in range(25):
    result = ingest_document(str(shared_dir / f"shared_{i}.md"), store_dir=store)
    failures += result.get("status") not in ("ok",)
sys.exit(failures)
"""


class ConcurrencyTests(unittest.TestCase):
    def test_two_processes_ingesting_concurrently_keep_the_index_intact(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store = tmp_path / ".documa"
            for i in range(25):
                (tmp_path / f"shared_{i}.md").write_text(
                    f"# shared {i}\n\nShared content {i}.\n", encoding="utf-8"
                )

            env = dict(os.environ)
            workers = [
                subprocess.Popen(
                    [sys.executable, "-c", _WORKER_SCRIPT, str(store), f"w{n}", str(tmp_path)],
                    cwd=REPO_ROOT,
                    env=env,
                )
                for n in (1, 2)
            ]
            exit_codes = [worker.wait(timeout=300) for worker in workers]

            self.assertEqual(exit_codes, [0, 0], "worker ingests reported failures")
            registry = load_registry(store)
            self.assertNotEqual(registry.get("code"), "REGISTRY_CORRUPTED")
            # 25 unique per worker + 25 shared (deduplicated across workers).
            self.assertEqual(len(registry["documents"]), 75)
            ids = [entry["document_id"] for entry in registry["documents"]]
            self.assertEqual(len(ids), len(set(ids)), "duplicate document ids in index")
            self.assertFalse((store / "registry.json.corrupted").exists())

            health = store_health(store)
            self.assertEqual(health["missing_ir"], [])
            self.assertEqual(health["orphan_dirs"], [])


class LockTimeoutTests(unittest.TestCase):
    def test_held_lock_times_out_with_explicit_error(self, ):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store = tmp_path / ".documa"
            store.mkdir()
            doc = tmp_path / "note.md"
            doc.write_text("# t\n\nbody\n", encoding="utf-8")

            with FileLock(str(store / "registry.lock")):
                result = ingest_document(str(doc), store_dir=store, lock_timeout=0.2)
            self.assertEqual(result["code"], "LOCK_TIMEOUT")
            self.assertIn("doctor --store-dir", result["message"])

            held_delete = delete_document("doc-ffffffffffffffff", store_dir=store, lock_timeout=0.2)
            self.assertEqual(held_delete["code"], "DOCUMENT_ID_NOT_FOUND")  # lock released, runs normally

    def test_rebuild_is_idempotent_under_sequential_calls(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            store = tmp_path / ".documa"
            doc = tmp_path / "note.md"
            doc.write_text("# t\n\nbody\n", encoding="utf-8")
            ingest_document(str(doc), store_dir=store)

            first = rebuild_index(store_dir=store)
            second = rebuild_index(store_dir=store)
            self.assertEqual(first["rebuilt"], 1)
            self.assertEqual(second["rebuilt"], 1)


class StoreHealthTests(unittest.TestCase):
    def _seeded_store(self, tmp_path: Path) -> Path:
        store = tmp_path / ".documa"
        doc = tmp_path / "note.md"
        doc.write_text("# t\n\nbody\n", encoding="utf-8")
        ingest_document(str(doc), store_dir=store)
        return store

    def test_healthy_store_reports_ok(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = self._seeded_store(Path(tmp))
            health = store_health(store)
            self.assertEqual(health["status"], "ok")
            self.assertEqual(health["document_count"], 1)

    def test_orphan_dir_and_missing_ir_are_reported(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = self._seeded_store(Path(tmp))
            (store / "documents" / "doc-orphan0000000000").mkdir()
            entry = load_registry(store)["documents"][0]
            (store / Path(entry["ir_path"])).unlink()

            health = store_health(store)
            self.assertEqual(health["status"], "warning")
            self.assertEqual(health["orphan_dirs"], ["doc-orphan0000000000"])
            self.assertEqual(health["missing_ir"], [entry["document_id"]])

    def test_stale_lock_is_flagged_but_not_deleted(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = self._seeded_store(Path(tmp))
            lock_file = store / "registry.lock"
            lock_file.touch()
            old = time.time() - 3600
            os.utime(lock_file, (old, old))

            health = store_health(store)
            self.assertTrue(health["lock"]["stale"])
            self.assertTrue(lock_file.exists(), "doctor must not delete locks")

    def test_corrupted_registry_reports_error(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = self._seeded_store(Path(tmp))
            (store / "registry.json").write_text("{broken", encoding="utf-8")
            health = store_health(store)
            self.assertEqual(health["code"], "REGISTRY_CORRUPTED")


class InterfaceTests(unittest.TestCase):
    def test_inspect_store_tool_and_doctor_store_dir(self):
        import tempfile

        from documa.interfaces.tools import doctor_tool, inspect_store_tool

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store = self._seed(Path(tmp))
            payload = inspect_store_tool(store_dir=str(store))
            self.assertEqual(payload["status"], "ok")

            doctor = doctor_tool(project_root=str(REPO_ROOT), include_benchmark=False, store_dir=str(store))
            self.assertIn("store_health", doctor)
            self.assertEqual(doctor["store_health"]["document_count"], 1)

    def _seed(self, tmp_path: Path) -> Path:
        store = tmp_path / ".documa"
        doc = tmp_path / "note.md"
        doc.write_text("# t\n\nbody\n", encoding="utf-8")
        ingest_document(str(doc), store_dir=store)
        return store


if __name__ == "__main__":
    unittest.main()
