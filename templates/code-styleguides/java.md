# Google Java Style Guide Summary

This document summarizes key rules and best practices from the Google Java Style Guide.

## 1. Source File Basics
- **File Name:** The file name is the name of the sole top-level class it contains, plus the `.java` extension.
- **Encoding:** UTF-8.
- **Special Characters:** Avoid non-ASCII; escape when unavoidable (use `μ`, not the raw `μ`).
- **One Top-Level Class:** Exactly one top-level class or interface per file.

## 2. Formatting
- **Braces:** K&R style — opening brace on the same line (`if () {`), closing on its own. Braces are **mandatory** even for empty/optional bodies.
- **Indentation:** 2 spaces. Never tabs.
- **Line Length:** 100 characters maximum.
- **Wrapping:** Break long lines at a higher syntactic level; indent the continuation +4 spaces. Operators stay at the end of the broken line.
- **Whitespace:** One space after keywords (`if (x)`), separating reserved words from `(`, and around all binary operators. No space before `(` in a method call.
- **Vertical Whitespace:** One blank line between methods, and sparingly elsewhere to group logic. No blank line at the start/end of a block.
- **Imports:** No wildcards — one import per line. No specific ordering mandated, but group consistently and never leave unused imports.
- **Annotations:** One per line, immediately above the declaration they apply to.
- **Modifiers:** Standard order: `public protected private abstract default static final transient volatile synchronized native strictfp`.
- **Numeric Literals:** Use uppercase for hex/octal (`0x1F`, `0177`). Use `L` (not `l`) for long suffixes (`3000000000L`).

## 3. Naming
- **Packages:** All lowercase, dotted reverse-DNS (`com.example.app`).
- **Classes & Interfaces:** PascalCase (`StringBuilder`, `Iterable`).
- **Methods:** camelCase (`sendMessage`, `isEmpty`). Not verbs-as-nouns; prefer `computeTotal()` over `total()`.
- **Constants:** `CONSTANT_CASE` (`MAX_VALUE`). Constants are `static final` with deeply immutable content; an array reference is **not** a constant.
- **Non-Constant Fields:** camelCase (`lineItems`).
- **Local Variables & Params:** camelCase (`inputValue`).
- **Type Variables:** Single capital, optionally with a number (`T`, `E`, `K`, `V`, `T2`).

## 4. Programming Practices
- **`@Override`:** Always present when overriding a superclass method or implementing an interface method.
- **Caught Exceptions:** Do not ignore. At minimum, log it; empty `catch` blocks need a comment explaining why suppression is safe.
- **Static Members:** Access static members via the class, not an instance (`Foo.bar()`, not `foo.bar()`).
- **Finalizers:** **Forbidden** — do not override `Object.finalize()`.
- **Visibility:** Minimize accessibility. Top-level classes are package-private unless they are the public API; fields are private by default.
- **Varargs:** Prefer collections (`List<T>`) over varargs for APIs, especially when callers pass arrays. Use `@SafeVarargs` only where unavoidable.

## 5. Literals & Defaults
- Use the most specific literal (`0L` not `0` for long). Prefer the boxed constants (`Boolean.TRUE`) only when avoiding autoboxing ambiguity.

## 6. Javadoc
- **Required** for all public classes and members.
- Present for non-obvious protected/package-private members.
- Format: a one-line summary (terminated by `.`), blank line, then details. Use block tags (`@param`, `@return`, `@throws`) consistently and in order.
- At minimum, every public class needs a class-level Javadoc explaining its purpose.

## 7. Testing

Follow the project's test file placement and naming conventions defined in `conductor/workflow/testing/strategy.md`.

**BE CONSISTENT.** When editing code, match the existing style.

*Source: [Google Java Style Guide](https://google.github.io/styleguide/javaguide.html)*
