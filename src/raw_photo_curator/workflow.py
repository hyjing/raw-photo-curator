from __future__ import annotations

import csv
import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class FileAction:
    source: str
    destination: str
    method: str
    status: str = "planned"


def export_selection(feedback: dict[str, dict], destination: Path, format: str) -> Path:
    rows = [
        {
            "path": path,
            "choice": item.get("choice"),
            "rating": item.get("rating"),
            "tags": json.loads(item.get("tags", "[]")) if isinstance(item.get("tags"), str) else item.get("tags", []),
            "note": item.get("note", ""),
            "updated_at": item.get("updated_at", ""),
        }
        for path, item in sorted(feedback.items())
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    if format == "json":
        destination.write_text(json.dumps({"version": 1, "photos": rows}, ensure_ascii=False, indent=2))
    elif format == "csv":
        with destination.open("w", newline="", encoding="utf-8") as target:
            writer = csv.DictWriter(target, fieldnames=("path", "choice", "rating", "tags", "note", "updated_at"))
            writer.writeheader()
            for row in rows:
                writer.writerow({**row, "tags": json.dumps(row["tags"], ensure_ascii=False)})
    else:
        raise ValueError("format must be json or csv")
    return destination


def xmp_sidecar(path: Path, choice: str, rating: int | None = None, label: str | None = None) -> str:
    resolved_rating = rating if rating is not None else (5 if choice in {"keep", "edit"} else -1)
    resolved_label = label or ("Green" if choice == "keep" else "Red" if choice == "reject" else "Yellow")
    pick = 1 if choice in {"keep", "edit"} else -1
    return f'''<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"
   xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"
   xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   xmp:Rating="{resolved_rating}" xmp:Label="{resolved_label}"
   photoshop:Urgency="{0 if pick > 0 else 8}" crs:Pick="{pick}"/>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>'''


def plan_file_actions(paths: list[str], destination: Path, method: str) -> list[FileAction]:
    if method not in {"copy", "hardlink", "symlink"}:
        raise ValueError("unsupported method")
    return [
        FileAction(path, str(destination / Path(path).name), method,
                   "conflict" if (destination / Path(path).name).exists() else "planned")
        for path in paths
    ]


def plan_xmp_actions(feedback: dict[str, dict]) -> list[FileAction]:
    actions = []
    for path, item in sorted(feedback.items()):
        choice = item.get("choice")
        if choice not in {"keep", "edit", "reject"}:
            continue
        destination = Path(path).with_suffix(".xmp")
        actions.append(FileAction(
            xmp_sidecar(Path(path), choice, item.get("rating")), str(destination), "xmp",
            "conflict" if destination.exists() else "planned",
        ))
    return actions


def apply_actions(actions: list[FileAction], audit_path: Path) -> dict:
    completed = []
    for action in actions:
        source, destination = Path(action.source), Path(action.destination)
        if action.status == "conflict" or destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if action.method == "copy":
            shutil.copy2(source, destination)
        elif action.method == "hardlink":
            os.link(source, destination)
        elif action.method == "symlink":
            destination.symlink_to(source)
        elif action.method == "xmp":
            destination.write_text(action.source, encoding="utf-8")
        else:
            raise ValueError("unsupported action")
        completed.append({**asdict(action), "status": "created"})
    audit = {
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "actions": completed,
        "undone_at": None,
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    return audit


def undo_actions(audit_path: Path) -> int:
    audit = json.loads(audit_path.read_text())
    if audit.get("undone_at"):
        return 0
    removed = 0
    for action in reversed(audit["actions"]):
        destination = Path(action["destination"])
        if destination.is_file() or destination.is_symlink():
            destination.unlink()
            removed += 1
    audit["undone_at"] = datetime.now(UTC).isoformat()
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    return removed
