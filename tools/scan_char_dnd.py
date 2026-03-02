"""Dry-run scanner for character_dnd entries.
Searches Data/Bot/*/character_dnd.json and reports any hp_info/ability_info fields
that are not JSON strings representing objects or are primitive strings/ints instead of
expected serialized JsonObject strings.

Outputs a human-readable report to stdout and saves JSON report to tools/scan_report.json
"""
import json
import os
from pathlib import Path

ROOT = Path(r"d:\touniang\DicePPBOT\DicePP")
DATA_BOT_GLOB = ROOT / "src" / "plugins" / "DicePP" / "Data" / "Bot"
REPORT_PATH = ROOT / "tools" / "scan_report.json"

keys_of_interest = {"hp_info", "ability_info"}

report = {
    "scanned_files": [],
    "issues": []
}


def short_repr(v):
    try:
        s = json.dumps(v, ensure_ascii=False)
    except Exception:
        s = repr(v)
    if len(s) > 200:
        return s[:200] + "..."
    return s


def search_obj(obj, path_stack, file_path):
    # obj can be dict, list, primitive
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_stack = path_stack + [str(k)]
            if k in keys_of_interest:
                # record type info
                t = type(v).__name__
                if isinstance(v, dict):
                    # could be already deserialized dict
                    report["issues"].append({
                        "file": str(file_path),
                        "path": ".".join(new_stack),
                        "type": "dict",
                        "sample": short_repr(v)
                    })
                elif isinstance(v, str):
                    # string — could be serialized JsonObject or plain
                    # try to json.loads
                    try:
                        parsed = json.loads(v)
                        parsed_type = type(parsed).__name__
                        if isinstance(parsed, dict):
                            # good: serialized object string
                            pass
                        else:
                            report["issues"].append({
                                "file": str(file_path),
                                "path": ".".join(new_stack),
                                "type": f"str->{parsed_type}",
                                "sample": short_repr(v)
                            })
                    except Exception:
                        # not json
                        report["issues"].append({
                            "file": str(file_path),
                            "path": ".".join(new_stack),
                            "type": "str->primitive",
                            "sample": short_repr(v)
                        })
                else:
                    report["issues"].append({
                        "file": str(file_path),
                        "path": ".".join(new_stack),
                        "type": t,
                        "sample": short_repr(v)
                    })
            # recurse
            search_obj(v, new_stack, file_path)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            search_obj(item, path_stack + [f"[{i}]"], file_path)


# walk Bot folders
if DATA_BOT_GLOB.exists():
    for bot_dir in DATA_BOT_GLOB.iterdir():
        if not bot_dir.is_dir():
            continue
        # look for character_dnd.json
        file_path = bot_dir / "character_dnd.json"
        if file_path.exists():
            report["scanned_files"].append(str(file_path))
            try:
                doc = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception as e:
                report["issues"].append({"file": str(file_path), "path": "<file>", "type": "invalid_json", "sample": str(e)})
                continue
            # find root
            root = doc.get("root", {})
            search_obj(root, [], file_path)

# save report
REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("Dry-run scan completed.")
print(f"Scanned files: {len(report['scanned_files'])}")
print(f"Issues found: {len(report['issues'])}")
print(f"Report saved to: {REPORT_PATH}")

if report['issues']:
    print('\nSample issues:')
    for it in report['issues'][:20]:
        print(f"- {it['file']} {it['path']} type={it['type']} sample={it['sample']}")
else:
    print('No issues found.')
