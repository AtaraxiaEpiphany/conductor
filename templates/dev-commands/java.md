## Development Commands (Java)

### Environment
```bash
# Maven and Gradle write artifacts next to the source tree — keep them out of git.
# Add to .gitignore: target/  build/  .gradle/  out/
```
> `target/` (Maven) and `build/` (Gradle) hold compiled classes and reports. Never commit them. Local caches live in `~/.m2/repository` and `~/.gradle/` (shared across projects, already outside the tree).

### Setup
```bash
# Maven
mvn -q -DskipTests package
# Gradle
./gradlew build -x test
```

### Daily Development
```bash
# Maven
mvn -q compile          # compile
mvn -q test             # run tests
mvn -q checkstyle:check # lint (or spotless:check if configured)

# Gradle
./gradlew compileJava   # compile
./gradlew test          # run tests
./gradlew check         # lint + tests
```

### Before Committing
```bash
# Maven (runs tests + configured checks/spotbugs)
mvn -q verify
# Gradle
./gradlew build
```

### Coverage
```bash
# Maven (requires the jacoco-maven-plugin)
mvn -q test jacoco:report
# Gradle (requires the jacoco plugin)
./gradlew test jacocoTestReport
```
> Reports land in `target/site/jacoco/` (Maven) or `build/reports/jacoco/test/html/` (Gradle).
