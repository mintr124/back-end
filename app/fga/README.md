# Start postgres-fga
docker compose up -d postgres-fga

# Wait for 5 second to start postgres
Start-Sleep -Seconds 5

# Migrate schema OpenFGA into postgres
docker compose run --rm openfga migrate

# Start all
docker compose up -d

# Create store for OpenFGA
$env:OPENFGA_URL="http://localhost:8080"; python -m app.fga.setup
# Copy STORE_ID into .env and config.py (openfga_store_id).

# Create model for OpenFGA
docker exec -it rag-api python -c "import json, pathlib, sys; sys.path.insert(0, '.'); from app.fga.client import fga_client; model = json.loads((pathlib.Path('app/fga/model.json')).read_text()); model_id = fga_client.write_model(model); print(f'OPENFGA_MODEL_ID={model_id}')"
# Copy MODEL_ID into .env and config.py (openfga_model_id).

# To check store that created, browse: http://localhost:8080/stores
# Result example: {"stores":[{"id":"01KMTFH0653Q23BT8R9BCA4GQN","name":"rag-enterprise","created_at":"2026-03-28T15:03:14.380041Z","updated_at":"2026-03-28T15:03:14.380041Z","deleted_at":null}],"continuation_token":""}

# To view schemas by UI, browse: https://play.fga.dev/sandbox/?fga_api_host=localhost%3A8080&fga_api_scheme=http&store=<enter-store-id-here>
# Example: https://play.fga.dev/sandbox/?fga_api_host=localhost%3A8080&fga_api_scheme=http&store=01KMTFH0653Q23BT8R9BCA4GQN
