#!/usr/bin/env bash
# Shallow-clone Autumbra-recommended OSS into a staging directory.
# Does NOT vendor into tervory-tank. Prefer adapters + onsite code-vault.
set -euo pipefail

DEST="${1:-${AUTUMBRA_OSS_VAULT:-./autumbra-oss-vault}}"
mkdir -p "$DEST"
cd "$DEST"

clone_one() {
  local name="$1" url="$2"
  if [[ -d "$name/.git" ]]; then
    echo "skip $name (already present)"
    return 0
  fi
  echo "clone $name"
  git clone --depth 1 --single-branch "$url" "$name"
  git -C "$name" rev-parse HEAD | awk -v n="$name" '{print n, $1}'
}

clone_one wazuh https://github.com/wazuh/wazuh.git
clone_one vector https://github.com/vectordotdev/vector.git
clone_one fluent-bit https://github.com/fluent/fluent-bit.git
clone_one steampipe https://github.com/turbot/steampipe.git
clone_one opencti https://github.com/OpenCTI-Platform/opencti.git
clone_one vllm https://github.com/vllm-project/vllm.git
clone_one nemo-guardrails https://github.com/NVIDIA-NeMo/Guardrails.git
clone_one llm-guard https://github.com/protectai/llm-guard.git
clone_one ansible https://github.com/ansible/ansible.git
clone_one terraform https://github.com/hashicorp/terraform.git

echo "done → $DEST"
df -h "$DEST" | tail -1
