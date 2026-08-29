import argparse
import json
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from .catalog import Catalog, fingerprint
from .embedding import ColorGridEmbedding
from .evaluation import grouping_metrics
from .grouping import build_groups
from .image_io import RAW_EXTENSIONS, as_array, discover, load_preview
from .metadata import PhotoMetadata, extract_metadata, extract_raw_metrics, perceptual_hash
from .models import Result
from .objective import BuiltinObjectivePlugin
from .plugins import default_registry
from .report import write_report
from .scoring import explain, measure, scores
from .server import serve
from .workflow import (
    apply_actions,
    export_selection,
    plan_file_actions,
    plan_xmp_actions,
    undo_actions,
)


def analyze(
    folder: Path,
    output: Path,
    limit: int | None = None,
    offset: int = 0,
    progress: Callable[[int, int], None] | None = None,
    stats: Callable[[dict[str, int]], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> list[Result]:
    paths = discover(folder)
    if limit is not None:
        paths = paths[offset : offset + limit]
    elif offset:
        paths = paths[offset:]
    thumbs = output / "thumbnails"
    thumbs.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []
    run_stats = {"hits": 0, "misses": 0, "failed": 0, "deleted": 0, "cancelled": 0}
    if progress:
        progress(0, len(paths))
    with Catalog(output / "catalog.sqlite3") as catalog:
        embedder = ColorGridEmbedding()
        objective_plugin = BuiltinObjectivePlugin()
        configured = catalog.plugin_settings()
        enabled_ids = (
            {plugin_id for plugin_id, enabled in configured.items() if enabled}
            if configured
            else {"builtin.objective", "builtin.saliency", "builtin.timing-depth"}
        )
        plugin_registry = default_registry(enabled_ids)
        preview_plugins = [
            plugin
            for plugin in plugin_registry.enabled()
            if plugin.id != objective_plugin.id and hasattr(plugin, "analyze_image")
        ]
        run_stats["deleted"] = catalog.prune_missing()
        for number, path in enumerate(paths, start=1):
            if cancelled and cancelled():
                run_stats["cancelled"] = 1
                break
            try:
                image = None
                cached = catalog.cached(path, output)
                if cached:
                    results.append(cached)
                    run_stats["hits"] += 1
                else:
                    image = load_preview(path)
                    metrics = measure(as_array(image))
                    keep, edit = scores(metrics)
                    thumb_name = f"{fingerprint(path)[:20]}.jpg"
                    image.save(thumbs / thumb_name, "JPEG", quality=84, optimize=True)
                    result = Result(
                        path, keep, edit, metrics, explain(metrics), f"thumbnails/{thumb_name}"
                    )
                    metadata = extract_metadata(path)
                    catalog.store(
                        result,
                        metadata,
                        perceptual_hash(image),
                        embedder.embed(image),
                        f"{embedder.id}:{embedder.version}",
                        objective_plugin.criteria,
                        objective_plugin.analyze_result(result, metadata),
                    )
                    results.append(result)
                    run_stats["misses"] += 1
                for plugin in preview_plugins:
                    if not catalog.has_plugin_results(path, plugin):
                        image = image or load_preview(path)
                        catalog.store_plugin_results(
                            path, plugin, plugin.analyze_image(image)
                        )
            # A damaged/unsupported file should not abort a long folder scan.
            except Exception as exc:  # noqa: BLE001
                run_stats["failed"] += 1
                print(f"跳过 {path}: {exc}", file=sys.stderr)
            finally:
                if progress:
                    progress(number, len(paths))
        records = catalog.photo_records()
        metadata_by_path = {
            str(record["path"]): PhotoMetadata(**record["metadata"]) for record in records
        }
        for result in sorted(results, key=lambda item: item.keep_score, reverse=True)[:30]:
            if result.path.suffix.lower() not in RAW_EXTENSIONS:
                continue
            metadata = metadata_by_path[str(result.path)]
            if metadata.raw_highlight_headroom is None:
                try:
                    highlight, shadow = extract_raw_metrics(result.path)
                    metadata = replace(
                        metadata,
                        raw_highlight_headroom=highlight,
                        raw_shadow_recovery=shadow,
                    )
                    catalog.update_metadata_and_criteria(
                        result.path,
                        metadata,
                        objective_plugin.analyze_result(result, metadata),
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"RAW 线性指标不可用 {result.path}: {exc}", file=sys.stderr)
        catalog.replace_automatic_groups(build_groups(catalog.photo_records()))
    if stats:
        stats(run_stats)
    print(
        f"扫描完成：{len(results)} 张 · 缓存命中 {run_stats['hits']} · "
        f"新分析 {run_stats['misses']} · 失败 {run_stats['failed']}"
    )
    return sorted(results, key=lambda item: item.keep_score, reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="本地 RAW 照片选片与调色潜力分析")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("analyze", help="分析照片文件夹并生成静态报告")
    live = sub.add_parser("serve", help="启动本地交互式选片工作台")
    evaluation = sub.add_parser("evaluate-groups", help="使用本地人工标注评测分组")
    plan_files = sub.add_parser("plan-files", help="预览复制/硬链接/符号链接操作")
    apply_plan = sub.add_parser("apply-plan", help="执行预览过的文件计划并写审计日志")
    undo = sub.add_parser("undo", help="按审计日志撤销文件操作")
    export = sub.add_parser("export", help="导出 JSON/CSV 选片清单")
    plan_xmp = sub.add_parser("plan-xmp", help="预览非破坏性 XMP sidecar 写入")
    for item in (command, live):
        item.add_argument("folder", type=Path)
        item.add_argument("--output", type=Path, default=Path("reports/latest"))
        item.add_argument("--limit", type=int)
    live.add_argument("--port", type=int, default=8765)
    evaluation.add_argument("catalog", type=Path)
    evaluation.add_argument("labels", type=Path)
    plan_files.add_argument("manifest", type=Path, help="包含 photos/path 的选片 JSON")
    plan_files.add_argument("destination", type=Path)
    plan_files.add_argument("--method", choices=("copy", "hardlink", "symlink"), default="copy")
    plan_files.add_argument("--output", type=Path, default=Path("file-plan.json"))
    apply_plan.add_argument("plan", type=Path)
    apply_plan.add_argument("--audit", type=Path, default=Path("file-audit.json"))
    undo.add_argument("audit", type=Path)
    export.add_argument("database", type=Path)
    export.add_argument("destination", type=Path)
    export.add_argument("--format", choices=("json", "csv"), default="json")
    export.add_argument("--profile", default="travel")
    plan_xmp.add_argument("manifest", type=Path)
    plan_xmp.add_argument("--output", type=Path, default=Path("xmp-plan.json"))
    args = parser.parse_args()
    if args.command == "evaluate-groups":
        labels = json.loads(args.labels.read_text())
        with Catalog(args.catalog) as catalog:
            predicted = [
                [member["path"] for member in group["members"]]
                for group in catalog.groups()
            ]
        print(json.dumps(grouping_metrics(predicted, labels["groups"]), indent=2))
        return
    if args.command == "plan-files":
        manifest = json.loads(args.manifest.read_text())
        paths = [item["path"] for item in manifest["photos"] if item.get("choice") in {"keep", "edit"}]
        actions = plan_file_actions(paths, args.destination, args.method)
        args.output.write_text(json.dumps([action.__dict__ for action in actions], indent=2))
        print(f"计划：{args.output.resolve()} · {len(actions)} 项")
        return
    if args.command == "apply-plan":
        from .workflow import FileAction

        actions = [FileAction(**item) for item in json.loads(args.plan.read_text())]
        audit = apply_actions(actions, args.audit)
        print(f"已执行 {len(audit['actions'])} 项；审计：{args.audit.resolve()}")
        return
    if args.command == "undo":
        print(f"已撤销 {undo_actions(args.audit)} 项")
        return
    if args.command == "export":
        connection = sqlite3.connect(args.database)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT path, choice, rating, tags, note, updated_at FROM profile_feedback WHERE profile_id = ?",
            (args.profile,),
        ).fetchall()
        connection.close()
        export_selection({row["path"]: dict(row) for row in rows}, args.destination, args.format)
        print(f"已导出：{args.destination.resolve()} · {len(rows)} 条")
        return
    if args.command == "plan-xmp":
        manifest = json.loads(args.manifest.read_text())
        feedback = {item["path"]: item for item in manifest["photos"]}
        actions = plan_xmp_actions(feedback)
        args.output.write_text(json.dumps([action.__dict__ for action in actions], indent=2))
        print(f"XMP 计划：{args.output.resolve()} · {len(actions)} 项")
        return
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
