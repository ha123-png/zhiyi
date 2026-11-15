"""Exercise release-script boundaries with disposable Git and empty candidate fixtures."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")
FIXTURE_PARENT = ROOT / ".local/tmp/2026-10-30-polish"
EVIDENCE_PARENT = ROOT / ".local/evidence/v0.6.0/2026-10-30-polish/stage2-resume/release-preflight/contracts"


@unittest.skipUnless(os.name == "nt" and PWSH, "Windows PowerShell 7 contract")
class PublicSourceComparisonTests(unittest.TestCase):
    def setUp(self):
        FIXTURE_PARENT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="release-compare-", dir=FIXTURE_PARENT)
        self.root = Path(self.temporary.name)
        self.private = self.root / "private"
        self.public = self.root / "public"
        self.script = self.private / "scripts/compare-public-source.ps1"
        for repo in (self.private, self.public):
            (repo / "scripts").mkdir(parents=True)
            (repo / "apps/web/src").mkdir(parents=True)
            (repo / "apps/web/src/existing.ts").write_text("export const existing = true;\n")
            (repo / "VERSION").write_text("0.0.0\n")
            (repo / "package.json").write_text('{}\n')
            (repo / "package-lock.json").write_text('{}\n')
            (repo / ".gitignore").write_text(".local/\n*.key\n*.pem\n")
            shutil.copyfile(ROOT / "scripts/compare-public-source.ps1", repo / "scripts/compare-public-source.ps1")
            self.git(repo, "init", "--quiet")
            self.git(repo, "add", ".")
            self.git(repo, "-c", "user.name=Release contract", "-c", "user.email=contract@example.invalid", "commit", "-qm", "fixture")
        self.git(self.public, "remote", "add", "origin", "https://github.com/ha123-png/zhiyi.git")

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, root, *args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)

    def compare(self):
        return subprocess.run([PWSH, "-NoProfile", "-File", str(self.script), "-PublicWorktree", "../public"],
                              capture_output=True, text=True, encoding="utf-8", timeout=25)

    def test_equal_product_sources_pass(self):
        result = self.compare()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_new_nonignored_component_cannot_disappear_from_comparison(self):
        (self.private / "apps/web/src/new-component.tsx").write_text("export const NewComponent = () => null;\n")
        result = self.compare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing-public: apps/web/src/new-component.tsx", result.stdout)

    def test_ignored_local_key_is_not_read_or_required_in_public_source(self):
        private = self.private / "apps/web/.local/private"
        private.mkdir(parents=True)
        (private / "synthetic.key").write_text("disposable fixture, not a credential\n")
        result = self.compare()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_public_tracked_key_path_fails_closed(self):
        (self.public / "apps/web/synthetic.pem").write_text("disposable fixture, not a credential\n")
        self.git(self.public, "add", "-f", "apps/web/synthetic.pem")
        self.git(self.public, "-c", "user.name=Release contract", "-c", "user.email=contract@example.invalid", "commit", "-qm", "excluded fixture")
        result = self.compare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("excluded runtime/private path", result.stderr)


@unittest.skipUnless(os.name == "nt" and PWSH, "Windows PowerShell 7 contract")
class LifecyclePathTests(unittest.TestCase):
    def setUp(self):
        FIXTURE_PARENT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="release-lifecycle-", dir=FIXTURE_PARENT)
        self.root = Path(self.temporary.name)
        self.dist = self.root / "empty-dist"
        self.dist.mkdir()
        self.parents = self.root / "runs"
        self.evidence = EVIDENCE_PARENT / uuid.uuid4().hex

    def tearDown(self):
        self.temporary.cleanup()
        if self.evidence.exists():
            self.assertTrue(self.evidence.resolve().is_relative_to(EVIDENCE_PARENT.resolve()))
            shutil.rmtree(self.evidence)

    def run_smoke(self, temporary=None, evidence=None, test_root=None):
        return subprocess.run([
            PWSH, "-NoProfile", "-File", str(ROOT / "scripts/smoke-release-lifecycle.ps1"),
            "-DistRoot", str(self.dist), "-TemporaryParent", str(temporary or self.parents),
            "-EvidenceParent", str(evidence or self.evidence),
            "-TestRoot", str(test_root or self.parents / "contract-run"),
        ], capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_custom_parents_reach_candidate_validation_only_in_selected_roots(self):
        result = self.run_smoke()
        # An empty dist intentionally cannot start a packaged app. Reaching the
        # candidate verifier proves the real script accepted and used both roots.
        self.assertNotEqual(result.returncode, 0)
        report = self.evidence / "contract-run/lifecycle-report.json"
        self.assertTrue(report.is_file(), result.stdout + result.stderr)
        import json
        saved = json.loads(report.read_text(encoding="utf-8-sig"))
        self.assertEqual(Path(saved["test_root"]), self.parents / "contract-run")
        self.assertEqual(Path(saved["evidence_root"]), self.evidence / "contract-run")
        self.assertEqual(saved["steps"][0]["name"], "verify-candidate-manifests-and-complete-payload")
        self.assertEqual(saved["steps"][0]["status"], "failed")
        self.assertEqual(saved["cleanup"], [])

    def test_temporary_parent_cannot_escape_tmp_category(self):
        outside = ROOT / ".local" / ("tmp-prefix-escape-" + uuid.uuid4().hex)
        result = self.run_smoke(temporary=outside, test_root=outside / "run")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TemporaryParent must remain", result.stderr)
        self.assertFalse(outside.exists())
        self.assertFalse(self.evidence.exists())

    def test_evidence_parent_cannot_use_runtime_or_private_category(self):
        outside = ROOT / ".local" / ("runtime-prefix-escape-" + uuid.uuid4().hex)
        result = self.run_smoke(evidence=outside)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EvidenceParent must remain", result.stderr)
        self.assertFalse(outside.exists())
        self.assertFalse(self.parents.exists())

    def test_link_ancestor_is_rejected_before_creating_target_descendants(self):
        target = self.root / "link-target"
        target.mkdir()
        junction = self.root / "junction"
        creation = subprocess.run([PWSH, "-NoProfile", "-Command",
            f"New-Item -ItemType Junction -Path '{junction}' -Target '{target}' | Out-Null"],
            capture_output=True, text=True, encoding="utf-8", timeout=10)
        if creation.returncode:
            self.skipTest("Directory junction unavailable")
        try:
            parent = junction / "must-not-be-created"
            result = self.run_smoke(temporary=parent, test_root=parent / "run")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not traverse links", result.stderr)
            self.assertFalse((target / "must-not-be-created").exists())
        finally:
            # Remove the fixture junction itself, never recurse through it.
            subprocess.run([PWSH, "-NoProfile", "-Command", f"Remove-Item -LiteralPath '{junction}' -Force"],
                           capture_output=True, text=True, check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
