# 1.0.0

Candidate; founder approval in Skala is required.

- Checkov 3.3.21 (Apache-2.0), isolated at `/opt/checkov` in the Radix image.
- One finding per failed check, with Checkov's rule ID and check text only.
  Severity is `unknown`; IaC findings alone never block a build.
- No downloads, external modules, platform calls, source execution or external
  Python checks. Same skipped folders and proxy network block as scan.static.
- Missing, failed or timed-out Checkov is `not run`, never a clean scan.
- Five eval cases: open Terraform security group, privileged Kubernetes pod,
  root Dockerfile, clean Terraform/Dockerfile set and empty-input refusal.
  Expected rule sets were captured from this pinned Checkov in a Radix image
  with `--network none`; the adjacent fixture files match the case inputs.
- Engine is a byte copy of `agents/radix/scanlib/iac.py`. Skala executes the
  cases through Radix's room-key-only `/evals/run`, using its existing room path.

Review checkpoint: the intentionally unsafe Docker and Kubernetes fixture data
use `.fixture` filenames so they cannot be mistaken for deployable configuration.
Eval input filenames, contents, Checkov rules and engine bytes are unchanged.
