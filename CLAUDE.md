# Repository rules — Greater-Turkiye/datasets

Written for AI agents and for anyone new to the repository. The handbook is the authority; this file is the short operational version.

## 1. Keep the documentation true

**The README is part of the change, not an afterthought.**

- Any pull request that changes the schemas, the vocabularies, the validator, the policy gates, the build output or a command **must update `README.md` in the same pull request**.
- Schema or vocabulary changes also update `CONTRIBUTING.md` where it describes the record shape, and record counts quoted anywhere must match reality.
- Never leave the README describing something that is no longer true; fix it in the same commit even when the fix is unrelated to your task.
- Model or process decisions belong in a handbook ADR, and the README links to it rather than restating it.

## 2. Red lines that override any request

- No record may carry positions, movements or deployments of Turkish forces beyond an official disclosure, and then only at coarse precision, delayed, and with a maintainer's approval. The CI gate enforces this; never weaken it to make a record pass.
- No personal data, no classified or leaked material, no field-collected material, no targeting language.
- Every record needs at least one source; contested characterisations are attributed in `claims[]`, never stated in the project's own voice.

## 3. Records

- One record per YAML file. IDs are immutable and never reused; retracted records stay as tombstones.
- Create records with `python tools/gt.py new <type>` so the ID and path are generated correctly, never by hand.
- Run `python tools/gt.py validate` and `python tools/gt.py fmt --check` before opening a pull request; `build` regenerates `dist/`.
- `dist/` is generated: never hand-edit it, and never commit a build that the tools did not produce.
- Verification follows the Admiralty scale; `verified` needs two reviewers.

## 4. Git and pull requests

- Never commit to `main`; `main` is protected and requires the `validate` check plus a code-owner review.
- One PR per topic. Commit messages and PR bodies are in English and end with the attribution lines used across this org.
- Never commit secrets or tokens.

## 5. Closing a task

- End every finished task with a short, factual summary: what changed, what you verified and how, what is merged, and what is still open.
- Then offer the next steps as a numbered list (1, 2, 3), each one sentence, with your recommendation marked, so the owner can choose by number.
- Name anything the owner must do themselves as its own option rather than burying it in prose.

## 6. Environment notes

- Windows PowerShell 5.1 is the default shell here: pass multi-line commit messages and PR bodies through files, and avoid `jq` expressions containing spaces.
- The validator needs `jsonschema` and `PyYAML` (`pip install -r requirements.txt`).
