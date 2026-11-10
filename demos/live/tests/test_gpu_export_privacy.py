"""Portable export labels and sanitized-copy disclosure; no GPU run or export."""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/export_gpu_evidence.py'
SPEC = spec_from_file_location('gpu_export_privacy', SCRIPT)
exporter = module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


class GPUExportPrivacyTests(unittest.TestCase):
    def test_bundled_labels_survive_repository_relocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            contexts = []
            for directory in ('first-checkout', 'relocated-checkout'):
                bundle = Path(temporary) / directory / 'evidence/gpu-case1'
                bundle.mkdir(parents=True)
                with patch.object(exporter, 'DEFAULT_BUNDLE', bundle):
                    contexts.append(exporter.source_context(bundle))
            self.assertEqual(contexts[0], contexts[1])
            self.assertEqual(contexts[0], {
                'source_root': 'evidence/gpu-case1', 'source_bundle': 'gpu-case1',
            })

    def test_external_bundle_never_exposes_directory_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / 'private-owner' / 'private-run-name'
            bundle.mkdir(parents=True)
            context = exporter.source_context(bundle)
            self.assertEqual(context, {
                'source_root': 'external_evidence_bundle',
                'source_bundle': 'external_evidence_bundle',
            })
            self.assertNotIn('private-owner', json.dumps(context))
            self.assertNotIn('private-run-name', json.dumps(context))
            self.assertNotIn(temporary, json.dumps(context))

    def test_sanitized_marker_adds_explicit_hash_and_check_scopes(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / 'evidence/gpu-case1'
            bundle.mkdir(parents=True)
            marker = bundle.parent / 'public_release.json'
            marker.write_text(json.dumps({
                'status': 'sanitized_public_copy',
                'internal_note': 'never-copy-this-private-marker-field',
            }), encoding='utf-8')
            with patch.object(exporter, 'DEFAULT_BUNDLE', bundle):
                context = exporter.source_context(bundle)
            privacy = context['privacy']
            self.assertEqual(privacy['status'], 'sanitized_public_copy')
            self.assertEqual(privacy['source_hash_scope'], 'sanitized_public_copies')
            self.assertEqual(privacy['execution_checks_scope'], 'historical_original_execution_checks')
            self.assertEqual(privacy['checkpoint_digest_scope'], 'retained_historical_digests_not_rehashed')
            self.assertEqual(set(privacy['pseudonymized_fields']), {
                'personal_paths', 'host_identifiers', 'gpu_uuids', 'pci_addresses',
            })
            self.assertIn('11 项执行检查', privacy['disclosure'])
            self.assertNotIn('never-copy-this-private-marker-field', json.dumps(context))

    def test_other_marker_status_does_not_claim_sanitization(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / 'bundle'
            bundle.mkdir()
            (bundle.parent / 'public_release.json').write_text(
                json.dumps({'status': 'original_record'}), encoding='utf-8')
            self.assertNotIn('privacy', exporter.source_context(bundle))

    def test_malformed_marker_does_not_silently_drop_disclosure(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / 'bundle'
            bundle.mkdir()
            marker = bundle.parent / 'public_release.json'
            for content in ('{broken', '[]'):
                marker.write_text(content, encoding='utf-8')
                with self.subTest(content=content), self.assertRaises(ValueError):
                    exporter.source_context(bundle)


if __name__ == '__main__':
    unittest.main(verbosity=2)
