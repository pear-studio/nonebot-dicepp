"""Extended dry-run scanner for Data/Bot JSON files.
Searches all JSON files under Data/Bot and reports any occurrences of keys of interest
(hp_info, ability_info, etc.) where the stored value is not a serialized JSON-object string.

Saves report to tools/scan_all_report.json
"""
import json
from pathlib import Path

ROOT = Path(r"d:\touniang\DicePPBOT\DicePP")
DATA_ROOT = ROOT / "src" / "plugins" / "DicePP" / "Data" / "Bot"
REPORT_PATH = ROOT / "tools" / "scan_all_report.json"

KEYS = {"hp_info", "ability_info", "hp", "hp_info_str"}

report = {
    "scanned_files": [],
    "occurrences": [],
}


def short(v):
    try:
        s = json.dumps(v, ensure_ascii=False)
    except Exception:
        s = repr(v)
    if len(s) > 300:
        return s[:300] + "..."
    return s


def inspect_value(val):
    """Return (status, note)
    status: OK (serialized dict string) | DICT | STR_JSON_PRIMITIVE | STR_NONJSON | OTHER
    """
    if isinstance(val, dict):
        return "DICT", "bare_dict"
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                return "OK", "serialized_json_object"
            else:
                return "STR_JSON_PRIMITIVE", type(parsed).__name__
        except Exception:
            # not json
            # try int
            try:
                int(val)
                return "STR_NONJSON", "int_like"
            except Exception:
                return "STR_NONJSON", "nonjson"
    return "OTHER", type(val).__name__


def search_obj(obj, path, file_path):
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_path = path + [str(k)]
            if k in KEYS:
                status, note = inspect_value(v)
                if status != "OK":
                    report["occurrences"].append({
                        "file": str(file_path),
                        "path": ".".join(new_path),
                        "key": k,
                        "status": status,
                        "note": note,
                        "sample": short(v)
                    })
            # recurse
            search_obj(v, new_path, file_path)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            search_obj(item, path + [f"[{i}]"], file_path)


if DATA_ROOT.exists():
    for bot_dir in DATA_ROOT.iterdir():
        if not bot_dir.is_dir():
            continue
        for json_file in bot_dir.rglob('*.json'):
            try:
                txt = json_file.read_text(encoding='utf-8')
                doc = json.loads(txt)
            except Exception as e:
                report["occurrences"].append({"file": str(json_file), "path": "<file>", "key": "<file>", "status": "INVALID_JSON", "note": str(e)})
                continue
            report["scanned_files"].append(str(json_file))
            root = doc.get('root', doc)
            search_obj(root, [], json_file)

REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('Scan completed')
print(f"Files scanned: {len(report['scanned_files'])}")
print(f"Occurrences (non-OK): {len(report['occurrences'])}")
if report['occurrences']:
    print('Sample:')
    for o in report['occurrences'][:20]:
        print(f"- {o['file']} {o['path']} key={o['key']} status={o['status']} note={o['note']} sample={o['sample']}")
print(f"Report saved to: {REPORT_PATH}")
