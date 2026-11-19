"""Filesystem locations shared by the local application."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = PROJECT_ROOT / "web"
FONT_ROOT = WEB_ROOT / "shared" / "fonts"
DATA_ROOT = Path(os.environ.get("NEXUS_DATA_DIR", PROJECT_ROOT / "data")).expanduser().resolve()
RESEARCH_ROOT = (
    Path(os.environ.get("NEXUS_RESEARCH_DIR", DATA_ROOT / "research")).expanduser().resolve()
)

OUTPUT_ROOT = (
    Path(os.environ.get("NEXUS_OUTPUT_DIR", Path.home() / "Documents" / "Nexus Research"))
    .expanduser()
    .resolve()
)
