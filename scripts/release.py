"""Manual release workflow helpers. No mutation unless explicitly using publish/promote."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi"))
from release_contract import DIGEST, SHA, validate, version_tuple


class GitHub:
    def __init__(self, repository, token=None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("invalid repository")
        self.repository, self.token = repository, token or os.environ.get("GH_TOKEN", "")

    def request(self, path, method="GET", data=None, optional=False):
        url = "https://api.github.com/repos/" + self.repository + "/" + path
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "token-tally-release"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if optional and error.code == 404:
                return None
            raise RuntimeError("GitHub " + method + " " + path.split("?")[0] + " returned " + str(error.code)) from None

    def pages(self, path, key):
        items = []
        for page in range(1, 101):
            data = self.request(path + ("&" if "?" in path else "?") + "per_page=100&page=" + str(page))
            batch = data[key]
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise ValueError("GitHub pagination exceeded safe bound")


def successful_run(runs, jobs, repository, source_sha, workflow, names):
    eligible = [run for run in runs if run.get("head_sha") == source_sha and run.get("event") == "push"
                and run.get("head_branch") == "main" and run.get("path") == ".github/workflows/" + workflow
                and run.get("head_repository", {}).get("full_name", "").lower() == repository.lower()]
    if not eligible or not names:
        raise ValueError("missing trusted checks for " + workflow)
    latest = max(eligible, key=lambda run: (run["run_number"], run["run_attempt"]))
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ValueError("latest run did not succeed: " + workflow)
    actual = jobs(latest)
    for name in names:
        matches = [job for job in actual if job.get("name") == name]
        if len(matches) != 1 or matches[0].get("status") != "completed" or matches[0].get("conclusion") != "success":
            raise ValueError("required job did not succeed: " + name)
    return latest["html_url"]


def output(name, value):
    if "\n" in str(value):
        raise ValueError("unsafe workflow output")
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write(name + "=" + str(value) + "\n")


def command(args):
    return subprocess.check_output(args, text=True).strip()


def preflight(repository, source_sha, version):
    version_tuple(version)
    if not SHA.fullmatch(source_sha):
        raise ValueError("full source SHA required")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("release workflow must run from trusted main")
    api = GitHub(repository)
    metadata = api.request("")
    if metadata["default_branch"] != "main":
        raise ValueError("release policy currently requires main as default branch")
    command(["git", "merge-base", "--is-ancestor", source_sha, "HEAD"])
    # Avoid needing workflow-write rights when creating the release's source tag.
    if command(["git", "diff", "--name-only", source_sha, "HEAD", "--", ".github/workflows"]):
        raise ValueError("candidate workflow tree differs from trusted main")
    policy = GitHub(repository, os.environ.get("RELEASE_POLICY_TOKEN") or os.environ.get("GH_TOKEN"))
    if policy.request("immutable-releases").get("enabled") is not True:
        raise ValueError("enable immutable releases before publication")
    if api.request("git/ref/tags/" + version, optional=True) or api.request("releases/tags/" + version, optional=True):
        raise ValueError("version already exists; never overwrite or rebuild a published version")
    required = json.loads((ROOT / ".github/release-checks.json").read_text())
    if not required:
        raise ValueError("required check policy is empty")
    for workflow, names in required.items():
        runs = api.pages("actions/workflows/" + workflow + "/runs?head_sha=" + source_sha + "&event=push&branch=main", "workflow_runs")
        def jobs(run):
            return api.pages("actions/runs/" + str(run["id"]) + "/attempts/" + str(run["run_attempt"]) + "/jobs", "jobs")
        successful_run(runs, jobs, repository, source_sha, workflow, names)
    output("source_sha", source_sha)
    output("version", version)
    output("image", "ghcr.io/" + repository.lower())


def registry_index(reference):
    raw = subprocess.check_output(["docker", "buildx", "imagetools", "inspect", "--raw", reference])
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    data = json.loads(raw)
    platforms = {}
    for entry in data.get("manifests", []):
        platform = entry.get("platform", {})
        key = platform.get("os", "") + "/" + platform.get("architecture", "")
        if key in platforms or key not in ("linux/amd64", "linux/arm64"):
            raise ValueError("unexpected or duplicate executable platform")
        platforms[key] = entry["digest"]
    if set(platforms) != {"linux/amd64", "linux/arm64"}:
        raise ValueError("index must contain both supported architectures")
    return digest, platforms


def make_manifest(image, source_sha, version, candidate, destination):
    digest, platforms = registry_index(candidate)
    data = validate({"schema": 1, "channel": "stable", "version": version, "source_sha": source_sha,
                     "image": image, "digest": digest, "platforms": platforms, "data_schema": 1})
    Path(destination).write_text(json.dumps(data, indent=2) + "\n")
    output("digest", digest)


def existing_tag(image, version):
    """Only an authenticated registry MANIFEST_UNKNOWN response means absent."""
    repository = image.removeprefix("ghcr.io/")
    query = urllib.parse.urlencode({"service": "ghcr.io", "scope": "repository:" + repository + ":pull"})
    headers = {"User-Agent": "token-tally-release"}
    token = os.environ.get("GH_TOKEN")
    if token:
        credentials = (os.environ["GITHUB_ACTOR"] + ":" + token).encode()
        headers["Authorization"] = "Basic " + base64.b64encode(credentials).decode()
    with urllib.request.urlopen(urllib.request.Request("https://ghcr.io/token?" + query, headers=headers), timeout=30) as response:
        access = json.load(response)["token"]
    headers = {"Authorization": "Bearer " + access,
               "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json"}
    request = urllib.request.Request("https://ghcr.io/v2/" + repository + "/manifests/" + version, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return "sha256:" + hashlib.sha256(response.read()).hexdigest()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            data = json.loads(error.read())
            errors = data.get("errors", [])
            if errors and all(item.get("code") == "MANIFEST_UNKNOWN" for item in errors):
                return None
        raise RuntimeError("cannot establish version tag state: registry returned " + str(error.code)) from None


def promote(path):
    data = validate(json.loads(Path(path).read_text()))
    expected, platforms = registry_index(data["image"] + "@" + data["digest"])
    if expected != data["digest"] or platforms != data["platforms"]:
        raise ValueError("candidate index differs from tested descriptor")
    tag = data["image"] + ":" + data["version"]
    existing = existing_tag(data["image"], data["version"])
    if existing is not None:
        if existing != expected:
            raise ValueError("existing version image differs")
    else:
        subprocess.run(["docker", "buildx", "imagetools", "create", "--tag", tag,
                        data["image"] + "@" + expected], check=True)
    if registry_index(tag) != (expected, platforms):
        raise ValueError("promotion changed tested digest")


def publish(repository, path):
    data = validate(json.loads(Path(path).read_text()), "ghcr.io/" + repository.lower())
    api = GitHub(repository)
    policy = GitHub(repository, os.environ.get("RELEASE_POLICY_TOKEN") or os.environ.get("GH_TOKEN"))
    if policy.request("immutable-releases").get("enabled") is not True:
        raise ValueError("release immutability is not enabled")
    if registry_index(data["image"] + ":" + data["version"]) != (data["digest"], data["platforms"]):
        raise ValueError("promoted image differs from tested release")
    if api.request("git/ref/tags/" + data["version"], optional=True) or api.request("releases/tags/" + data["version"], optional=True):
        raise ValueError("version already exists; inspect partial publication manually")
    # Create the exact tag first: target_commitish cannot retarget an existing tag.
    api.request("git/refs", "POST", {"ref": "refs/tags/" + data["version"], "sha": data["source_sha"]})
    release = api.request("releases", "POST", {"tag_name": data["version"], "target_commitish": data["source_sha"],
        "name": data["version"], "draft": True, "prerelease": False,
        "body": "Tested source: " + data["source_sha"] + "\nImage: " + data["image"] + "@" + data["digest"]})
    # gh handles the separate uploads host. Only this publication job gets write rights.
    subprocess.run(["gh", "release", "upload", data["version"], str(path) + "#release.json", "--repo", repository], check=True)
    tag = api.request("git/ref/tags/" + data["version"])
    draft = api.request("releases/" + str(release["id"]))
    assets = [asset for asset in draft.get("assets", []) if asset["name"] == Path(path).name]
    expected_asset = "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if tag["object"]["sha"] != data["source_sha"] or len(assets) != 1 or assets[0].get("digest") != expected_asset:
        raise ValueError("draft tag or asset changed before publication")
    if policy.request("immutable-releases").get("enabled") is not True:
        raise ValueError("release immutability changed before publication")
    published = api.request("releases/" + str(release["id"]), "PATCH", {"draft": False, "make_latest": "true"})
    if published.get("immutable") is not True:
        raise RuntimeError("published release is unexpectedly mutable; stop automatic consumption")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    check = sub.add_parser("check")
    for name in ("repository", "source-sha", "version"):
        check.add_argument("--" + name, required=True)
    manifest = sub.add_parser("manifest")
    for name in ("image", "source-sha", "version", "candidate", "output"):
        manifest.add_argument("--" + name, required=True)
    promotion = sub.add_parser("promote")
    promotion.add_argument("manifest")
    publication = sub.add_parser("publish")
    publication.add_argument("manifest")
    publication.add_argument("--repository", required=True)
    args = parser.parse_args()
    if args.action == "check":
        preflight(args.repository, args.source_sha, args.version)
    elif args.action == "manifest":
        make_manifest(args.image, args.source_sha, args.version, args.candidate, args.output)
    elif args.action == "promote":
        promote(args.manifest)
    else:
        publish(args.repository, args.manifest)


if __name__ == "__main__":
    main()
