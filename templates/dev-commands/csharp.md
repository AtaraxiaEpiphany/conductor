## Development Commands (C#)

### Setup
```bash
dotnet restore
```

### Daily Development
```bash
dotnet run            # run application
dotnet test           # run tests
```

### Before Committing
```bash
dotnet build --no-restore && dotnet test --no-build
```

### Coverage
```bash
dotnet test --collect:"XPlat Code Coverage"
```
