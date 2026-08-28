import argparse
import hashlib
import sys
from collections.abc import Callable
from pathlib import Path

from .image_io import as_array, discover, load_preview
from .models import Result
from .report import write_report
from .scoring import explain, measure, scores
from .server import serve


def analyze(
    folder: Path,
    output: Path,
    limit: int | None = None,
    offset: int = 0,
    progress: Callable[[int, int], None] | None = None,
) -> list[Result]:
    paths = discover(folder)
    if limit is not None:
        paths = paths[offset : offset + limit]
    elif offset:
        paths = paths[offset:]
    thumbs = output / "thumbnails"
    thumbs.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []
    if progress:
        progress(0, len(paths))
    for number, path in enumerate(paths, start=1):
        try:
            image = load_preview(path)
            metrics = measure(as_array(image))
            keep, edit = scores(metrics)
            digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
            thumb_name = f"{digest}.jpg"
            image.save(thumbs / thumb_name, "JPEG", quality=84, optimize=True)
            results.append(Result(path, keep, edit, metrics, explain(metrics), f"thumbnails/{thumb_name}"))
            print(f"[{number}/{len(paths)}] {path.name}: 保留 {keep:.0f} / 调色 {edit:.0f}")
        # A damaged/unsupported file should not abort a long folder scan.
        except Exception as exc:  # noqa: BLE001
            print(f"跳过 {path}: {exc}", file=sys.stderr)
        finally:
            if progress:
                progress(number, len(paths))
    return sorted(results, key=lambda item: item.keep_score, reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="本地 RAW 照片选片与调色潜力分析")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("analyze", help="分析照片文件夹并生成静态报告")
    live = sub.add_parser("serve", help="启动本地交互式选片工作台")
    for item in (command, live):
        item.add_argument("folder", type=Path)
        item.add_argument("--output", type=Path, default=Path("reports/latest"))
        item.add_argument("--limit", type=int)
    live.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not args.folder.is_dir():
        parser.error(f"照片文件夹不存在：{args.folder}")
    if args.command == "serve":
        serve([], args.output, args.port, args.folder, 5, analyze)
    else:
        results = analyze(args.folder, args.output, args.limit)
        report = write_report(results, args.output)
        print(f"\n完成：{report.resolve()}")


if __name__ == "__main__":
    main()
