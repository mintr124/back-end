# Start postgres-fga
docker compose up -d postgres-fga

# Wait for 5 second to start postgres
Start-Sleep -Seconds 5

# Migrate schema OpenFGA into postgres
docker compose run --rm openfga migrate

# Start all
docker compose up -d

# Run setup (one time only)
$env:OPENFGA_URL="http://localhost:8080"; python -m app.fga.setup

# Copy STORE_ID and MODEL_ID into .env and config.py (openfga_store_id and openfga_model_id), then restart the application.

# To check store that created, browse: http://localhost:8080/stores
# Result example: {"stores":[{"id":"01KMTFH0653Q23BT8R9BCA4GQN","name":"rag-enterprise","created_at":"2026-03-28T15:03:14.380041Z","updated_at":"2026-03-28T15:03:14.380041Z","deleted_at":null}],"continuation_token":""}

# To view schemas by UI, browse: https://play.fga.dev/sandbox/?fga_api_host=localhost%3A8080&fga_api_scheme=http&store=<enter-store-id-here>
# Example: https://play.fga.dev/sandbox/?fga_api_host=localhost%3A8080&fga_api_scheme=http&store=01KMTFH0653Q23BT8R9BCA4GQN
