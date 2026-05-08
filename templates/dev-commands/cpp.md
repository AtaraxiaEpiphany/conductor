## Development Commands (C++)

### Environment
```bash
export CMAKE_BUILD_DIR="build"
```
> Keep build artifacts isolated in the `build/` directory. Never compile in-source. Add `build/` to `.gitignore`.

### Setup
```bash
cmake -B build && cmake --build build
```

### Daily Development
```bash
cmake --build build          # compile
ctest --test-dir build       # run tests
```

### Before Committing
```bash
cmake --build build && ctest --test-dir build
```

### Coverage
```bash
cmake -B build -DCMAKE_BUILD_TYPE=Debug -DENABLE_COVERAGE=ON && cmake --build build && ctest --test-dir build
```
