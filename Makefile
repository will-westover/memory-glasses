help:
	@grep -E "^[a-z-]+:.*## " Makefile | sed -E "s/:.*## /	/" | expand -t 12

test: ## Run the server tests
	cd server && uv run pytest -q

evals: ## Ask every test question and grade the answers
	cd server && uv run python -m memory.evals --mode agent --mode plain $(ARGS)

moments: ## List the recorded moments
	cd server && uv run python -m memory.db list

deploy: ## Build the server and deploy it to Cloud Run
	R2_ACCOUNT_ID=$$(grep "^R2_ACCOUNT_ID=" server/.env | cut -d= -f2) server/deploy/deploy.sh

app: ## Build the iPhone app for the Simulator
	cd app && xcodebuild -scheme MemoryGlasses -configuration Debug -destination "generic/platform=iOS Simulator" build | grep -E "error|BUILD"

protocol: ## Test the Bluetooth protocol and check the app against it
	firmware/protocol/check.sh

firmware: ## Build the glasses firmware
	cd firmware/glasses && cargo build --release --bin glasses

flash: ## Build the firmware and flash it over USB
	firmware/glasses/flash.sh

publish: ## Publish the firmware so the glasses update themselves
	firmware/glasses/publish.sh

.PHONY: help test evals moments deploy app protocol firmware flash publish
