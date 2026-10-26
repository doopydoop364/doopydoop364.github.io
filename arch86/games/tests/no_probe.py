"""Imported first by every test module: the suite must never run the real lspci/glxinfo/vulkaninfo (vulkaninfo segfaults when DISPLAY is
set but the X connection fails, and each crash is a systemd coredump). The environment variable is inherited by CLI subprocesses too."""

import os

os.environ["ARCH_GAMES_NO_PROBE"] = "1"
