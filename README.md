# Godot Android workflows

Build signed Android releases from each game's Actions tab, optionally publishing to Google Play internal testing. Shared implementation for `benincasantonio` projects; no game code or production credentials live here.

## Add a game

1. Commit a named Android export preset with a concrete package ID, version name, positive version code, and explicit architectures. GDScript is supported; the validated baseline is Godot 4.6.2. Other exact Godot 4 versions are configurable but need their own compatibility verification. C# and custom engine templates are not supported in v1.
2. Copy [`examples/android.yml`](examples/android.yml) into your game's `.github/workflows/android.yml`. Set its project directory, preset, and engine version.
3. Add repository Actions secrets:

   | Secret | Value |
   |---|---|
   | `ANDROID_UPLOAD_KEYSTORE_BASE64` | Base64-encoded existing upload keystore |
   | `ANDROID_UPLOAD_KEY_ALIAS` | Keystore entry alias |
   | `ANDROID_UPLOAD_KEY_PASSWORD` | Matching store/key password |

   Keep a separate backup of the upload key. To upload it without displaying its contents:

   ```sh
   base64 < /path/to/upload.jks | gh secret set ANDROID_UPLOAD_KEYSTORE_BASE64 --repo OWNER/GAME
   gh secret set ANDROID_UPLOAD_KEY_ALIAS --repo OWNER/GAME
   gh secret set ANDROID_UPLOAD_KEY_PASSWORD --repo OWNER/GAME
   ```

4. For publishing, complete [keyless Google setup](docs/google-setup.md) and set the three repository variables listed there. Build-only runs do not need Google credentials.
5. Open **Actions → Android release → Run workflow**. Leave **Publish** unchecked to download the selected AAB/APK. AAB is the default. Check **Publish** to release an AAB to internal testing from `main`; the format selector is then ignored and no artifacts are uploaded.

Before the first automated Play upload, create the app in Play Console, configure Play App Signing and the upload certificate, complete required Console setup, and manually upload its first build. The build-only workflow can produce that first AAB.

## Release behavior

Publishing checks the committed preset's version code against the maximum code returned by Play's current bundles, APKs and tracks. A used/older code, missing configuration, or API failure fails the run before build tools or compilation. The Play API remains authoritative for historical code reuse and concurrent Console edits.

Both modes build the real release package using the caller's upload key. The pipeline exports an AAB, validates it, and generates a signed universal APK using bundletool. Download mode retains only the selected binary and its checksum for 14 days. Publishing refreshes authentication, repeats the version check, and uploads with Fastlane to `internal` / `completed`. It does not modify listing text, screenshots or changelogs, and does not fall back to a draft.

Publishing runs are serialized per caller repository without cancelling an active release. Use one repository as the release owner of each Play app; separate repositories publishing the same app are not covered by GitHub's repository-scoped concurrency.

No version increments or source commits are made. Increase `version/code` in your game's preset before the next Play release. The run summary records the game commit, tools revision, version and outcome.

## Inputs and hooks

| Input | Default |
|---|---|
| `publish` | `false` |
| `artifact-format` | `AAB`; accepts `AAB` or `APK` |
| `project-directory` | `.` relative to the caller repository |
| `export-preset` | `Android` |
| `godot-version` | `4.6.2` |
| `prepare-script` | Empty |
| `verify-script` | Empty |
| `gcp-project-id`, `workload-identity-provider`, `service-account` | Empty; required when publishing |

Secrets passed to `workflow_call` are `upload-keystore-base64`, `upload-key-alias`, and `upload-key-password`. Pass them explicitly as in the example.

Hooks must be executable files in the caller repository, with a shebang. They run from the caller repository root:

- Preparation: `hook STAGED_PROJECT_DIRECTORY`, before import and export.
- Verification: `hook AAB_PATH APK_PATH VERIFICATION_DIRECTORY`, after generic verification.

Both receive `GODOT_BIN`, `GODOT_TEMPLATE_DIR`, `JAVA_HOME`, `ANDROID_HOME`, `BUNDLETOOL_JAR`, `GRADLE_USER_HOME`, and Godot's release-keystore environment variables. Hooks may install plugins or run game tests; nonzero exit stops the release. Do not print credentials. Preparation must retain the package, version and architecture/SDK metadata read during preflight. Public game-specific IDs belong in the game's own configuration.

The toolchain uses Ubuntu 24.04, Temurin 21, SDK platforms 35/36, Build-Tools 35.0.0/35.0.1, NDK 28.1.13356709, CMake 3.10.2.4988404 and bundletool 1.18.3. Godot binaries/templates are checked against official SHA-512 sums; bundletool and workflow actions are pinned.

## Maintaining v1

Callers use `@v1` to receive compatible fixes automatically. Breaking input or behavior changes require a new major release.

For each release, update the immutable tools tag embedded in `android.yml` (for example, `v1.0.1`), run the checks, then create that immutable tag. Validate the fixture and a game build-only integration against the candidate before advancing `v1`. Never move a full version tag. The workflow checks out helper scripts from its embedded full version so a moving `v1` cannot mix workflow and helper versions.

Dispatch **Promote shared release** with that immutable tag after the game check. It reruns validation and the fixture, confirms the embedded tools tag, and automatically advances `v1` on success. A failed check leaves `v1` unchanged.

Checks:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
bundle install
bundle exec ruby tests/test_play.rb
actionlint -shellcheck= .github/workflows/*.yml examples/android.yml
```

The validation workflow also builds a plain Godot fixture using a disposable non-debug key, checks both output formats, and never contacts Google Play.
