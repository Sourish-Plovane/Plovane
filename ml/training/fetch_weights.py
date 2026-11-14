"""Fetch an encoder's weights from a URL that this container can reach.

Pretrained encoder weights do not reach this environment through HuggingFace:
``huggingface.co`` is denied by the network policy and ``api.anthropic.com``
goes through, but ``storage.googleapis.com`` and ``github.com`` do too.
A GitHub release in the project's own org works today -- that is how spaCy's
model wheel arrived -- so this script is the general version of that: give it
a URL and a SHA-256, it downloads to ``ml/weights/<name>/``, verifies the
checksum, and unpacks a tar or zip in place.

The checksum is required rather than optional. A loader that trusts whatever
is at the other end of a URL silently loads whatever happened to be there,
which is the one thing a weights file must never do; if the hash does not
match the download is removed and the script exits non-zero.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

CHUNK = 1 << 20  # 1 MiB
WEIGHTS_ROOT = Path(__file__).resolve().parents[1] / "weights"
TOKEN_NAMES = ("GITHUB_TOKEN", "GH_TOKEN", "GH_RESULTS_TOKEN")
RELEASE_URL = re.compile(
    r"^https://github\.com/(?P<repo>[^/]+/[^/]+)/releases/download/(?P<tag>[^/]+)/(?P<name>[^/?]+)$"
)


# Input: none
# Output: str | None
# Processing: The first GitHub token set in the environment, by name only.
# Changed by: Sourish
# Date: 2026-10-17
def github_token() -> str | None:
    """The first GitHub token set in the environment, matched by name only."""
    for name in TOKEN_NAMES:
        for key, value in os.environ.items():
            if key.upper() == name and value:
                return value
    return None


# Input: url (str), token (str | None)
# Output: urllib.request.Request
# Processing: The request that downloads url, through the API for a private release asset.
# Changed by: Sourish
# Date: 2026-10-17
def asset_request(url: str, token: str | None) -> urllib.request.Request:
    """The request that downloads ``url``.

    A release asset in a private repository cannot be fetched from its
    ``github.com/.../releases/download`` link: that answers 404 to a token.
    The API's asset endpoint with ``Accept: application/octet-stream`` does,
    by redirecting to a signed storage URL. The token is added as an
    *unredirected* header so it is never sent on to that storage host, which
    would also reject a request carrying two credentials.
    """
    match = RELEASE_URL.match(url)
    if token is None or match is None:
        return urllib.request.Request(url)
    auth = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    lookup = urllib.request.Request(
        f"https://api.github.com/repos/{match['repo']}/releases/tags/{match['tag']}", headers=auth
    )
    with urllib.request.urlopen(lookup) as response:
        release = json.load(response)
    asset = next((a for a in release.get("assets", []) if a["name"] == match["name"]), None)
    if asset is None:
        raise RuntimeError(f"release {match['tag']} has no asset named {match['name']}")
    request = urllib.request.Request(asset["url"], headers={"Accept": "application/octet-stream"})
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    return request


# Input: url (str), destination (Path), expected_sha256 (str)
# Output: None
# Processing: Stream a URL to disk and verify its SHA-256
# Changed by: Sourish
# Date: 2026-10-13
def download(url: str, destination: Path, *, expected_sha256: str) -> None:
    """Download, hash as we go, and fail loudly on a mismatch.

    Streamed because the file is likely to be hundreds of megabytes; writing
    into a sibling path first and renaming on success means an interrupted
    download never looks like a finished one.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    hasher = hashlib.sha256()
    request = asset_request(url, github_token())
    with urllib.request.urlopen(request) as response, partial.open("wb") as handle:
        while chunk := response.read(CHUNK):
            handle.write(chunk)
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if actual != expected_sha256:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"SHA-256 mismatch at {url}: expected {expected_sha256}, got {actual}"
        )
    partial.replace(destination)


# Input: archive (Path), destination (Path)
# Output: None
# Processing: Extract a tar or zip archive into a directory
# Changed by: Sourish
# Date: 2026-10-13
def extract(archive: Path, destination: Path) -> None:
    """Unpack into a clean directory so a partial failure cannot shadow weights.

    Tar's ``data`` filter is used where available (Python 3.12+), which rejects
    absolute paths and symlinks that climb out of the extraction root. A tar
    without that protection could overwrite files outside the weights folder.
    """
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(destination)
        return
    if archive.name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
        with tarfile.open(archive) as tf:
            tf.extractall(destination, filter="data")
        return
    raise ValueError(f"unknown archive type: {archive.name}")


# Input: argv (list[str] | None)
# Output: int
# Processing: Download, verify and unpack an encoder
# Changed by: Sourish
# Date: 2026-10-13
def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a shell exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help="where under ml/weights/ the files will live")
    parser.add_argument("url", help="URL to download (release asset, bucket URL, ...)")
    parser.add_argument("sha256", help="expected SHA-256 of the downloaded file")
    parser.add_argument(
        "--extract",
        action="store_true",
        help="treat the download as a tar or zip and unpack it next to a file ``model.bin`` "
        "would land as; omit for a single-file artefact (a Pytorch bin, a safetensors file)",
    )
    args = parser.parse_args(argv)

    destination_dir = WEIGHTS_ROOT / args.name
    download_path = destination_dir / Path(args.url.split("?")[0]).name
    download(args.url, download_path, expected_sha256=args.sha256)
    if args.extract:
        extract(download_path, destination_dir / "model")
        download_path.unlink()
    print(f"OK  {args.name}  -> {destination_dir.resolve()}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
