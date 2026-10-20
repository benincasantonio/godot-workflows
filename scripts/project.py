"""Read the committed Android preset without starting Godot."""
import argparse
import configparser
import json
import os
from pathlib import Path
import re


def config(path):
    result = configparser.ConfigParser(interpolation=None, strict=True)
    result.read_string(Path(path).read_text())
    return result


def quoted(value):
    return json.loads(value)


def within(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Paths must stay inside the calling repository.")
    return path


def metadata(project, preset_name):
    project = Path(project)
    settings = (project / "project.godot").read_text()
    if re.search(r'^config/features=.*"C#"', settings, re.M):
        raise ValueError("This workflow supports GDScript projects, not C#.")
    presets = config(project / "export_presets.cfg")
    matches = [s for s in presets.sections() if re.fullmatch(r"preset\.\d+", s)
               and quoted(presets[s]["name"]) == preset_name]
    if len(matches) != 1:
        raise ValueError("Select exactly one existing Android export preset.")
    section = matches[0]
    if quoted(presets[section]["platform"]) != "Android":
        raise ValueError("The selected preset must export Android.")
    options = presets[section + ".options"]
    package = quoted(options["package/unique_name"])
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", package):
        raise ValueError("Set a concrete Android package ID in the export preset.")
    version = quoted(options["version/name"])
    code = int(options["version/code"])
    if not version.strip() or any(c in version for c in "\r\n") or not 1 <= code <= 2100000000:
        raise ValueError("Set a nonempty version name and a valid positive Android version code.")
    architectures = [a for a in ("arm64-v8a", "armeabi-v7a", "x86", "x86_64")
                     if options.get("architectures/" + a, "false") == "true"]
    if not architectures:
        raise ValueError("Explicitly enable Android architectures in the preset.")
    return {"package": package, "version": version, "version_code": code,
            "architectures": architectures, "section": section, "preset": preset_name,
            "min_sdk": quoted(options.get("gradle_build/min_sdk", '""')),
            "target_sdk": quoted(options.get("gradle_build/target_sdk", '""'))}


def set_options(project, preset_name, values):
    path = Path(project) / "export_presets.cfg"
    section = metadata(project, preset_name)["section"] + ".options"
    # Preserve Godot's file verbatim outside this preset's options.
    lines = path.read_text().splitlines()
    start = lines.index("[" + section + "]") + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("[")), len(lines))
    body = lines[start:end]
    for key, value in values.items():
        replacement = key + "=" + value
        index = next((i for i, line in enumerate(body) if line.split("=", 1)[0].strip() == key), None)
        if index is None:
            body.append(replacement)
        else:
            body[index] = replacement
    path.write_text("\n".join(lines[:start] + body + lines[end:]) + "\n")


def validate_request(publish, ref, event, artifact_format):
    if artifact_format not in ("AAB", "APK"):
        raise ValueError("Artifact format must be AAB or APK.")
    if publish and (ref != "refs/heads/main" or event != "workflow_dispatch"):
        raise ValueError("Publishing requires a manual workflow dispatch from main.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--preset", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    validate_request(os.environ.get("PUBLISH") == "true", os.environ.get("GITHUB_REF"),
                     os.environ.get("GITHUB_EVENT_NAME"), os.environ.get("ARTIFACT_FORMAT", "AAB"))
    data = metadata(within(Path.cwd(), args.project), args.preset)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Android package: {data['package']}; version {data['version']} ({data['version_code']})")


if __name__ == "__main__":
    main()
