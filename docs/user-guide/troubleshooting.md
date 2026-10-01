# Troubleshooting & FAQ

Frequently asked questions and troubleshooting guides for `git2svn`.

---

### Q: Why does `replay` fail with "SVN workspace has uncommitted changes"?
`replay` requires a clean SVN workspace so every Git commit is ported as an isolated, atomic Subversion revision. Before running `replay`, review and commit or revert uncommitted changes in your SVN working copy:
```bash
svn status
svn revert -R .
```

---

### Q: Why does `replay` reject my branch with a "merge commit" error?
`replay` ports changes commit-by-commit to maintain a clean linear SVN history. If your Git branch has merge commits, rebase it first against your target branch:
```bash
git checkout feature/login
git rebase main
```
Alternatively, if you want to squash the entire branch into a single uncommitted SVN changeset without rebasing, use `stage`:
```bash
git2svn stage main..feature/login -s /path/to/svn
```

---

### Q: How do I handle patch rejections (`.rej` files)?
If `git apply` encounters conflicting context lines, it writes `.rej` files into the SVN working copy and pauses.
1. Open the `.rej` file to see the rejected hunk.
2. Manually make the necessary edits in the target file.
3. Delete the `.rej` (and any `.orig`) file.
4. Run `git2svn replay --continue`.

---

### Q: Subversion error on Windows: `svn: command not found`
If using TortoiseSVN, ensure the **"command line client tools"** feature was selected during installation. If not, re-run the TortoiseSVN installer, choose **Modify**, and enable the command-line tools component. `git2svn` will also automatically discover `svn.exe` in `C:\Program Files\TortoiseSVN\bin\svn.exe` or `C:\Program Files\SlikSvn\bin\svn.exe`.

---

### Q: Why doesn't `svn log` show newly replayed commits in my working copy?
Subversion uses mixed-revision working copies: `svn commit` updates the repository, but only touched files have their local revision numbers updated. The working copy root directory remains at its previous base revision until you update.

Run:
```bash
svn update
```
Or instruct `git2svn replay` to update automatically:
```bash
git2svn replay -u <range>
# Or configure globally:
git config git2svn.autoUpdate true
```

---

### Q: Why does `git2svn` fail with "Subversion working copy is locked" (`E155004`)?
If Subversion was interrupted mid-operation (e.g. an aborted command, an IDE indexing run, or a background mirror sync), Subversion places lock files in `.svn` to prevent database corruption.

To resolve:
1. Run:
   ```bash
   svn cleanup /path/to/svn
   ```
2. If background sync scripts or IDE indexing processes are actively touching the directory, wait for them to finish.
3. Re-run your `git2svn` command.

---

### Q: Why does `git2svn` fail with "Item is out of date" or collision (`E155015` / `E160024`)?
This happens when another developer or automated process committed new revisions to Subversion upstream while your local working copy was still at an older revision. Subversion rejects committing against stale base revisions.

To resolve:
1. Update your SVN working copy to bring it up to HEAD:
   ```bash
   svn update /path/to/svn
   ```
2. Check `svn status` to verify there are no tree or content conflicts.
3. If an in-progress replay was paused, continue it with:
   ```bash
   git2svn replay --continue
   ```

