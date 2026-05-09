# Contributing to Andyria

Thank you for your interest in contributing to the Andyria Foundation.

## Quick Start

1. **Fork** the repo and clone your fork
2. Create a branch: `git checkout -b feat/your-feature`
3. Make your changes, add tests
4. Run the test suite: `cd python && pytest -q`
5. Open a Pull Request against `main`

## Code Style

- **Python**: Ruff formatter/lint (`ruff format`, `ruff check`) and mypy type checking
- **Rust**: `cargo fmt` and `cargo clippy`
- **JavaScript**: 2-space indent, semicolons optional

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add new reasoning capability
fix: correct entropy beacon hash calculation
docs: update architecture diagram
refactor: simplify model router fallback chain
test: add coverage for AutoLearner.record()
```

## Areas to Contribute

| Area | Good For |
|---|---|
| `python/andyria/` | Core runtime, agents, memory, API |
| `rust/crates/ledger/` | Cryptographic DAG, signing, entropy |
| `tt-live-agent/` | TikTok Live integration, JETS token |
| `docs/` | GitHub Pages site, architecture docs |
| `deploy/` | Docker, Raspberry Pi, Termux configs |
| Tests | Any `tests/` directory |

## Reporting Issues

Use [GitHub Issues](https://github.com/andyriax/andyria/issues).  
For security vulnerabilities, please email the maintainer directly — do not open a public issue.

## License

By contributing, you agree your contributions are licensed under Apache 2.0.
