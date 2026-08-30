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
        output = Path.home() / "Library/Application Support/RAW Photo Curator/live"
        Timer(0.8, lambda: webbrowser.open("http://127.0.0.1:8765/")).start()
        serve([], output, 8765, None, 5, analyze)
