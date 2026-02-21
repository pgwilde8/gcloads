# Commit Message Convention

Use short, imperative commits with a clear type prefix.

## Format
`<type>: <summary>`

## Types
- `feat`: new functionality
- `fix`: bug fix
- `refactor`: code change without behavior change
- `docs`: documentation only
- `chore`: tooling, config, or maintenance
- `test`: add/update tests
- `perf`: performance improvement

## Rules
- Keep subject line <= 72 chars
- Use present/imperative tense (`add`, `fix`, `update`)
- One logical change per commit
- If schema changes, include migration in same commit
- Never include secrets in commit messages

## Examples
- `feat: add packet snapshot logging on secure load`
- `fix: parse negotiation id from subject fallback token`
- `docs: add simulator fallback runbook`
- `chore: add .env.example and tighten .gitignore`

## Optional body template
```text
Why:
- reason 1
- reason 2

What:
- change 1
- change 2

Risk:
- low/medium/high + rollback note
```
