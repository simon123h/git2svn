# Contributing to git2svn

Thank you for contributing to `git2svn`! This document outlines our development workflow, coding standards, and contribution guidelines.

---

## 1. Development Environment Setup

`git2svn` is built entirely on the Python 3 standard library with zero runtime dependencies. Only development tooling (Ruff) is required.

### 1.1 Requirements
* Python `>= 3.11`
* `git` CLI (standard installation)
* `svn` CLI (Apache Subversion command-line client)
* `ruff` for code formatting and linting

### 1.2 Installation for Development
```bash
# Clone the repository
git clone https://github.com/simon123h/git2svn.git
cd git2svn

# Optional: create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dev dependencies (ruff, mypy, coverage)
pip install -e ".[dev]"

# Enable git pre-commit hooks (checks formatting and linter before commit)
git config core.hooksPath .githooks
# or if using pre-commit framework:
# pip install pre-commit && pre-commit install
```

---

## 2. Coding Standards & Conventions

### 2.1 Commit Messages
All commit messages **must** strictly adhere to the [Conventional Commits](https://www.conventionalcommits.org/) specification:
```text
<type>(<optional scope>): <description>

[optional body]

[optional footer(s)]
```
* **Allowed Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`.
* **Example:** `feat(patch): replace patch with git apply and add EOL normalization`

### 2.2 Code Formatting, Linting & Type Checking
We use [Ruff](https://astral.sh/ruff) (target Python 3.11+, line length 120) and [MyPy](https://mypy-lang.org/):
```bash
# Check code for lint errors and auto-fix what's safe
ruff check --fix .

# Format code
ruff format .

# Check formatting without modifying
ruff format --check .

# Run static type checks
mypy git2svn
```
All code must pass `ruff check .`, `ruff format --check .`, and `mypy git2svn` with zero errors or warnings before committing.

### 2.3 Documentation-as-Code
* All architecture documentation resides in [`docs/arc42/README.md`](docs/arc42/README.md) following the [arc42 template](https://arc42.org/) and [`docs/arc42/adrs.md`](docs/arc42/adrs.md).
* All requirements documentation resides in [`docs/req42.md`](docs/req42.md) following the [req42 framework](https://req42.de/).
* CLI workflows and command guides reside in [`docs/user-guide/README.md`](docs/user-guide/README.md).
* Diagrams are embedded directly using **Mermaid.js** code blocks (`mermaid`).

---

## 3. Testing Guidelines

### 3.1 Running Tests
Run the test suite using Python's built-in `unittest` runner:
```bash
python3 -m unittest discover tests
```

### 3.2 Writing Tests
* Any new feature or bugfix must include corresponding automated tests in `tests/`.
* Mock external network or real SVN servers where appropriate, but use real temporary Git repositories and directory structures (`tempfile.TemporaryDirectory`) for unit and integration testing.
* When adding newline/patch logic, test both `\r\n` (CRLF) and `\n` (LF) scenarios to prevent regressions with SVN `E135000` mixed line endings.

---

## 4. Pull Request Process

1. Create a feature branch off `main` (e.g. `feat/my-new-option`).
2. Make your changes adhering to conventional commits.
3. Run `ruff check .`, `ruff format --check .`, `mypy git2svn`, and `python3 -m unittest discover tests`.
4. Ensure documentation in `docs/` and `README.md` is updated if CLI syntax or architecture changed.
5. Open a Pull Request with a clear description of the problem solved.
