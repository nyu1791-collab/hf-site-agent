import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest

from scripts import audit_secret_safety


class SecretSafetyTests(unittest.TestCase):
    def test_environment_reference_is_not_a_secret_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "safe.yml"
            path.write_text("api_key: ${AI_API_KEY}\n", encoding="utf-8")
            self.assertEqual(audit_secret_safety.scan_paths(Path(directory)), [])

    def test_literal_secret_is_reported_without_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.json"
            secret = "gsk_" + "live_value_must_not_be_printed"
            path.write_text(f'{{"api_key": "{secret}"}}\n', encoding="utf-8")
            findings = audit_secret_safety.scan_paths(Path(directory))
            self.assertTrue(findings)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(audit_secret_safety.main(["--root", directory]), 1)
            self.assertNotIn(secret, output.getvalue())
            self.assertIn("SECRET_CANDIDATE_FOUND=true", output.getvalue())

    def test_plaintext_secret_binding_is_reported_without_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            secret = "admin_" + "password_value_must_not_be_printed"
            binding = (
                '{"type":"' + "plain_text" + '","name":"ADMIN_PASSWORD","text":"'
                + secret + '"}\n'
            )
            path.write_text(binding, encoding="utf-8")
            findings = audit_secret_safety.scan_paths(Path(directory))
            self.assertEqual({finding.kind for finding in findings}, {"plaintext_secret_binding"})
            output = io.StringIO()
            with redirect_stdout(output):
                audit_secret_safety.main(["--root", directory])
            self.assertNotIn(secret, output.getvalue())

    def test_direct_secret_output_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workflow.yml"
            path.write_text('run: echo "${{ ' + 'secrets.ADMIN_PASSWORD }}"\n', encoding="utf-8")
            findings = audit_secret_safety.scan_paths(Path(directory))
            self.assertEqual({finding.kind for finding in findings}, {"secret_output"})

    def test_provider_names_and_error_labels_are_not_secret_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "safe.js"
            path.write_text(
                "const env = HF_INFERENCE_API_KEY;\n"
                "const pattern = 'hf_[A-Za-z0-9_-]{12,}';\n"
                "log('hf_network_error');\n"
                "log('hf_review_unavailable');\n",
                encoding="utf-8",
            )
            self.assertEqual(audit_secret_safety.scan_paths(Path(directory)), [])


if __name__ == "__main__":
    unittest.main()
