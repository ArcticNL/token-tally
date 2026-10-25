"""Shared, data-only release descriptor validation (stdlib, Python 3.9+)."""
import re

SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
IMAGE = re.compile(r"ghcr\.io/[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*\Z")
PLATFORMS = {"linux/amd64", "linux/arm64"}


def version_tuple(version):
    match = VERSION.fullmatch(version) if isinstance(version, str) else None
    if not match:
        raise ValueError("expected stable vMAJOR.MINOR.PATCH version")
    return tuple(int(part) for part in match.groups())


def validate(data, image=None):
    keys = {"schema", "channel", "version", "source_sha", "image", "digest", "platforms", "data_schema"}
    if not isinstance(data, dict) or set(data) != keys:
        raise ValueError("unexpected release descriptor fields")
    if type(data["schema"]) is not int or data["schema"] != 1 or data["channel"] != "stable":
        raise ValueError("unsupported release schema or channel")
    if type(data["data_schema"]) is not int or data["data_schema"] != 1:
        raise ValueError("automatic updates do not support this data schema")
    version_tuple(data["version"])
    for field, pattern in (("source_sha", SHA), ("digest", DIGEST), ("image", IMAGE)):
        if not isinstance(data[field], str) or not pattern.fullmatch(data[field]):
            raise ValueError("invalid release " + field)
    if image is not None and data["image"] != image:
        raise ValueError("release image is outside configured repository")
    if not isinstance(data["platforms"], dict) or set(data["platforms"]) != PLATFORMS:
        raise ValueError("release must contain exactly amd64 and arm64")
    if any(not isinstance(digest, str) or not DIGEST.fullmatch(digest) for digest in data["platforms"].values()):
        raise ValueError("invalid platform digest")
    return data


def image_reference(data):
    validate(data)
    return data["image"] + "@" + data["digest"]
