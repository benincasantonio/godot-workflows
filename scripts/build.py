"""Stage, sign, export and verify a Godot Android release using standard tools."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

from project import metadata, set_options, within


def require(condition, message):
    if not condition:
        raise ValueError(message)


def certificate_der(text):
    match = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", text, re.S)
    require(match is not None, "Missing signing certificate.")
    return ssl.PEM_cert_to_DER_cert(match.group())


def run(command, env, cwd=None):
    result = subprocess.run([str(x) for x in command], cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    output = result.stdout
    for name in ("GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD", "UPLOAD_KEYSTORE_BASE64"):
        secret = env.get(name)
        if secret:
            output = output.replace(secret, "[redacted]")
    # Never print complete tool environments or commands containing signing material.
    if result.returncode:
        raise RuntimeError(f"{Path(str(command[0])).name} failed:\n" + "\n".join(output.splitlines()[-40:]))
    return output


def hook(root, name, arguments, env):
    if name:
        path = within(root, name)
        require(path.is_file() and os.access(path, os.X_OK), "Hooks must be executable files in the game repository.")
        print(f"Running project hook: {name}", flush=True)
        output = run([path, *arguments], env, root)
        print(output)


def verify_manifest(xml, expected, version):
    ns = "{http://schemas.android.com/apk/res/android}"
    manifest = ET.fromstring(xml)
    require(manifest.get("package") == expected["package"], "Unexpected Android package.")
    require(manifest.get(ns + "versionCode") == str(expected["version_code"]), "Unexpected version code.")
    require(manifest.get(ns + "versionName") == expected["version"], "Unexpected version name.")
    app = manifest.find("application")
    require(app is not None and app.get(ns + "debuggable", "false") == "false", "Debuggable release.")
    sdk = manifest.find("uses-sdk")
    for key, xml_key in (("min_sdk", "minSdkVersion"), ("target_sdk", "targetSdkVersion")):
        if expected[key]:
            require(sdk is not None and sdk.get(ns + xml_key) == expected[key], f"Unexpected {key}.")
    values = {item.get(ns + "name"): item.get(ns + "value") for item in app.findall("meta-data")}
    for key in ("org.godotengine.editor.version", "org.godotengine.library.version"):
        require(values.get(key) == version + ".stable", "Godot editor/template version mismatch.")


def verify_elf(data):
    require(data[:6] == b"\x7fELF\x02\x01", "Expected little-endian ELF64.")
    offset = struct.unpack_from("<Q", data, 32)[0]
    size, count = struct.unpack_from("<HH", data, 54)
    loads = [struct.unpack_from("<IIQQQQQQ", data, offset + i * size) for i in range(count)]
    loads = [entry for entry in loads if entry[0] == 1]
    require(loads and all(entry[7] >= 16384 for entry in loads), "Native library lacks 16 KB ELF alignment.")


def verify(aab, expected, version, work, env, certificate):
    java = Path(env["JAVA_HOME"]) / "bin/java"
    tools = Path(env["ANDROID_HOME"]) / "build-tools/35.0.0"
    bundle = [java, "-jar", env["BUNDLETOOL_JAR"]]
    run(bundle + ["validate", f"--bundle={aab}"], env)
    require("jar verified." in run([java.with_name("jarsigner"), "-J-Duser.language=en", "-verify", aab], env),
            "AAB signature verification failed.")
    actual_certificate = run([java.with_name("keytool"), "-printcert", "-rfc", "-jarfile", aab], env)
    require(certificate_der(actual_certificate) == certificate, "AAB uses an unexpected signing key.")
    manifest = run(bundle + ["dump", "manifest", f"--bundle={aab}"], env)
    (work / "manifest.xml").write_text(manifest)
    verify_manifest(manifest, expected, version)
    bundle_config = json.loads(run(bundle + ["dump", "config", f"--bundle={aab}"], env))
    require(bundle_config["optimizations"]["uncompressNativeLibraries"]["alignment"] == "PAGE_ALIGNMENT_16K",
            "AAB does not request 16 KB alignment.")
    with zipfile.ZipFile(aab) as archive:
        require(archive.testzip() is None, "Corrupt AAB.")
        libs = [n for n in archive.namelist() if n.startswith("base/lib/") and n.endswith(".so")]
        require({n.split("/")[2] for n in libs} == set(expected["architectures"]), "Unexpected architectures.")
        for name in libs:
            if name.split("/")[2] in ("arm64-v8a", "x86_64"):
                verify_elf(archive.read(name))
        require(not any(n.endswith((".jks", ".keystore", "export_credentials.cfg")) or
                        Path(n).name.startswith("gha-creds-") for n in archive.namelist()),
                "Credentials included in the bundle.")
    password = work / ".password"
    try:
        descriptor = os.open(password, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(env["GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD"])
        apk_set = work / "universal.apks"
        run(bundle + ["build-apks", f"--bundle={aab}", f"--output={apk_set}", "--mode=universal", "--overwrite",
                      "--ks=" + env["GODOT_ANDROID_KEYSTORE_RELEASE_PATH"],
                      "--ks-key-alias=" + env["GODOT_ANDROID_KEYSTORE_RELEASE_USER"],
                      f"--ks-pass=file:{password}", f"--key-pass=file:{password}"], env)
    finally:
        password.unlink(missing_ok=True)
    apk = work / "release.apk"
    with zipfile.ZipFile(apk_set) as archive:
        require(archive.testzip() is None, "Corrupt APK set.")
        apk.write_bytes(archive.read("universal.apk"))
    with zipfile.ZipFile(apk) as archive:
        require(archive.testzip() is None, "Corrupt APK.")
        libs = [n for n in archive.namelist() if n.startswith("lib/") and n.endswith(".so")]
        require({n.split("/")[1] for n in libs} == set(expected["architectures"]), "Unexpected APK architectures.")
    badging = run([tools / "aapt", "dump", "badging", apk], env)
    for key, value in (("name", expected["package"]), ("versionCode", str(expected["version_code"])),
                       ("versionName", expected["version"])):
        require(f"{key}='{value}'" in badging.splitlines()[0], f"Unexpected APK {key}.")
    signature = run([tools / "apksigner", "verify", "--verbose", "--print-certs", apk], env)
    require("certificate SHA-256 digest: " + hashlib.sha256(certificate).hexdigest() in signature,
            "APK uses an unexpected signing key.")
    run([tools / "zipalign", "-c", "-P", "16", "4", apk], env)
    (work / "verification.json").write_text(json.dumps(expected | {
        "godot_version": version, "signer_sha256": hashlib.sha256(certificate).hexdigest(),
        "aab_validated": True, "apk_signature_verified": True, "alignment_16kb": True}, indent=2) + "\n")
    return apk


def stage(root, source, destination):
    require(not destination.resolve().is_relative_to(source.resolve()), "Staging must be outside the source project.")
    def ignored(directory, names):
        return [n for n in names if n in {".git", ".godot", ".qa", ".release-tools", ".github", "builds",
                                        "export_credentials.cfg", "play_games.cfg"} or
                n.endswith((".jks", ".keystore")) or n.startswith("gha-creds-")]
    shutil.copytree(source, destination, ignore=ignored)


def build(args):
    root = Path.cwd().resolve()
    source = within(root, args.project)
    expected = json.loads(Path(args.metadata).read_text())
    require(metadata(source, args.preset) == expected, "Source preset changed after preflight.")
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    key = work / "upload.jks"
    try:
        if env.get("UPLOAD_KEYSTORE_BASE64"):
            descriptor = os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(base64.b64decode(env.pop("UPLOAD_KEYSTORE_BASE64"), validate=True))
            env["GODOT_ANDROID_KEYSTORE_RELEASE_PATH"] = str(key)
        for name in ("GODOT_ANDROID_KEYSTORE_RELEASE_PATH", "GODOT_ANDROID_KEYSTORE_RELEASE_USER",
                     "GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD"):
            require(bool(env.get(name)), f"Missing signing configuration: {name}")
        java = Path(env["JAVA_HOME"]) / "bin/java"
        signing = run([java.with_name("keytool"), "-J-Duser.language=en", "-list", "-v",
                       "-keystore", env["GODOT_ANDROID_KEYSTORE_RELEASE_PATH"],
                       "-alias", env["GODOT_ANDROID_KEYSTORE_RELEASE_USER"],
                       "-storepass:env", "GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD"], env)
        require("PrivateKeyEntry" in signing and "CN=Android Debug" not in signing,
                "A non-debug private upload key is required.")
        certificate = certificate_der(run(
            [java.with_name("keytool"), "-exportcert", "-rfc", "-keystore", env["GODOT_ANDROID_KEYSTORE_RELEASE_PATH"],
             "-alias", env["GODOT_ANDROID_KEYSTORE_RELEASE_USER"], "-storepass:env",
             "GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD"], env))
        env["PLAY_UPLOAD_CERT_SHA256"] = hashlib.sha256(certificate).hexdigest()
        project = work / "project"
        stage(root, source, project)
        # The portable editor gives hooks and export the same isolated configuration.
        portable = work / "editor/godot"
        portable.parent.mkdir(parents=True)
        shutil.copy2(env["GODOT_BIN"], portable)
        portable.with_name("_sc_").touch()
        data = portable.parent / "editor_data"
        templates = data / "export_templates" / (args.godot_version + ".stable")
        templates.parent.mkdir(parents=True)
        templates.symlink_to(Path(env["GODOT_TEMPLATE_DIR"]).resolve(), target_is_directory=True)
        settings = '[gd_resource type="EditorSettings" format=3]\n\n[resource]\n'
        for key_name, value in (("java_sdk_path", env["JAVA_HOME"]), ("android_sdk_path", env["ANDROID_HOME"])):
            settings += f"export/android/{key_name}={json.dumps(value)}\n"
        settings += "export/android/shutdown_adb_on_exit=false\n"
        minor = ".".join(args.godot_version.split(".")[:2])
        (data / f"editor_settings-{minor}.tres").write_text(settings)
        if sys.platform == "darwin":
            run(["codesign", "--force", "--sign", "-", portable], env)
        env["GODOT_BIN"] = str(portable)
        require(run([portable, "--version"], env).startswith(args.godot_version + ".stable"),
                "Unexpected Godot version.")
        hook(root, args.prepare_script, [project], env)
        require(metadata(project, args.preset) == expected, "Preparation changed release identity or export metadata.")
        set_options(project, args.preset, {"gradle_build/use_gradle_build": "true",
                                         "gradle_build/export_format": "1", "package/signed": "true"})
        command = [portable, "--headless", "--path", project, "--log-file", work / "godot.log"]
        print("Importing staged project…", flush=True)
        run(command + ["--editor", "--import", "--quit"], env)
        aab = work / "release.aab"
        print("Exporting signed AAB…", flush=True)
        run(command + ["--install-android-build-template", "--export-release", args.preset, aab], env)
        print("Verifying AAB and generating signed APK…", flush=True)
        apk = verify(aab, expected, args.godot_version, work, env, certificate)
        hook(root, args.verify_script, [aab, apk, work], env)
        print(f"Verified {expected['package']} {expected['version']} ({expected['version_code']})")
    finally:
        key.unlink(missing_ok=True)
        # Gradle may persist signing values in the staged project.
        shutil.rmtree(work / "project", ignore_errors=True)


def artifact(work, artifact_format):
    require(artifact_format in ("AAB", "APK"), "Artifact format must be AAB or APK.")
    source = Path(work) / ("release." + artifact_format.lower())
    target = Path(work) / "artifact"
    target.mkdir()
    shutil.copy2(source, target / source.name)
    (target / (source.name + ".sha256")).write_text(
        hashlib.sha256(source.read_bytes()).hexdigest() + "  " + source.name + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=".")
    parser.add_argument("--preset", default="Android")
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--godot-version", default="4.6.2")
    parser.add_argument("--prepare-script", default="")
    parser.add_argument("--verify-script", default="")
    build(parser.parse_args())
