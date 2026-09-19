# Pull requests and release notes

Use English for commit messages, PR titles and descriptions, and GitHub release
notes. Describe the change for someone who has not read the discussion. Product
documentation remains available in English and Chinese.

## One format, two audiences

| Material | Reader's question | Format |
| --- | --- | --- |
| Commit / PR title | What changed? | `type(scope): specific outcome` |
| PR description | Why this change, and is it ready to merge? | Summary → Changes → Validation → Upgrade notes |
| Release notes | Why update, and what must I do? | Highlights → Upgrade notes → Validation → Installation → Full changelog |

## Titles and commits

Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).
Prefer an imperative verb, a lowercase type and scope, and a title of at most
72 characters. The length is a style target, not a merge requirement.

| Type | Use |
| --- | --- |
| `feat` | New or expanded functionality |
| `fix` | Correct an existing behavior |
| `perf` | Improve performance |
| `docs` | Documentation only |
| `refactor` | Restructure code while preserving behavior |
| `ci` / `build` | Workflows, packaging or build configuration |
| `chore` / `test` | Maintenance or public verification tools |

Useful scopes include `mirror`, `alas`, `auth`, `ui`, `deploy`, `docker`, `ci`
and `deps`. Scope is optional. Avoid titles such as `update`, `fix bugs`, or
`final changes`. Do not put test counts, internal audit IDs or status badges
in the title. Dependabot titles such as `chore(deps): bump ...` are valid.

```text
feat(mirror): add a collapsible control toolbar
fix(ui): keep mobile controls on one row
docs(deploy): explain host-network upgrades
```

For an incompatible change, use `!` in the title or a `BREAKING CHANGE:` commit
footer, and explain the required migration in **Upgrade notes**. A version
number is not automatically assigned from the title.

Keep each commit focused. In the body, explain the trigger, resulting behavior
and relevant validation when the title is insufficient. Before squash merging,
review the proposed commit title and body; do not retain a list of temporary
work-in-progress messages. This guide does not change repository merge settings.

## PR descriptions

New PRs use the [PR template](../../.github/pull_request_template.md). GitHub
loads the template after it reaches the default branch; existing descriptions
are not rewritten automatically. CLI-created PRs should use a prepared body
file, for example `gh pr create --body-file <file>`, with actual newlines.

- **Summary:** one or two sentences with the trigger and before/after behavior.
- **Changes:** a few observable results; mention implementation only when it
  explains a constraint or risk. Do not list every file.
- **Validation:** name the environment and actual result. Use `Passed`, `Failed`,
  `Pending`, or `Not run — reason`. A skipped publication job on a PR is expected,
  not evidence of a successful release. Never copy example results as evidence.
- **Upgrade notes:** required user actions, changed defaults, configuration,
  migrations and rollback limits. Use `No additional upgrade steps.` only when
  that is true. Unknown impact should be stated explicitly.

For visible UI changes, one cropped before/after comparison with the viewport
size is usually sufficient. Remove private account names and device details.
For small documentation-only changes, short paragraphs or bullets are enough;
the validation table is optional. Keep large supporting details collapsed.

Apply **one primary category label** before merging:

| Label | Generated release section | Typical PRs |
| --- | --- | --- |
| `enhancement` | Features and improvements | Features, usability and performance improvements |
| `bug` | Fixes | Corrected behavior |
| `documentation` | Documentation | Documentation-only updates |
| `dependencies` | Dependencies | Dependabot and manual dependency updates |
| No matching label | Other changes | Refactoring, CI and other maintenance |

The existing `accessibility`, `python` and `github_actions` labels can accompany
a primary label; they describe the affected area. If several primary labels
are applied, the first matching section in `.github/release.yml` wins. Titles
do not apply labels automatically. A `fix` PR that also edits its documentation
normally receives `bug`, not both `bug` and `documentation`.

When updating a PR, rewrite its title and description around the final diff.
Use `Closes #123` only for a real issue that the PR fully resolves. Security
reports go through [SECURITY.md](../../SECURITY.md); publish security details
only through the coordinated disclosure process.

## Release notes

GitHub releases are the versioned changelog. Use the
[release template](../../.github/RELEASE_TEMPLATE.md) for the reader-facing
summary, and [release.yml](../../.github/release.yml) for GitHub's generated
list of merged PRs. The Markdown template is copied manually; it is not a
special GitHub template filename.

1. Choose the verified source commit and version according to the
   [release procedure](../deployment/ci-cd.md#publish-a-release). A tag triggers
   image publication; the configuration here does not create a tag or publish
   a release automatically.
2. Wait for that tag's **Builds** run, including publication, to succeed. Record
   the exact image digest and verified architectures from its summary.
3. Create a draft GitHub Release titled `ScrcpyGate vMAJOR.MINOR.PATCH`; select
   the existing tag and the correct previous release for the comparison. Mark
   release candidates as prereleases. When using `gh release create`, pass
   `--verify-tag --draft` to avoid unintentionally creating a tag.
4. Click **Generate release notes**. GitHub categorizes merged PRs by their
   labels using `.github/release.yml`. Keep dependency PRs visible; unclassified
   PRs remain in **Other changes** rather than disappearing.
5. Copy the generated list into **Full changelog** in the release template.
   Write up to three highlights, upgrade actions and validation above it.
   Replace every placeholder and remove empty sections before publication.

PR titles should remain specific enough to make the generated list useful.
Highlights explain user outcomes rather than repeating every PR title. For
example, a mobile control fix could be described as:

> Mobile mirroring controls stay on one row, with less-used actions available
> in More. The video retains more space on narrow screens.

That is a writing example, not a claim about a published version. A dependency
update should say which runtime or build component changed and mention user
impact only when verified; do not call every version bump a security fix.

Published notes must distinguish merged source, published images and completed
server deployment. The project deploys to servers manually. Link the verified
digest, not a mutable `edge` or `latest` tag, when identifying the tested image.
For the first release, describe the initial scope without inventing release
history. This repository does not currently maintain a second manual
`CHANGELOG.md`; avoid two competing records of the same release.

## References

- [GitHub: pull request templates](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/creating-a-pull-request-template-for-your-repository)
- [GitHub: automatically generated release notes](https://docs.github.com/en/repositories/releasing-projects-on-github/automatically-generated-release-notes)
- [GitHub CLI: release create](https://cli.github.com/manual/gh_release_create)
