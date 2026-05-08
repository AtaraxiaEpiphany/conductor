## Development Commands (Python)

### Environment
```bash
export PYTHONPYCACHEPREFIX="/tmp/.pycache_$(pwd)"
```
> Redirects all `__pycache__/` to `/tmp/` to keep the project tree clean during TDD cycles.

### Setup
```bash
pip install -e ".[dev]"
# or: uv sync / poetry install
```

### Daily Development
```bash
python -m pytest      # run tests
ruff check .          # lint
ruff format .         # format
```

### Before Committing
```bash
ruff check . && ruff format --check . && pytest --cov
```

### Coverage
```bash
pytest --cov --cov-report=html
```
