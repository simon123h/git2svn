# Architecture Decisions (ADR Summary)

This document contains the Architecture Decision Records (ADRs) for `git2svn`.

---

### ADR-1: 2-Verb CLI Model (`stage` and `replay`)
* **Context:** Early designs featured overlapping commands like `patch`, `copy`, `cherry-pick`, and flags for partial commits.
* **Decision:** Consolidate around two core verbs with strict commit boundaries:
  - `stage`: **Never commits.** Prepares changes in SVN workspace (`svn add`, `svn rm`), leaving files dirty for review or squash commits.
  - `replay`: **Always commits.** Sequentially ports individual Git commits with original author messages.
* **Consequences:** Eliminates accidental commits, provides intuitive user semantics, and aligns with developers' mental models.

---

### ADR-2: `git apply` over GNU `patch`
* **Context:** The original prototype used GNU `patch`. On Windows, GNU `patch` is not natively installed and often mishandles Git diff headers (renames, binary diffs, mode changes).
* **Decision:** Rely on `git apply --ignore-whitespace --unsafe-paths --reject`.
* **Consequences:** Eliminates external Windows dependencies (since `git` is already guaranteed to exist) and reliably processes all Git diff metadata.

---

### ADR-3: Post-Patch EOL Normalization
* **Context:** Git unified diffs use `LF` newlines. Applying an `LF` patch to a `CRLF` file in Subversion causes mixed line endings in the same file. Subversion commits fail with fatal error `svn: E135000: Inconsistent line ending style`.
* **Decision:** Pre-inspect original file line endings before applying the patch. After patching, normalize touched text files to match the detected style (`\r\n` or `\n`), skipping binaries and symlinks.
* **Consequences:** Transparent commit success on Windows and cross-platform repositories without requiring repository-wide newline churn.

---

### ADR-4: `--snapshot` Full Tree Alignment
* **Context:** When a feature branch heavily diverges from SVN, or when cherry-picks occurred in SVN out-of-order, computing a patch against a common Git ancestor fails with massive conflicts.
* **Decision:** Provide `stage --snapshot <ref>`, which compares the tree of `<ref>` directly with the SVN filesystem, calculating additions, deletions, and modifications without needing a common base commit.
* **Consequences:** Serves as a foolproof escape hatch to realign divergent trees in a single reviewable staging step.

---

### ADR-5: Zero External Runtime Dependencies
* **Context:** Python packaging often introduces complex virtual environment and dependency management hurdles.
* **Decision:** Use only the Python 3 standard library (`argparse`, `pathlib`, `subprocess`, `json`, `shutil`, `tempfile`).
* **Consequences:** Single-file script or portable wheel runnable immediately on any developer workstation or CI environment without `pip install` prerequisites.

---

### ADR-6: Commit Message Delivery via Temporary File (`-F`)
* **Context:** Passing multi-line or long commit messages via `svn commit -m "..."` hits Windows `cmd.exe` command-line length limits (8,191 characters) and suffers from shell escaping and quotation breakage.
* **Decision:** Write commit messages to a temporary UTF-8 file and invoke `svn commit -F <tempfile>`.
* **Consequences:** Supports arbitrary message lengths, multi-paragraph markdown bodies, and special characters cleanly.

---

### ADR-7: Explicit Subprocess UTF-8 Encoding
* **Context:** On Windows, Python subprocesses default to the active OEM code page (e.g. `cp1252` or `cp850`), crashing when processing Git logs or diffs containing UTF-8 characters (umlauts, emojis, accented names).
* **Decision:** Explicitly specify `encoding="utf-8", errors="replace"` on all subprocess executions.
* **Consequences:** Robust internationalization and character preservation across all operating systems.

---

### ADR-8: Windows Subversion Executable Auto-Discovery
* **Context:** TortoiseSVN on Windows installs `svn.exe` into `C:\Program Files\TortoiseSVN\bin\svn.exe`, but doesn't always add it to user `PATH`.
* **Decision:** Implement automatic search in standard TortoiseSVN and SlikSvn directories if `svn` is not found on `PATH`.
* **Consequences:** Seamless out-of-the-box operation for TortoiseSVN users on Windows.

---

### ADR-9: Configuration Persistence via `.git/config`
* **Context:** Passing `--svn-dir` on every invocation is tedious and error-prone. Introducing a `.git2svnrc` or JSON file litters the workspace and risks accidental commits into Git.
* **Decision:** Read settings directly from the local Git repository's `.git/config` via `git config git2svn.*` (`svnDir`, `dryRun`, `copy`, `defaultRange`, `autoUpdate`).
* **Consequences:** Zero workspace clutter, repository-scoped persistence, and clean precedence (CLI > Env > Git Config > Defaults).

---

