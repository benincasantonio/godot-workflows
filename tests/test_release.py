import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from project import metadata, set_options, validate_request, within
from build import artifact, hook, stage, verify_elf, verify_manifest

FIXTURE = Path(__file__).parent / "fixture"


class ReleaseTests(unittest.TestCase):
    def test_preset_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            shutil.copytree(FIXTURE, project)
            original = metadata(project, "Android")
            set_options(project, "Android", {"version/code": "12", "version/name": '"2.0.0"'})
            changed = metadata(project, "Android")
            self.assertEqual(changed["version_code"], 12)
            self.assertEqual(changed["version"], "2.0.0")
            self.assertEqual(original["package"], changed["package"])
            for key, value in (("version/code", "0"), ("version/code", "2100000001"),
                               ("package/unique_name", '"org.godot.$genname"')):
                shutil.copy2(FIXTURE / "export_presets.cfg", project / "export_presets.cfg")
                set_options(project, "Android", {key: value})
                with self.assertRaises(ValueError):
                    metadata(project, "Android")

    def test_publish_gate(self):
        validate_request(True, "refs/heads/main", "workflow_dispatch", "APK")
        validate_request(False, "refs/heads/feature", "workflow_dispatch", "AAB")
        for ref, event in (("refs/heads/feature", "workflow_dispatch"), ("refs/tags/main", "workflow_dispatch"),
                           ("refs/heads/main", "pull_request"), ("refs/heads/main", "push")):
            with self.assertRaises(ValueError):
                validate_request(True, ref, event, "AAB")
        with self.assertRaises(ValueError):
            validate_request(False, "", "", "ZIP")

    def test_paths_and_hook_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "hook.sh"
            script.write_text("#!/bin/sh\nexit 7\n")
            script.chmod(0o755)
            with self.assertRaises(RuntimeError):
                hook(root, "hook.sh", [], os.environ.copy())
            with self.assertRaises(ValueError):
                within(root, "../escape")

    def test_stage_excludes_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            shutil.copytree(FIXTURE, source)
            for name in ("private.jks", "export_credentials.cfg", "gha-creds-test.json"):
                (source / name).write_text("not for export")
            stage(root, source, root / "staged")
            self.assertEqual(set(p.name for p in (root / "staged").iterdir()),
                             {"project.godot", "export_presets.cfg", "main.tscn"})

    def test_only_selected_binary_is_archived(self):
        for selected in ("AAB", "APK"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for extension in ("apk", "aab"):
                    (root / ("release." + extension)).write_bytes(extension.encode())
                artifact(root, selected)
                name = "release." + selected.lower()
                self.assertEqual({p.name for p in (root / "artifact").iterdir()}, {name, name + ".sha256"})

    def test_manifest_guard(self):
        expected = metadata(FIXTURE, "Android")
        xml = f'''<manifest xmlns:android="http://schemas.android.com/apk/res/android"
          package="{expected['package']}" android:versionCode="1" android:versionName="1.0.0">
          <uses-sdk android:minSdkVersion="24" android:targetSdkVersion="36"/>
          <application>
            <meta-data android:name="org.godotengine.editor.version" android:value="4.6.2.stable"/>
            <meta-data android:name="org.godotengine.library.version" android:value="4.6.2.stable"/>
          </application></manifest>'''
        verify_manifest(xml, expected, "4.6.2")
        for old, new in (('versionCode="1"', 'versionCode="2"'), ('versionName="1.0.0"', 'versionName="2.0.0"'),
                         ('<application>', '<application android:debuggable="true">'),
                         ("4.6.2.stable", "4.6.3.stable")):
            with self.assertRaises(ValueError):
                verify_manifest(xml.replace(old, new), expected, "4.6.2")

    def test_elf_alignment(self):
        import struct
        elf = bytearray(120)
        elf[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<Q", elf, 32, 64)
        struct.pack_into("<HH", elf, 54, 56, 1)
        struct.pack_into("<IIQQQQQQ", elf, 64, 1, 0, 0, 0, 0, 0, 0, 16384)
        verify_elf(elf)
        struct.pack_into("<Q", elf, 64 + 48, 4096)
        with self.assertRaises(ValueError):
            verify_elf(elf)


if __name__ == "__main__":
    unittest.main()
