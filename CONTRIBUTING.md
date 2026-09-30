# Contributing to git2svn

Thank you for contributing to `git2svn`! This document outlines our development workflow, coding standards, and contribution guidelines.

---

## 1. Development Environment Setup

`git2svn` is built entirely on the Python 3 standard library with zero runtime dependencies. Only development tooling (Ruff) is required.

### 1.1 Requirements
* Python `>= 3.8`
* `git` CLI (standard installation)
* `svn` CLI (Apache Subversion command-line client)
* `ruff` for code formatting and linting

### 1.2 Installation for Development
```bash
# Clone the repository
git clone https://github.com/your-org/git2svn.git
cd git2svn

# Optional: create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dev dependencies (ruff)
pip install -e ".[dev]"
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

### 2.2 Code Formatting & Linting
We use [Ruff](https://astral.sh/ruff) configured in `pyproject.toml` (target Python 3.8+, line length 120):
```bash
# Check code for lint errors and auto-fix what's safe
ruff check --fix .

# Format code
ruff format .

# Check formatting without modifying
ruff format --check .
```
All code must pass `ruff check .` and `ruff format --check .` with zero errors or warnings before committing.

### 2.3 Documentation-as-Code
* All architecture documentation resides in [`docs/arc42.md`](file:///home/simon/Code/git2svn/docs/arc42.md) following the [arc42 template](https://arc42.org/).
* All requirements documentation resides in [`docs/req42.md`](file:///home/simon/Code/git2svn/docs/req42.md) following the [req42 framework](https://req42.de/).
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
3. Run `ruff check .`, `ruff format --check .`, and `python3 -m unittest discover tests`.
4. Ensure documentation in `docs/` and `README.md` is updated if CLI syntax or architecture changed.
5. Open a Pull Request with a clear description of the problem solved.
