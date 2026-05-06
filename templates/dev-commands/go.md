## Development Commands (Go)

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
