## Development Commands (Dart)

### Environment
```bash
export PUB_CACHE="${HOME}/.pub-cache"
```
> Keep `.dart_tool/` and `build/` out of version control. Add both to `.gitignore`.

### Setup
```bash
dart pub get
# or: flutter pub get
```

### Daily Development
```bash
dart run              # run application
dart test             # run tests
dart analyze          # static analysis
dart format .         # format
```

### Before Committing
```bash
dart analyze && dart format --set-exit-if-changed . && dart test
```

### Coverage
```bash
dart test --coverage=coverage && dart pub run coverage:format_coverage
```
