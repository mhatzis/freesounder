#!/usr/bin/env python3
"""Fetch one platform of a public OCI/Docker image into a `docker load` archive.

No Docker daemon is needed, so pi-gen can preload Music Assistant into the
player image. Blobs are cached and verified by sha256; the archive uses the
classic docker-archive layout (manifest.json + config + gzip layer blobs), which
`docker load` from Docker 20.10 onwards accepts.

    fetch-docker-archive.py ghcr.io/music-assistant/server:2.10.9 out.tar \
        --platform linux/arm64 --cache ~/.cache/freesounder-ma
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

INDEX_TYPES = (
    "application/vnd.oci.image.index.v1+json,"
    "application/vnd.docker.distribution.manifest.list.v2+json"
)
MANIFEST_TYPES = (
    "application/vnd.oci.image.manifest.v1+json,"
    "application/vnd.docker.distribution.manifest.v2+json"
)


def parse_ref(ref: str) -> tuple[str, str, str]:
    registry, _, rest = ref.partition("/")
    repo, _, tag = rest.rpartition(":")
    if not repo or not tag:
        raise SystemExit(f"Expected registry/repo:tag, got {ref}")
    return registry, repo, tag


class Registry:
    def __init__(self, registry: str, repo: str) -> None:
        self.base = f"https://{registry}/v2/{repo}"
        url = f"https://{registry}/token?scope=repository:{repo}:pull"
        self.token = json.load(urllib.request.urlopen(url, timeout=60))["token"]

    def open(self, path: str, accept: str = "*/*"):
        request = urllib.request.Request(
            f"{self.base}/{path}",
            headers={"Authorization": f"Bearer {self.token}", "Accept": accept},
        )
        return urllib.request.urlopen(request, timeout=120)

    def json(self, path: str, accept: str) -> dict:
        with self.open(path, accept) as response:
            return json.load(response)


def fetch_blob(reg: Registry, digest: str, cache: Path) -> Path:
    algo, _, hexdigest = digest.partition(":")
    if algo != "sha256":
        raise SystemExit(f"Unsupported digest {digest}")
    dest = cache / hexdigest
    if dest.is_file() and _sha256(dest) == hexdigest:
        return dest
    for attempt in range(1, 6):
        try:
            tmp = dest.with_suffix(".part")
            hasher = hashlib.sha256()
            with reg.open(f"blobs/{digest}") as response, tmp.open("wb") as out:
                while chunk := response.read(1 << 20):
                    hasher.update(chunk)
                    out.write(chunk)
            if hasher.hexdigest() != hexdigest:
                raise ValueError(f"sha256 mismatch for {digest}")
            tmp.replace(dest)
            return dest
        except (OSError, ValueError, urllib.error.URLError) as exc:
            print(f"blob {digest[:19]} attempt {attempt} failed: {exc}", file=sys.stderr)
            time.sleep(3 * attempt)
    raise SystemExit(f"Could not fetch {digest}")


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            hasher.update(chunk)
    return hasher.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ref")
    parser.add_argument("output", type=Path)
    parser.add_argument("--platform", default="linux/arm64")
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "fs-oci")
    args = parser.parse_args()

    os_name, _, arch = args.platform.partition("/")
    registry, repo, tag = parse_ref(args.ref)
    args.cache.mkdir(parents=True, exist_ok=True)
    reg = Registry(registry, repo)
    top = reg.json(f"manifests/{tag}", f"{INDEX_TYPES},{MANIFEST_TYPES}")
    if "manifests" in top:
        matches = [
            item
            for item in top["manifests"]
            if item.get("platform", {}).get("architecture") == arch
            and item.get("platform", {}).get("os") == os_name
        ]
        if not matches:
            raise SystemExit(f"No {args.platform} manifest for {args.ref}")
        manifest_digest = matches[0]["digest"]
        manifest = reg.json(f"manifests/{manifest_digest}", MANIFEST_TYPES)
    else:
        manifest = top
        manifest_digest = "(single-platform)"

    config_path = fetch_blob(reg, manifest["config"]["digest"], args.cache)
    config = json.loads(config_path.read_text())
    if config.get("architecture") != arch:
        raise SystemExit(f"Config architecture {config.get('architecture')} != {arch}")
    layers = [fetch_blob(reg, layer["digest"], args.cache) for layer in manifest["layers"]]

    config_name = f"{manifest['config']['digest'].split(':')[1]}.json"
    layer_names = [f"{path.name}/layer.tar" for path in layers]
    docker_manifest = [
        {"Config": config_name, "RepoTags": [args.ref], "Layers": layer_names}
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=args.output.parent, suffix=".tar.part")
    os.close(fd)
    with tarfile.open(tmp_name, "w", format=tarfile.GNU_FORMAT) as archive:
        archive.add(config_path, arcname=config_name)
        for path, name in zip(layers, layer_names, strict=True):
            archive.add(path, arcname=name)
        payload = json.dumps(docker_manifest).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        info.mtime = int(time.time())
        import io

        archive.addfile(info, io.BytesIO(payload))
    Path(tmp_name).replace(args.output)
    total = sum(path.stat().st_size for path in layers)
    print(
        f"{args.ref} {args.platform} manifest {manifest_digest} "
        f"config {manifest['config']['digest']} layers {len(layers)} {total / 1e6:.0f} MB "
        f"-> {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
