.PHONY: install lint format type test qa bump release help

export UV_MALWARE_CHECK := 1

install: ## Install project dependencies with uv
	@uv sync

lint: ## Run Ruff checks on this repository
	@uv run --group lint ruff check .

format: ## Format code with Ruff
	@uv run --group lint ruff format .

type: ## Run ty checks on this repository
	@uv run --group qa ty check

test: ## Run repository tests
	@uv run --group test pytest -q

qa: lint type test ## Run local quality checks

bump: ## Bump the project minor version
	@uv version --bump minor

.PHONY: release
release: ## Create a GitHub release for the current version
	@version=$$(uv version --short); \
	git commit --no-verify -am "Bump $$version"; \
	git push origin main; \
	owner=$$(gh repo view --json owner -q .owner.login); \
	gh api repos/{owner}/{repo}/releases/generate-notes -f tag_name="$$version" --jq .body \
		| sed "s/ by @$$owner\$$//g; s/ by @$$owner / /g" \
		| gh release create "$$version" --notes-file -

.PHONY: help
help:
	@uv run python -c "import re; \
	[[print(f'\033[36m{m[0]:<20}\033[0m {m[1]}') for m in re.findall(r'^([a-zA-Z_.-]+):.*?## (.*)$$', open(makefile).read(), re.M)] for makefile in ('$(MAKEFILE_LIST)').strip().split()]"

.DEFAULT_GOAL := help
