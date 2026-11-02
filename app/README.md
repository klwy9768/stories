# Stories — Flutter client

Android client for the Stories backend. See the [root README](../README.md) for architecture,
features, and deployment.

## Build

```bash
flutter pub get

# 1) server address: lib/api.dart, lib/log.dart, lib/notif.dart
# 2) access token: lib/secrets.dart
# 3) signing: cp android/key.properties.example android/key.properties

flutter build apk --release
```

Output: `build/app/outputs/flutter-apk/app-release.apk`

## Demo mode

Build with sample data and no backend connection:

```bash
flutter build apk --release \
  --dart-define=DEMO=true \
  --dart-define=API_BASE=http://127.0.0.1:9
```
