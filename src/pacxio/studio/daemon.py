"""Start a local Workbench independently of a short-lived POSIX agent worker.

This helper runs in a fresh interpreter, before starting any threads. It reports
the actual server PID, detaches, and executes the ordinary Workbench server.
Permissions, environment, loopback binding and explicit studio_stop stay intact.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("The detached launcher requires POSIX")
    with args.log.open("wb") as log, open(os.devnull, "rb") as null:
        pid = os.fork()
        if pid:
            print(pid, flush=True)
            return
        try:
            os.setsid()
            os.dup2(null.fileno(), 0)
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            os.execv(
                sys.executable,
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "pacxio.studio.server",
                    str(args.directory.resolve()),
                    "--port",
                    str(args.port),
                ],
            )
        except BaseException:
            import traceback

            traceback.print_exc()
            os._exit(1)


if __name__ == "__main__":
    main()
