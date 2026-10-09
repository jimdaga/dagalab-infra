#!/usr/bin/env bash
# Bastion (Raspberry Pi, Ubuntu, arm64) CLI tooling. Everything goes in ~/.local except dig (apt, sudo).
#   - kubectl / helm / terraform: pinned by the repo's .tool-versions (via mise)
#   - k9s, kubetail, argocd, vault, aws, yq: latest, global
#   - dig: dnsutils from apt (only prompts for sudo if missing)
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

# Cluster/ops tools (not pinned)
for tool in k9s argocd vault aws-cli yq; do
  mise use --global "$tool@latest"
done
# kubetail isn't in mise's registries and tags releases as cli/vX.Y.Z, so fetch the binary directly
KUBETAIL_VERSION=0.18.0
if [[ "$(kubetail --version 2>/dev/null)" != *"$KUBETAIL_VERSION"* ]]; then
  curl -fsSL -o ~/.local/bin/kubetail \
    "https://github.com/kubetail-org/kubetail/releases/download/cli/v${KUBETAIL_VERSION}/kubetail-linux-arm64"
  chmod +x ~/.local/bin/kubetail
fi

# dig for DNS debugging (UniFi gateway, Cloudflare)
if ! command -v dig >/dev/null; then
  if [[ -t 0 ]] || sudo -n true 2>/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y -qq dnsutils
  else
    echo "skipping dig: no terminal for sudo; re-run interactively (ssh -t) to install dnsutils" >&2
  fi
fi

# Completions: generated once into bash-completion's per-command dir, so they load lazily on first
# <Tab> instead of on every login (running kubectl/helm/argocd at login cost ~4s on the Pi).
export PATH="$HOME/.local/share/mise/shims:$PATH"   # non-interactive: mise tools via shims
COMP_DIR=~/.local/share/bash-completion/completions
mkdir -p "$COMP_DIR"
kubectl completion bash > "$COMP_DIR/kubectl"
helm completion bash    > "$COMP_DIR/helm"
argocd completion bash  > "$COMP_DIR/argocd"
yq shell-completion bash > "$COMP_DIR/yq"
# `k` alias: bash-completion looks up completions/k when you tab after `k`
printf '%s\n' ". $COMP_DIR/kubectl" 'complete -o default -F __start_kubectl k' > "$COMP_DIR/k"

# Login-time shell config stays cheap: aliases + `complete -C` hooks only
mkdir -p ~/.bashrc.d
cat > ~/.bashrc.d/k8s.sh <<'RC'
alias k=kubectl
complete -C "$(command -v vault)" vault
complete -C "$(command -v aws_completer)" aws
RC
grep -q 'bashrc.d/k8s.sh' ~/.bashrc || echo '[ -f ~/.bashrc.d/k8s.sh ] && . ~/.bashrc.d/k8s.sh' >> ~/.bashrc

mise ls --global
