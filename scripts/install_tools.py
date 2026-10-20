"""Install official Godot Linux binaries/templates and a pinned bundletool."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

BUNDLETOOL_SHA256 = "a099cfa1543f55593bc2ed16a70a7c67fe54b1747bb7301f37fdfd6d91028e29"


def download(url, path, digest, algorithm="sha512"):
    with urllib.request.urlopen(url, timeout=120) as response, path.open("wb") as target:
        shutil.copyfileobj(response, target)
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, algorithm).hexdigest()
    if actual != digest:
        raise ValueError(f"Checksum mismatch: {path.name}")


def main():
    version, destination = sys.argv[1:]
    if not re.fullmatch(r"4\.\d+\.\d+", version):
        raise ValueError("Use an exact stable Godot 4 version, such as 4.6.2.")
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    base = f"https://github.com/godotengine/godot-builds/releases/download/{version}-stable/"
    with urllib.request.urlopen(base + "SHA512-SUMS.txt", timeout=60) as response:
        checksums = dict((name.lstrip("*"), digest) for digest, name in
                         (line.split(maxsplit=1) for line in response.read().decode().splitlines() if line.strip()))
    editor_name = f"Godot_v{version}-stable_linux.x86_64.zip"
    templates_name = f"Godot_v{version}-stable_export_templates.tpz"
    for name in (editor_name, templates_name):
        download(base + name, root / name, checksums[name])
    with zipfile.ZipFile(root / editor_name) as archive:
        binary = root / "godot"
        binary.write_bytes(archive.read(editor_name.removesuffix(".zip")))
        binary.chmod(0o755)
    templates = root / "templates"
    templates.mkdir(exist_ok=True)
    with zipfile.ZipFile(root / templates_name) as archive:
        for name in ("android_source.zip", "android_debug.apk", "android_release.apk"):
            (templates / name).write_bytes(archive.read("templates/" + name))
    bundletool = root / "bundletool.jar"
    download("https://github.com/google/bundletool/releases/download/1.18.3/bundletool-all-1.18.3.jar",
             bundletool, BUNDLETOOL_SHA256, "sha256")
    subprocess.run(["sdkmanager", "platform-tools", "platforms;android-35", "platforms;android-36",
                    "build-tools;35.0.0", "build-tools;35.0.1", "ndk;28.1.13356709",
                    "cmake;3.10.2.4988404"], check=True)
    with open(os.environ["GITHUB_ENV"], "a") as stream:
        for key, value in {"GODOT_BIN": binary, "GODOT_TEMPLATE_DIR": templates,
                           "BUNDLETOOL_JAR": bundletool}.items():
            stream.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
