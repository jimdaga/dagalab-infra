.DEFAULT_GOAL := help

ARGOCD_CHART_VERSION := $(shell awk '/chart: argo-cd/{getline; gsub(/"/,"",$$2); print $$2}' argocd/apps/argocd/application.yaml)
CHARTS := $(wildcard helm/charts/*)

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

.PHONY: lint
lint: ## Lint local Helm charts
	@for c in $(CHARTS); do helm lint $$c || exit 1; done

.PHONY: template
template: ## Render local Helm charts to stdout (sanity check)
	@for c in $(CHARTS); do helm template $$(basename $$c) $$c || exit 1; done

.PHONY: bootstrap
bootstrap: ## Install Argo CD and the root app on the current kube-context
	@echo "Bootstrapping Argo CD $(ARGOCD_CHART_VERSION) into context: $$(kubectl config current-context)"
	helm upgrade --install argocd argo-cd \
		--repo https://argoproj.github.io/argo-helm \
		--version $(ARGOCD_CHART_VERSION) \
		--namespace argocd --create-namespace \
		-f argocd/apps/argocd/values.yaml \
		--wait
	kubectl apply -f argocd/bootstrap/root-app.yaml

.PHONY: argocd-password
argocd-password: ## Print the initial Argo CD admin password
	@kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
