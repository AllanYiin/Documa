import unittest
from pathlib import Path

from documa.core.ir import FixtureIssueType
from documa.quality.fixture_manifest import load_fixture_manifest


class FixtureManifestTests(unittest.TestCase):
    def test_manifest_covers_all_required_issue_types(self):
        manifest_path = Path("fixtures/pdf/manifest.json")
        manifest = load_fixture_manifest(manifest_path)

        expected = {item.value for item in FixtureIssueType}
        self.assertEqual(manifest.issue_type_values(), expected)

    def test_each_case_has_expected_capability_contract(self):
        manifest = load_fixture_manifest("fixtures/pdf/manifest.json")

        for case in manifest.cases:
            with self.subTest(case=case.id):
                self.assertTrue(case.id)
                self.assertTrue(case.title)
                self.assertGreaterEqual(len(case.expected_capabilities), 2)

