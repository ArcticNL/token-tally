"""Static artifact-flow regressions; no GitHub calls, credentials or publication.

Inspect the workflow's explicit block/scalar structure, then resolve its actual
artifact expressions against retained producer outputs from partial reruns.
Actionlint separately validates the complete YAML and GitHub expression syntax.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def block(source, key, indent):
    lines = source.splitlines()
    start = lines.index(" " * indent + key + ":") + 1
    end = start
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line.lstrip().startswith("#") and len(line) - len(line.lstrip()) <= indent:
            break
        end += 1
    return "\n".join(lines[start:end])


def scalar(source, key, indent):
    matches = re.findall(r"^" + " " * indent + re.escape(key) + r": (.+)$", source, re.MULTILINE)
    if len(matches) != 1:
        raise AssertionError("expected exactly one scalar: " + key)
    return matches[0]


def action(source, name):
    steps = re.split(r"(?=^      - )", block(source, "steps", 4), flags=re.MULTILINE)
    matches = [step for step in steps if re.search(r"uses: actions/" + re.escape(name) + r"@[0-9a-f]{40}\b", step)]
    if len(matches) != 1:
        raise AssertionError("expected exactly one action: " + name)
    return matches[0]


def resolve(expression, values):
    def substitute(match):
        return values[match.group(1).strip()]
    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", substitute, expression)


class ArtifactFlowTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / ".github/workflows/release.yml").read_text()
        self.jobs = block(self.source, "jobs", 0)
        self.builder = (ROOT / ".github/workflows/release-build.yml").read_text()

    def job(self, name):
        return block(self.jobs, name, 2)

    def build_output(self, architecture, artifact_id):
        job = self.job("build_" + architecture)
        self.assertEqual(scalar(job, "uses", 4), "./.github/workflows/release-build.yml")
        self.assertEqual(scalar(job, "arch", 6), architecture)
        self.assertEqual(scalar(job, "needs", 4), "check")
        # Independent caller outputs avoid last-writer-wins matrix outputs.
        self.assertNotIn("strategy:", job)
        producer = block(block(self.builder, "jobs", 0), "build", 2)
        upload = action(producer, "upload-artifact")
        upload_id = scalar(upload, "id", 8)
        self.assertEqual(scalar(upload, "if-no-files-found", 10), "error")
        job_value = resolve(scalar(block(producer, "outputs", 4), "artifact_id", 6),
                            {"steps." + upload_id + ".outputs.artifact-id": artifact_id})
        call = block(block(self.builder, "on", 0), "workflow_call", 2)
        output = block(block(call, "outputs", 4), "artifact_id", 6)
        return resolve(scalar(output, "value", 8), {"jobs.build.outputs.artifact_id": job_value})

    def download(self, job, values):
        source = self.job(job)
        step = action(source, "download-artifact")
        inputs = block(step, "with", 8)
        self.assertNotRegex(inputs, r"(?m)^          (name|pattern|run-id|repository|github-token):")
        expression = scalar(inputs, "artifact-ids", 10)
        referenced = set(re.findall(r"needs\.([\w]+)\.outputs\.", expression))
        dependencies = set(re.findall(r"\w+", scalar(source, "needs", 4)))
        self.assertTrue(referenced <= dependencies)
        return resolve(expression, values).split(",")

    def test_failed_platform_retry_keeps_other_platform_artifact(self):
        amd64 = self.build_output("amd64", "101")  # Successful attempt 1 retained.
        arm64 = self.build_output("arm64", "202")  # Failed build succeeds in attempt 2.
        selected = self.download("index", {"needs.build_amd64.outputs.artifact_id": amd64,
                                           "needs.build_arm64.outputs.artifact_id": arm64,
                                           "github.run_attempt": "2"})
        artifacts = {"101": {"amd64.digest": "sha256:amd64"}, "202": {"arm64.digest": "sha256:arm64"}}
        files = {path: value for artifact_id in selected for path, value in artifacts[artifact_id].items()}
        self.assertEqual(files, {"amd64.digest": "sha256:amd64", "arm64.digest": "sha256:arm64"})
        self.assertEqual(scalar(action(self.job("index"), "download-artifact"), "merge-multiple", 10), "true")

    def test_downstream_retries_use_the_original_tested_descriptor(self):
        index = self.job("index")
        upload = action(index, "upload-artifact")
        upload_id = scalar(upload, "id", 8)
        self.assertEqual(scalar(upload, "path", 10), "release.json")
        self.assertEqual(scalar(upload, "if-no-files-found", 10), "error")
        descriptor = resolve(scalar(block(index, "outputs", 4), "descriptor_artifact_id", 6),
                             {"steps." + upload_id + ".outputs.artifact-id": "301"})
        for attempt in (2, 3):
            for job in ("promote", "publish"):
                with self.subTest(attempt=attempt, job=job):
                    self.assertEqual(self.download(job, {"needs.index.outputs.descriptor_artifact_id": descriptor,
                                                        "github.run_attempt": str(attempt)}), ["301"])

    def test_reusable_build_keeps_policy_credentials_outside_candidate_jobs(self):
        self.assertNotIn("RELEASE_POLICY_TOKEN", self.builder)
        self.assertNotIn("secrets:", self.builder)
        for architecture in ("amd64", "arm64"):
            self.assertNotIn("secrets:", self.job("build_" + architecture))
        permissions = block(block(block(self.builder, "jobs", 0), "build", 2), "permissions", 4)
        self.assertEqual(scalar(permissions, "contents", 6), "read")
        self.assertEqual(scalar(permissions, "packages", 6), "write")


if __name__ == "__main__":
    unittest.main()
