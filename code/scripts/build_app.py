"""Build a self-contained, locally signed macOS app. Never package private data."""

import importlib.metadata
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / ".local" / "native-build"
APP = ROOT / "dist" / "Nexus Research.app"
LOGO = ROOT.parent / "logo.png"


def run(args):
    subprocess.run([str(x) for x in args], cwd=ROOT, check=True)


def build_icon(destination):
    """Frame the original mark on a rounded white tile at normal macOS icon scale."""
    iconset = BUILD / "Nexus.iconset"
    helper = BUILD / "BuildIcon"
    run(
        [
            "xcrun",
            "swiftc",
            "-O",
            "-module-cache-path",
            BUILD / "swift-cache",
            "-framework",
            "AppKit",
            ROOT / "desktop/BuildIcon.swift",
            "-o",
            helper,
        ]
    )
    run([helper, LOGO, iconset])
    run(["iconutil", "--convert", "icns", iconset, "--output", destination])


def main():
    if sys.platform != "darwin":
        raise SystemExit("The native build currently supports macOS only.")
    if not LOGO.is_file():
        raise SystemExit("The repository-root logo.png is required to build the Dock icon.")
    BUILD.mkdir(parents=True, exist_ok=True)
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--onedir",
            "--name",
            "NexusEngine",
            "--distpath",
            BUILD / "engine",
            "--workpath",
            BUILD / "work",
            "--specpath",
            BUILD,
            "--paths",
            ROOT,
            "--collect-all",
            "rdkit",
            "--collect-all",
            "numpy",
            "--add-data",
            f"{ROOT / 'web'}:web",
            ROOT / "scripts/desktop_engine.py",
        ]
    )
    if APP.exists():
        shutil.rmtree(APP)
    macos, resources = APP / "Contents/MacOS", APP / "Contents/Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    build_icon(resources / "Nexus.icns")
    shutil.copytree(BUILD / "engine/NexusEngine", resources / "Engine")
    notices = resources / "ThirdParty"
    notices.mkdir()
    shutil.copyfile(ROOT.parent / "LICENSE", resources / "LICENSE")
    for package in ("rdkit", "numpy", "pillow", "pyinstaller", "certifi"):
        distribution = importlib.metadata.distribution(package)
        for file in distribution.files or []:
            if (
                file.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))
                and ".." not in file.parts
            ):
                destination = notices / package / file.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(distribution.locate_file(file), destination)
    shutil.copyfile(ROOT / "web/shared/fonts/OFL.md", notices / "SourceCodePro-OFL.md")
    run(
        [
            "xcrun",
            "swiftc",
            "-O",
            "-target",
            "arm64-apple-macosx14.0",
            "-module-cache-path",
            BUILD / "swift-cache",
            "-framework",
            "AppKit",
            "-framework",
            "WebKit",
            ROOT / "desktop/NexusResearch.swift",
            "-o",
            macos / "NexusResearch",
        ]
    )
    info = {
        "CFBundleName": "Nexus Research",
        "CFBundleDisplayName": "Nexus Research",
        "CFBundleIdentifier": "org.nexusresearch.desktop",
        "CFBundleExecutable": "NexusResearch",
        "CFBundlePackageType": "APPL",
        "CFBundleIconFile": "Nexus.icns",
        "CFBundleShortVersionString": "0.4.0",
        "CFBundleVersion": "5",
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    }
    if qa := os.environ.get("NEXUS_BUILD_QA_DATA"):
        info["NexusQADataDirectory"] = str(Path(qa).resolve())
    (APP / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    # Ad-hoc signing makes this a local preview, not a notarized distribution release.
    run(["codesign", "--force", "--deep", "--sign", "-", APP])
    print(
        f"\nBuilt {APP}\nLocal preview only; Developer ID signing and notarization remain release work."
    )


if __name__ == "__main__":
    main()
