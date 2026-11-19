"""Package the self-contained app as the repository's drag-to-Applications installer."""

import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "dist/Nexus Research.app"
INSTALLER = ROOT.parent / "Install Nexus.dmg"


def main():
    if sys.platform != "darwin" or not APP.exists():
        raise SystemExit("Build the macOS app with make app first.")
    info = plistlib.loads((APP / "Contents/Info.plist").read_bytes())
    if info.get("CFBundleIdentifier") != "org.nexusresearch.desktop":
        raise SystemExit("The app has the wrong bundle identity.")
    if "NexusQADataDirectory" in info or any(APP.rglob("*.sqlite")):
        raise SystemExit("Do not package a QA-configured app or private database.")
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(APP)], check=True)
    local = ROOT / ".local"
    local.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="installer-", dir=local) as directory:
        stage = Path(directory) / "Nexus"
        stage.mkdir()
        shutil.copytree(APP, stage / APP.name, symlinks=True)
        (stage / "Applications").symlink_to("/Applications")
        image = Path(directory) / INSTALLER.name
        subprocess.run(
            [
                "hdiutil",
                "create",
                "-volname",
                "Nexus Research",
                "-srcfolder",
                str(stage),
                "-fs",
                "HFS+",
                "-format",
                "UDZO",
                "-imagekey",
                "zlib-level=9",
                "-o",
                str(image),
            ],
            check=True,
        )
        subprocess.run(["hdiutil", "verify", str(image)], check=True)
        if image.stat().st_size >= 100 * 1024 * 1024:
            raise SystemExit(
                "Installer exceeds GitHub's 100 MiB file limit; the previous installer was kept."
            )
        image.replace(INSTALLER)
    print(f"Built {INSTALLER} ({INSTALLER.stat().st_size / 1024**2:.1f} MiB).")


if __name__ == "__main__":
    main()
