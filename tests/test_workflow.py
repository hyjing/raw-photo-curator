import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from raw_photo_curator.workflow import (
    FileAction,
    apply_actions,
    export_selection,
    plan_file_actions,
    plan_xmp_actions,
    undo_actions,
    xmp_sidecar,
)


def test_json_csv_and_adobe_xmp_exports(tmp_path: Path):
    feedback = {"a.ARW": {"choice": "keep", "rating": 5, "tags": '["travel"]', "note": "best"}}
    json_path = export_selection(feedback, tmp_path / "selection.json", "json")
    csv_path = export_selection(feedback, tmp_path / "selection.csv", "csv")
    assert json.loads(json_path.read_text())["photos"][0]["choice"] == "keep"
    with csv_path.open() as source:
        assert next(csv.DictReader(source))["rating"] == "5"
    root = ET.fromstring(xmp_sidecar(Path("a.ARW"), "keep"))
    description = root.find(".//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description")
    assert description is not None
    assert description.attrib["{http://ns.adobe.com/xap/1.0/}Rating"] == "5"
    assert description.attrib["{http://ns.adobe.com/camera-raw-settings/1.0/}Pick"] == "1"


def test_file_plan_apply_and_undo_preserve_source(tmp_path: Path):
    source = tmp_path / "source.ARW"
    source.write_bytes(b"raw bytes")
    destination = tmp_path / "selected"
    actions = plan_file_actions([str(source)], destination, "copy")
    assert actions[0].status == "planned"
    audit_path = tmp_path / "audit.json"
    apply_actions(actions, audit_path)
    assert (destination / source.name).read_bytes() == b"raw bytes"
    assert source.read_bytes() == b"raw bytes"
    assert undo_actions(audit_path) == 1
    assert source.read_bytes() == b"raw bytes"


def test_xmp_action_is_audited_and_reversible(tmp_path: Path):
    sidecar = tmp_path / "photo.xmp"
    audit = tmp_path / "xmp-audit.json"
    apply_actions([FileAction(xmp_sidecar(Path("photo.ARW"), "reject"), str(sidecar), "xmp")], audit)
    assert sidecar.is_file()
    assert undo_actions(audit) == 1
    assert not sidecar.exists()


def test_xmp_plan_never_overwrites_existing_sidecar(tmp_path: Path):
    raw = tmp_path / "photo.ARW"
    raw.write_bytes(b"raw")
    raw.with_suffix(".xmp").write_text("existing")
    plan = plan_xmp_actions({str(raw): {"choice": "keep", "rating": 4}})
    assert plan[0].status == "conflict"
