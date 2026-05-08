## Development Commands (Go)

### Environment
```bash
export GOCACHE="/tmp/.go-build-cache"
```
> Redirects Go build cache to `/tmp/`. The test binary cache (`go test -c`) is also stored here.

### Setup
```bash
go mod tidy
```

### Daily Development
```bash
go run .              # run application
go test ./...         # run tests
golangci-lint run     # lint
```

### Before Committing
```bash
go test ./... && golangci-lint run
```

### Coverage
```bash
go test -coverprofile=coverage.out ./... && go tool cover -html=coverage.out
```