### ADR-10: Optional Post-Replay Working Copy Update (`--update` / `git2svn.autoUpdate`)
* **Context:** Subversion's mixed-revision working copy design leaves the local root revision behind `HEAD` after commits, hiding new revisions from `svn log` until `svn update` is run.
* **Decision:** Provide an opt-in `-u` / `--update` flag and `git2svn.autoUpdate` setting to trigger `svn update` upon successful replay completion.
* **Consequences:** Keeps local working copy base at `HEAD` while allowing users on massive remote SVN repositories to avoid extra network latency.

---

### ADR-11: No Author/Metadata Preservation or Date Spoofing
* **Context:** In Git, commits carry distinct author and committer names, emails, and historical timestamps. Subversion tracks revision properties (`svn:author`, `svn:date`) which are either immutable by default or require custom server-side hook scripts (`pre-revprop-change`) that are typically locked down in enterprise SVN deployments. Modifying revision properties or spoofing commit timestamps/authors can create security, audit, and traceability concerns, in addition to introducing server-dependent replay failures.
* **Decision:** Explicitly reject author spoofing, date manipulation, or artificial metadata rewriting in `git2svn`. All replayed commits are committed using the active SVN working copy user credentials and the current server timestamp. Original Git commit messages are preserved verbatim, but author identity and commit time in SVN remain authoritative to the person running the tool at the time of execution.
* **Consequences:**
  - Guarantees compatibility with all SVN servers out of the box without requiring `pre-revprop-change` server hook privileges.
  - Maintains strict organizational accountability: the committer who pushes the changes to SVN is always the verified SVN user.
  - Avoids temporal inconsistencies in SVN repositories where revisions must strictly increase in time.
  - Keeps the codebase lightweight and free of brittle revision-property manipulation logic.

---

### ADR-12: Scope Boundary: Adapter Bridge vs. Autonomous Master-Slave Exporter
* **Context:** In scenarios where Git is the sole upstream source of truth without an automated reverse `svn2git` mirror creating tracking branches (e.g. `origin/svn-mirror/trunk`), `git2svn` has no external reference point to determine which Git commit corresponds to the current state of the SVN working copy. To operate autonomously without an explicit Git range or external mirror, `git2svn` would need to record and track synchronization state across boundaries (e.g. via SVN commit trailers, custom root revision properties, or local/remote sync tags), implement out-of-band conflict reconciliation logic, and handle multi-branch topology mapping.
* **Decision:** Explicitly maintain `git2svn`'s scope as a lightweight, stateless **adapter bridge** rather than an autonomous master-slave repository replication system:
  1. `git2svn` will not take on the responsibility of an internal state machine or persistent cross-VCS metadata synchronization engine.
  2. In the standard workflow, state and collision detection remain externalized in Git (via mirror tracking branches) and SVN working copy revisions.
  3. When operating without an SVN-to-Git mirror, `git2svn` continues to function reliably as an explicit delivery tool via user-directed arguments:
     - `git2svn replay <base-commit>..<target-commit>` (explicitly providing the base commit where SVN last left off).
     - `git2svn stage --snapshot <target-ref>` (statelessly projecting the desired Git tree onto the SVN workspace without requiring historical ancestry).
* **Consequences:**
  - Preserves Unix philosophy: `git2svn` focuses solely on reliably applying Git diffs and tree states to an SVN working copy.
  - Keeps the codebase lean, robust, and free of fragile metadata synchronization or repository reconciliation logic.
  - Avoids architectural divergence and false promises regarding bidirectional synchronization or unmanaged out-of-band SVN mutations.

---

### ADR-13: Managed Working Copy in `.git/git2svn/svn_wc/`
* **Context:** Requiring users to manually create and pass an SVN working copy directory (`--svn-dir`) adds friction compared to tools like `git-svn`. However, directly committing to remote SVN servers via `svnmucc` or SWIG Python bindings would add immense complexity (directory lifecycle management, binary chunking, loss of local patch conflict inspection, or non-portable C-extension dependencies).
* **Decision:** Implement a **Managed Working Copy** architecture.
  1. `git2svn setup <url-or-path>` dynamically detects whether the argument is a local working copy or an SVN repository URL (`https://`, `svn://`, `file://`, etc.).
  2. When an SVN URL is provided, `git2svn` automatically checks out the working copy to `.git/git2svn/svn_wc/` and persists `git2svn.svnUrl` and `git2svn.svnDir` in `.git/config`.
  3. All operational commands (`stage`, `replay`, `diff`, `status`) seamlessly resolve this managed path without requiring `--svn-dir`.
  4. If `.git/git2svn/svn_wc/` is deleted (or on a fresh clone), `git2svn` automatically and transparently self-heals by re-checking out the repository using the configured `git2svn.svnUrl`.
* **Consequences:**
  - Delivers the checkout-free convenience of `git-svn` without introducing C-extension dependencies or fragile direct-to-server transaction engines.
  - Automatically bound to the repository lifecycle: deleting the Git repository completely cleans up the working copy with zero orphan cache accumulation.
  - Invisible to Git status (located within `.git/`).
  - Preserves 100% of local patching, line ending normalization, and interactive conflict recovery mechanisms.


