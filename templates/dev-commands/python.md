## Development Commands (Python)

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
