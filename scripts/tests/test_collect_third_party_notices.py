import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "collect-third-party-notices.py"
SPEC = importlib.util.spec_from_file_location("collect_notices", SCRIPT)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


class RuntimeInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.requirements = self.root / "requirements.txt"
        self.audit = self.root / "audit.json"

    def write_inputs(self, requirements, versions):
        self.requirements.write_text(requirements, encoding="utf-8")
        self.audit.write_text(json.dumps({"dependencies": [
            {"name": name, "version": version, "vulns": []}
            for name, version in versions.items()
        ]}), encoding="utf-8")

    def test_resolved_extras_and_platform_markers_share_audit_inventory(self):
        self.write_inputs(
            '# frozen uv export\nPyJWT[crypto]==2.15.1 \\\n'
            '  --hash=sha256:' + 'a' * 64 + '\n'
            '# via mcp\ncryptography==50.0.0\n'
            'unused==1.0; python_version < "2"\n',
            {"pyjwt": "2.15.1", "cryptography": "50.0.0"},
        )
        self.assertEqual(collector._runtime_python_versions(self.requirements, self.audit),
                         {"pyjwt": "2.15.1", "cryptography": "50.0.0"})

    def test_missing_audit_record_or_unpinned_input_fails_closed(self):
        for declaration, versions in [
            ("pyjwt==2.15.1\ncryptography==50.0.0", {"pyjwt": "2.15.1"}),
            ("pyjwt>=2.15.1", {"pyjwt": "2.15.1"}),
            ("pyjwt==2.15.1", {"pyjwt": "2.13.0"}),
            ("# empty export", {}),
        ]:
            with self.subTest(declaration=declaration):
                self.write_inputs(declaration, versions)
                with self.assertRaises(RuntimeError):
                    collector._runtime_python_versions(self.requirements, self.audit)

    def test_installed_version_drift_and_missing_license_file_fail_closed(self):
        distribution = Mock()
        distribution.metadata = {"Name": "PyJWT", "License-Expression": "MIT"}
        distribution.version = "2.13.0"
        distribution.files = []
        with patch.object(collector.metadata, "distributions", return_value=[distribution]):
            with self.assertRaisesRegex(RuntimeError, "differs from frozen"):
                collector._copy_python_licenses(self.root, {"pyjwt": "2.15.1"})
            distribution.version = "2.15.1"
            with self.assertRaisesRegex(RuntimeError, "no license file"):
                collector._copy_python_licenses(self.root, {"pyjwt": "2.15.1"})


if __name__ == "__main__":
    unittest.main()
