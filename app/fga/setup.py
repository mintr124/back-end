"""python -m app.fga.setup"""
import json, pathlib, sys
sys.path.insert(0, ".")

from app.fga.client import fga_client
from app.core.config import settings  # import thêm

def main():
    store_id = fga_client.create_store("rag-enterprise-2")
    print(f"OPENFGA_STORE_ID={store_id}")

    # ✅ Set vào settings để _base dùng đúng store_id
    settings.openfga_store_id = store_id

    model = json.loads((pathlib.Path(__file__).parent / "model.json").read_text())
    model_id = fga_client.write_model(model)
    print(f"OPENFGA_MODEL_ID={model_id}")
    print("\nCopy 2 dòng trên vào .env rồi restart.")

if __name__ == "__main__":
    main()