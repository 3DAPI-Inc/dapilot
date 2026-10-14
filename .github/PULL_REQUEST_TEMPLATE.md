**What this changes**

**Why**

**Checks**
- [ ] `claude plugin validate plugins/dapilot` passes
- [ ] the plugin's version moved (`scripts/dev/release.sh`) if anything under `plugins/dapilot` changed
- [ ] no credential, token file, store or run in the diff
- [ ] `docs/changelog.md` has a dated entry when the behaviour changed
