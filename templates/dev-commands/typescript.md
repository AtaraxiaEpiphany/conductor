## Development Commands (TypeScript)

### Environment
```bash
export NODE_OPTIONS="--max-old-space-size=4096"
```
> Keep build/test artifacts out of source: add `coverage/`, `.nyc_output/`, and `*.tsbuildinfo` to `.gitignore`.

### Setup
```bash
npm install
```

### Daily Development
```bash
npm run dev           # start dev server
npm test              # run tests
npm run lint          # lint code
npx tsc --noEmit      # type check
```

### Before Committing
```bash
npm run check         # format + lint + type check + test
```

### Coverage
```bash
npm test -- --coverage
```
