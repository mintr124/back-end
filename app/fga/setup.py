"""
One-time FGA bootstrap script. Creates the store and uploads the authorization model.
Run with: python -m app.fga.setup
"""
import json
import pathlib
import sys

sys.path.insert(0, ".")

from app.core.config import settings
from app.fga.client import fga_client


# Create the FGA store, upload model.json, and print the IDs to copy into .env.
def main():
    store_id = fga_client.create_store("rag-enterprise-6")
    print(f"OPENFGA_STORE_ID={store_id}")

    settings.openfga_store_id = store_id

    model = json.loads((pathlib.Path(__file__).parent / "model.json").read_text())
    model_id = fga_client.write_model(model)
    print(f"OPENFGA_MODEL_ID={model_id}")
    print("\nCopy STORE_ID and MODEL_ID into .env and config.py (openfga_store_id and openfga_model_id), then restart the application.")

if __name__ == "__main__":
    main()