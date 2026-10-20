# Publishing a RailWatch release

Use semantic versions. The first pilot release is **0.1.1**, tagged **v0.1.1** and marked as a GitHub prerelease. Update `project.version` in `pyproject.toml` before tagging. For alpha/beta/RC tags use `v0.2.0-rc.1` and the corresponding Python version `0.2.0rc1`; the first pilot's ordinary version number is marked prerelease through GitHub metadata.

1. Run Ruff, the full Python suite and `npm run test:browser`; inspect desktop/mobile screenshots. Check `bash -n install.sh scripts/install.sh`.
2. Commit and push, and wait for Client checks on that exact commit to pass.
3. Tag the tested commit, for example `git tag -a v0.1.1 -m 'RailWatch 0.1.1 prerelease'`, then `git push origin v0.1.1`.
4. Build a source bundle from the tag, with exactly this name and directory layout:

   ```sh
   mkdir -p dist
   git archive --format=tar.gz --prefix=railwatch-0.1.1/ v0.1.1 > dist/railwatch-0.1.1.tar.gz
   sha256sum dist/railwatch-0.1.1.tar.gz > dist/SHA256SUMS
   ```

5. Create a **draft prerelease**, attach the source bundle and SHA256SUMS, and write notes describing changes, known limitations and the installation command. The updater ignores drafts and releases lacking a verified install bundle.
6. Check the attached bundle's GitHub-generated `digest` against the local SHA-256, then publish the prerelease. The bundle must remain under 20 MiB compressed / 50 MiB expanded. Never replace an asset or move a published tag; publish a new version instead.
7. Verify the public Releases API contains the published tag, prerelease flag and matching asset digest. Check a fresh install and an upgrade on a spare Pi before recommending new OS/dependency changes broadly.

The installer and dashboard select the highest semantic version among the most recent 100 published, installable releases, including prereleases during this pilot. They never select arbitrary branches, unverified archives or draft releases. GitHub's generated asset digests are documented in [the Releases API](https://docs.github.com/en/rest/releases/releases).

The updater stages a separate Python environment and checks its package version against the requested tag. It preserves the previous environment for rollback; it does not migrate or delete user data. OS packages and the root-owned updater itself are managed by rerunning the installer; releases requiring changes to those must call that out in their notes.
