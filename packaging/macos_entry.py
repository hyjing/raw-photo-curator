import subprocess
import sys
import webbrowser
from pathlib import Path
from threading import Timer

from raw_photo_curator.cli import analyze, main
from raw_photo_curator.server import serve

if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    else:
        selection = subprocess.run(
            [
                "osascript", "-e",
                'POSIX path of (choose folder with prompt "Choose a RAW photo folder")',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if selection.returncode == 0 and selection.stdout.strip():
            folder = Path(selection.stdout.strip()).resolve()
            output = Path.home() / "Library/Application Support/RAW Photo Curator/live"
            Timer(0.8, lambda: webbrowser.open("http://127.0.0.1:8765/")).start()
            serve([], output, 8765, folder, 5, analyze)
