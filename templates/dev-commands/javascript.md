## Development Commands (JavaScript)

### Environment
```bash
export NODE_OPTIONS="--max-old-space-size=4096"
```
> Keep test artifacts and coverage reports out of source: add `coverage/` and `.nyc_output/` to `.gitignore`.

### Setup
```bash
npm install
```

### Daily Development
```bash
npm run dev           # start dev server
npm test              # run tests
npm run lint          # lint code
```

### Before Committing
```bash
npm run check         # format + lint + test
```

### Coverage
```bash
npm test -- --coverage
```
