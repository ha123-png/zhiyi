"""A public product catalog must work without weakening private governance."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("doc_governance", ROOT / "scripts/check-doc-governance.py")
assert SPEC and SPEC.loader
governance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(governance)


class ProductCatalogTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".local/tmp/2026-10-30-polish"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="public-doc-contract-", dir=parent)
        self.root = Path(self.temporary.name)
        self.catalog = {
            "publication_scope": "product",
            "authoritative_current_document": "README.md",
            "current_docs": ["README.md", "docs/start.md"],
            "entries": [{"path": "README.md"}, {"path": "docs/start.md"}],
        }
        self.write("README.md", "# Product\n[Start](docs/start.md)\n[Source](apps/sample.py)\n")
        self.write("docs/start.md", "[Home](../README.md)\n[Upstream](https://example.test/product)\n")
        self.write("apps/sample.py", "# public source\n")

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name, contents):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")

    def check(self):
        self.write("docs/document-status.json", json.dumps(self.catalog))
        return governance.check(self.root)

    def test_explicit_product_catalog_passes_without_private_ledgers(self):
        result = self.check()
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["current_documents_checked"], 2)
        self.assertFalse(result["private_history_ledgers_checked"])
        self.assertFalse(result["product_tests_run"])

    def test_existing_uncatalogued_history_link_is_not_silently_published(self):
        self.write("docs/history/local-review.md", "# Private historical review\n")
        self.write("README.md", "[Old review](docs/history/local-review.md)\n")
        result = self.check()
        self.assertFalse(result["passed"])
        self.assertEqual(result["broken_current_links"], [{"file": "README.md", "target": "docs/history/local-review.md"}])

    def test_local_private_and_absolute_links_fail_even_when_target_exists(self):
        self.write(".local/evidence/fixture.md", "# Disposable fixture\n")
        self.write("README.md", "[Local](.local/evidence/fixture.md)\n[Disk](file:///D:/fixture.md)\n")
        result = self.check()
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["private_or_external_file_links"]), 2)

    def test_public_readme_html_image_must_exist(self):
        self.write("README.md", '<img src="docs/assets/preview.png" alt="preview" />\n')
        result = self.check()
        self.assertFalse(result["passed"])
        self.assertEqual(result["broken_current_links"], [{"file": "README.md", "target": "docs/assets/preview.png"}])
        self.write("docs/assets/preview.png", "disposable image path fixture")
        self.assertTrue(self.check()["passed"])

    def test_current_docs_must_be_explicit_and_catalogued(self):
        self.catalog.pop("current_docs")
        self.assertFalse(self.check()["passed"])
        self.catalog["current_docs"] = ["README.md", "docs/unlisted.md"]
        self.write("docs/unlisted.md", "# Not catalogued\n")
        result = self.check()
        self.assertFalse(result["passed"])
        self.assertTrue(any(item["path"] == "docs/unlisted.md" for item in result["invalid_catalog_paths"]))

    def test_missing_duplicates_outside_and_key_entries_are_rejected(self):
        self.catalog["entries"] += [
            {"path": "README.md"}, {"path": "docs/missing.md"},
            {"path": "../outside.md"}, {"path": "docs/synthetic.key"},
        ]
        result = self.check()
        self.assertFalse(result["passed"])
        self.assertEqual(result["duplicate_documents"], ["README.md"])
        self.assertEqual(result["missing_documents"], ["docs/missing.md"])
        self.assertEqual({item["path"] for item in result["invalid_catalog_paths"]}, {"../outside.md", "docs/synthetic.key"})

    def test_default_scope_still_requires_and_checks_private_evidence(self):
        self.catalog.pop("publication_scope")
        with self.assertRaises(FileNotFoundError):
            self.check()
        for name in ("CHANGELOG.md", "docs/README.md", "docs/INDEX.md", "docs/开发/README.md",
                     "docs/设计/README.md", "docs/history/README.md", "scripts/INDEX.md", "docs/开发/审计与证据治理.md"):
            self.write(name, "# Fixture\n")
        self.write("docs/开发/v0.6.0-深度审计覆盖.json", json.dumps({"workstreams": [], "artifact": ".local/evidence/v0.6.0/missing.json"}))
        self.write("docs/开发/v0.6.0-生产工作地图.json", json.dumps({"workstreams": []}))
        (self.root / ".local/evidence/v0.6.0").mkdir(parents=True)
        result = self.check()
        self.assertFalse(result["passed"])
        self.assertIn(".local/evidence/v0.6.0/missing.json", result["missing_local_evidence"])


if __name__ == "__main__":
    unittest.main()
