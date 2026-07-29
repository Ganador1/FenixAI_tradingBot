#!/usr/bin/env python3
"""Execute a command with a validated dotenv environment, without shell evaluation."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.security.dotenv_security import secure_load_dotenv


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")

    original_path = os.environ.get("PATH", os.defpath)
    secure_load_dotenv(args.env, required=True)
    # Command lookup must not be redirected by a credential file.
    os.environ["PATH"] = original_path
    os.environ["FENIX_SECURE_DOTENV_LOADED"] = "1"
    os.environ["FENIX_SKIP_DOTENV"] = "1"
    executable = shutil.which(command[0], path=original_path)
    if executable is None or not Path(executable).is_file():
        parser.error(f"command is not an executable file: {command[0]}")
    resolved = str(Path(executable).resolve(strict=True))
    # This wrapper intentionally executes the caller-selected argv without a
    # shell after resolving it against the pre-dotenv PATH.
    os.execve(resolved, [resolved, *command[1:]], os.environ)  # nosemgrep
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
