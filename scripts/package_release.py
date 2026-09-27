"""Package the built Chrome/Brave extension for the website download button."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "v3" / "dist"
TARGET = ROOT / "release" / "pelican-v3.zip"
REQUIRED = {"manifest.json", "background.js", "content.js", "sidepanel.html", "dashboard.html", "onboarding.html", "pelican-config.js"}


def main() -> None:
    missing = sorted(name for name in REQUIRED if not (SOURCE / name).is_file())
    if missing:
        raise SystemExit("Build the extension first: missing " + ", ".join(missing))
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(TARGET, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted(SOURCE.rglob("*")):
            if source.is_file() and source.suffix != ".map":
                archive.write(source, source.relative_to(SOURCE).as_posix())
    digest = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    print(f"{TARGET} ({TARGET.stat().st_size} bytes, sha256 {digest})")


if __name__ == "__main__":
    main()
