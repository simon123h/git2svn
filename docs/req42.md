# req42 Requirements Specification: `git2svn`

This document defines the requirements for the `git2svn` CLI synchronization utility according to the [req42](https://req42.de/) framework.

---

## 1. Stakeholders, Vision & Goals

### 1.1 Problem Statement & Background
In legacy software development environments, enterprise Subversion (SVN) monorepos often contain millions of revisions and hundreds of gigabytes of history. In this setting:
* Native `git-svn` suffers from crippling clone times, memory leaks, and frequent fetch crashes.
* Developers want to use modern Git tooling locally (fast branch switching, rebasing, bisecting, feature branches) while their organization still mandates Subversion as the authoritative central repository.
* Fast mirrors maintained by tools such as KDE's `all-fast-export/svn2git` exist for one-way export, but pushing feature work back to Subversion remains fraught with manual friction, line-ending mismatches, and merge conflicts.

### 1.2 Stakeholders
| Stakeholder | Role | Key Interest |
| :--- | :--- | :--- |
| **Developer** | Primary User | Work exclusively in local Git, then cleanly bridge branches/commits to a local SVN checkout. |
| **SVN Reviewer / Lead** | Secondary User | Inspect changes in TortoiseSVN or CLI `svn diff` with atomic, reviewable commits before upstream push. |
| **Monorepo Admin** | Governance | Ensure Subversion integrity (`svn:eol-style` consistency, no broken histories, valid commit messages). |

### 1.3 Vision & Strategic Goals
* **SG-1 (Zero Overhead):** Provide a zero-dependency Python 3 utility requiring no external packages or compilation.
* **SG-2 (Simplicity & Predictability):** Consolidate all operations into two primary verbs: `stage` (never commits) and `replay` (always commits).
* **SG-3 (Format Resilience):** Transparently bridge Git's default `LF` format with legacy SVN repositories containing mixed `CRLF`/`LF` files, eliminating Subversion `E135000` rejections.
* **SG-4 (Stateful Pause/Resume):** Support robust conflict handling during multi-commit replays, avoiding partial commits or corrupt workspaces.

---

## 2. Functional Requirements (FR)

### FR-1: Flexible Reference Parsing
* **FR-1.1:** The CLI MUST accept a single commit hash (e.g. `abc1234` or full SHA).
* **FR-1.2:** The CLI MUST accept two-dot range notation (e.g. `main..feature` or `a1b2..c3d4`).
* **FR-1.3:** The CLI MUST accept two separate positional arguments (e.g. `main feature`).
* **FR-1.4:** The CLI MUST interpret single commit references as single-commit actions, and range expressions as multi-commit or squashed actions.

### FR-2: Staging Without Committing (`stage`)
* **FR-2.1 (Non-Committing):** `stage` MUST apply changes to the SVN workspace without ever executing `svn commit`.
* **FR-2.2 (Single Commit):** `stage <commit>` MUST port only that single commit's delta onto the SVN workspace.
* **FR-2.3 (Squash Range):** `stage <ref1> <ref2>` MUST port the squashed delta between `ref1` and `ref2` onto the SVN workspace as a single uncommitted changeset.
* **FR-2.4 (Structural Operations):**
  * Added files (`A`) MUST trigger `svn add <filepath> --parents`.
  * Deleted files (`D`) MUST trigger `svn rm <filepath>`.
  * Renamed files (`R`) MUST trigger `svn rm <old>` followed by `svn add <new> --parents`.
  * Copied files (`C`) MUST trigger `svn add <new> --parents`.
* **FR-2.5 (Direct Object DB Extraction `--copy`):** When `--copy` is passed, `stage` MUST bypass diff patching and directly extract binary snapshots from Git's object database (`git show <ref>:<path>`), properly handling file permissions and symlinks.
* **FR-2.6 (Full Tree Alignment `--snapshot`):** When `--snapshot` is passed, `stage` MUST align the SVN workspace to match the target Git commit/branch tree without needing to know where it branched off from in Git history:
  * Detect and delete files present in SVN but missing in Git (`svn rm`).
  * Detect and add files present in Git but missing in SVN (`svn add`).
  * Overwrite modified files to match Git content, preserving target line-ending conventions.
* **FR-2.7 (Staged Diff Preview `diff` & `--diff`):**
  * `git2svn diff` MUST display an `svn diff` of uncommitted changes in the SVN workspace.
  * `git2svn diff --stat` MUST display a compact diffstat summary of changed files.
  * `git2svn stage --diff` (or `-p`) MUST immediately print the staged diff preview upon stage completion.

### FR-3: Stateful History Replay (`replay`)
* **FR-3.1 (Always Commits & Metrics):** `replay` MUST port commits sequentially, executing an atomic `svn commit` for each commit using the original Git commit message and author body, reporting elapsed duration per commit and across the overall queue.
* **FR-3.2 (Single Commit Cherry-pick):** `replay <commit>` MUST replay only that single commit and commit it to SVN.
* **FR-3.3 (Linear History Enforcement):** `replay <ref1> <ref2>` MUST reject ranges containing merge commits and prompt the user to rebase to a linear history.
* **FR-3.4 (Pre-flight Clean Check):** `replay` MUST verify the SVN workspace has no uncommitted modifications before beginning.
* **FR-3.5 (Conflict Pause):** If a patch fails during replay:
  * Replay execution MUST pause immediately.
  * Replay state MUST be saved in `<svn_dir>/.svn/git2svn-replay.json`.
  * Reject artifacts (`.rej`, `.orig`) and conflicting filenames MUST be reported to stderr.
  * Clear instructions for manual resolution MUST be displayed.
* **FR-3.6 (Conflict Continuation `--continue`):**
  * `replay --continue` MUST verify that all `.rej` and `.orig` conflict artifacts have been deleted.
  * It MUST commit any staged resolutions using the interrupted commit's original message.
  * It MUST continue replaying the remaining queue of commits.
* **FR-3.7 (Conflict Abort `--abort`):**
  * `replay --abort` MUST revert uncommitted changes (`svn revert -R .`), remove conflict artifacts, clear state, and restore the SVN workspace to the last cleanly committed state.
* **FR-3.8 (Conflict Skip `--skip`):**
  * `replay --skip` MUST revert the uncommitted changes for the failing commit, clear its artifacts, and resume replaying subsequent commits in the queue.

### FR-4: Line Ending Normalization (CRLF / LF)
* **FR-4.1 (Native Diff Application):** Diffs MUST be applied using `git apply --ignore-whitespace --unsafe-paths --reject`.
* **FR-4.2 (Target Style Preservation):** For each file modified or added:
  * If the target file already exists in the SVN workspace, its dominant newline style (`\r\n` vs `\n`) MUST be detected before modification.
  * After patching, all line endings in that file MUST be normalized back to the detected target style.
* **FR-4.3 (Subversion Compliance):** The normalization MUST guarantee that no file contains mixed line endings, preventing Subversion `svn: E135000: Inconsistent line ending style` errors during `svn commit`.
* **FR-4.4 (Binary Immunity):** Binary files and symlinks MUST be detected and excluded from line-ending conversion.

### FR-5: User Feedback & Diagnostics
* **FR-5.1 (Identity Banner):** Before executing any command, the CLI MUST print a pre-flight identity target banner detailing:
  * Active Git repo name, current checked-out branch, HEAD commit hash, and target ref spec.
  * Active SVN workspace name, SVN URL/Relative URL, and current SVN revision (`rXXXX`).
* **FR-5.2 (Dry Run):** Passing `-n` / `--dry-run` MUST output all Git commands, diff previews, file mutations, and SVN actions without modifying disk.
* **FR-5.3 (Verbose Logging):** Passing `-v` / `--verbose` MUST display debug-level diagnostics and stack traces on unexpected errors.

---

## 3. Non-Functional Requirements (NFR)

| ID | Category | Requirement | Verification |
| :--- | :--- | :--- | :--- |
| **NFR-1** | **Portability** | Must run on standard Python 3.8+ on Linux, macOS, and Windows. | Tested on Linux, macOS & Windows matrix |
| **NFR-2** | **Zero Dependencies** | Runtime execution MUST NOT require `pip install` packages; standard library only. | Inspected `pyproject.toml` |
| **NFR-3** | **No GNU patch Dependency** | Must eliminate requirements for GNU `patch` / `patch.exe` on Windows by relying on standard `git apply`. | Automated tests |
| **NFR-4** | **Safety & Atomicity** | No SVN operation may leave untracked state or corrupt `.svn` metadata directories. | State tests |
| **NFR-5** | **Scalability** | Standard diff/patch staging must execute in under 3 seconds for commits modifying up to 5,000 lines. | Benchmark / Profiling |
| **NFR-6** | **Encoding Robustness** | Must handle UTF-8 characters (accents, umlauts) in commit logs and paths across Windows without `cp1252` encoding crashes. | Unit & E2E tests |
| **NFR-7** | **Message Length Immunity** | `svn commit` MUST use temporary message files (`-F`) to avoid OS shell argument length limits (e.g. 8191 chars on `cmd.exe`). | Windows execution & E2E tests |

---

## 4. Architecture & Interface Constraints

* **CON-1:** The utility must be executable both as a standalone script (`./git2svn.py`) and as a modular package (`python3 -m git2svn` or installed console script).
* **CON-2:** Replay state files MUST be stored inside `<svn_dir>/.svn/` to avoid polluting the working directory or appearing as unversioned files in SVN status.
* **CON-3:** CLI options must be flexible in placement, supporting flags before or after subcommands.
* **CON-4:** On Windows systems where `svn` is not in `PATH`, the utility MUST attempt to locate `svn.exe` in common default installation directories (TortoiseSVN, SlikSVN).
