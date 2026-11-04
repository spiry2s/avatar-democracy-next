# Android app

This folder contains the native Android app for Avatar Democracy. It has **two modes**:

## Standalone mode (default — no PC needed)

The APK bundles the full web frontend plus a JavaScript port of the backend. Everything runs on the phone:

- Bills, your Avatar profile, the synthetic population, and the audit log are stored on the device (WebView localStorage).
- Claude is called directly from the phone over HTTPS using **your own Anthropic API key**.
- On first launch a settings panel opens automatically — paste your API key there (get one at [console.anthropic.com](https://console.anthropic.com)) and pick models. The gear button (bottom right) reopens it anytime.
- The key is stored only on the device and sent only to `api.anthropic.com`. No key is baked into the APK.

Settings include: main model (Opus 5.5 default), checker model for the capture-defense cross-check (Haiku 5.5 default), synthetic population size, demo mode (skips cooling-off timers), and a local-data wipe.

Standalone limitations (use a PC server for these): PDF upload and analyze-by-URL are not available — paste bill text instead.

## Remote mode (thin client for the Python backend)

Enter a server URL in the top bar and tap **Go** — the app becomes a WebView onto the FastAPI server, exactly like the desktop browser. Tap **Local** to return to standalone mode.

For an emulator: the server URL `http://10.0.2.2:8000` reaches the host PC.
For a physical phone: connect phone and PC to the same Wi-Fi, run `Start Avatar Democracy LAN.bat`, and enter the printed address (e.g. `http://192.168.1.50:8000`).

## How standalone mode works

- `app/src/main/assets/app/*.js` is a JS port of the Python backend (state machine, prompts, direct Anthropic API client).
- The repo's `frontend/` directory is bundled into APK assets **unmodified** at build time (see `sourceSets` in `app/build.gradle`) — it stays the single source of truth for the UI.
- `MainActivity` serves the assets from a private origin and injects the script tags into each page; a fetch shim reroutes the frontend's `/api/*` calls to the in-app backend.
- `android/dev_server.py` serves the identical bundle on the desktop (`py -3 android/dev_server.py` → http://127.0.0.1:8123) so the standalone app can be tested in a normal browser.

## Build an APK

From the `android/` folder (the Gradle wrapper is included; only a JDK 17+ is needed):

```powershell
$env:JAVA_HOME = "C:\dev\android-toolchain\jdk17"   # if JAVA_HOME isn't set globally
.\gradlew.bat assembleDebug
```

The debug APK lands in `android/app/build/outputs/apk/debug/`.

The Android SDK location is read from `android/local.properties` (`sdk.dir=...`); this machine's copy points at `C:\dev\android-toolchain\android-sdk`.

## Run with Android Studio

1. Install Android Studio.
2. Open the `android/` folder as a project.
3. Let Android Studio sync Gradle.
4. Run the `app` configuration on an emulator or phone.

## Notes

- Android backup is disabled (`allowBackup="false"`) so the API key stored on the device is never copied into device or cloud backups.
- Remote mode against a LAN server needs the access token the server prints: open `http://YOUR-PC-IP:8000/?token=...`.
- The JavaScript port in `assets/app/` mirrors the Python backend but has not yet received the Python side's structured-output and section-id fixes; treat the Python server as the reference implementation.
- Cleartext HTTP is enabled so remote mode can reach a LAN FastAPI server. Standalone mode itself only talks HTTPS to `api.anthropic.com`.
- For any public deployment of the backend, host it over HTTPS.
