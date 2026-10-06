#!/usr/bin/env bash
# Bastion (Raspberry Pi, Ubuntu, arm64) CLI tooling. No sudo needed: everything goes in ~/.local.
#   - kubectl / helm / terraform: pinned by the repo's .tool-versions (via mise)
#   - k9s, kubetail: latest, global
# Re-run any time; after bumping .tool-versions, `git pull && mise install` is enough.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/git/jimdaga/dagalab-infra}"
export PATH="$HOME/.local/bin:$PATH"

# mise: version manager that reads .tool-versions
if ! command -v mise >/dev/null; then
  curl -fsSL https://mise.run | sh
fi
grep -q 'mise activate bash' ~/.bashrc || echo 'eval "$(~/.local/bin/mise activate bash)"' >> ~/.bashrc

# Trust the repo's .tool-versions and install the pinned tools; also make them the global defaults
cd "$REPO_DIR"
mise trust --yes .tool-versions
mise install
while read -r tool version; do
  [[ -z "$tool" || "$tool" == \#* ]] && continue
  mise use --global "$tool@$version"
done < .tool-versions

# Cluster UX tools (not pinned)
mise use --global k9s@latest
mise use --global "aqua:kubetail-org/kubetail@latest"

# Completions + aliases
mkdir -p ~/.bashrc.d
cat > ~/.bashrc.d/k8s.sh <<'RC'
source <(kubectl completion bash)
source <(helm completion bash)
alias k=kubectl
complete -o default -F __start_kubectl k
RC
grep -q 'bashrc.d/k8s.sh' ~/.bashrc || echo '[ -f ~/.bashrc.d/k8s.sh ] && . ~/.bashrc.d/k8s.sh' >> ~/.bashrc

mise ls --global
