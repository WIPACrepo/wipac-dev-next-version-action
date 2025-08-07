# wipac-dev-next-version-action

GitHub Action to Compute a Project’s Next Semantic Version Powered by Commit, Tag, and Release Analysis

## Overview

This GHA package determines the next semantic version (`MAJOR.MINOR.PATCH`) based on:

- commit messages since the last version tag
- optionally ignoring changes to certain paths (e.g. `assets/**`, `.github/**`, etc.)

This action is designed for CI/CD workflows that automatically publish new releases only when needed.

## How it Works

1. Finds the most recent tag matching `vX.Y.Z` or `X.Y.Z`
2. Gets the git diff since that tag
3. Inspects the new commit messages (titles) for one of the following "bump strings":
    - `[major]`
    - `[minor]`
    - `[patch]`, `[fix]`, or `[bump]`
    - `[no-bump]`
4. If there weren't any "bump strings"
    - checks if the changed files are all ignorable (see [`ignore-paths`](#inputs)), or
    - whether to make a patch bump anyways (see [`force-patch-if-no-commit-token`](#inputs))
5. Computes the next version accordingly
6. Outputs the bumped version, or an empty string if no release is needed
    - The version number does not have a v-prefix (e.g. `1.2.5`)

## Inputs

| Name                             | Required | Default   | Description                                                                                                                               |
|----------------------------------|----------|-----------|-------------------------------------------------------------------------------------------------------------------------------------------|
| `force-patch-if-no-commit-token` | `false`  | `"false"` | If true, bumps a patch version even if no commit message contains a "bump string"                                                         |
| `ignore-paths`                   | `false`  | `""`      | Newline-delimited glob patterns (e.g. `resources/**`) — if all changed files match these and there's no "bump string", no release is made |

## Outputs

| Name      | Description                                                                               |
|-----------|-------------------------------------------------------------------------------------------|
| `version` | The computed semantic version (e.g. `1.2.3`), or empty string if no new version is needed |

## Example Usage

The following is based on `WIPACrepo/wipac-dev-actions-testbed`'s [`cicd.yml`](https://github.com/WIPACrepo/wipac-dev-actions-testbed/blob/main/.github/workflows/cicd.yml):

```yaml
jobs:
  ...

  tag-and-release:
  # only run on main/master/default
  if: format('refs/heads/{0}', github.event.repository.default_branch) == github.ref
  needs: [
    ...
  ]
  runs-on: ubuntu-latest
  concurrency: tag-and-release  # prevent any possible race conditions
  steps:
    - uses: actions/checkout@v4
      with:
        fetch-depth: 0  # required to see tags and commits
        ref: ${{ github.sha }}  # lock to triggered commit ('github.ref' is dynamic)

    ...

    - uses: WIPACrepo/wipac-dev-next-version-action@...
      id: next-version
      with:
        force-patch-if-no-commit-token: ...
        ignore-paths: |
          ...

    - if: steps.next-version.outputs.version != ''
      name: Tag New Version
      run: |
        git tag v${{ steps.next-version.outputs.version }}  # note: prepend 'v'
        git push origin --tags

    - if: steps.next-version.outputs.version != ''
      name: Make Release
      ...
```
