"""Model registry: track versions, metrics and the current best model."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from src.utils.common import get_logger, get_paths

logger = get_logger("model_registry")


class ModelRegistry:
    def __init__(self):
        self.paths = get_paths()
        self.registry_path = self.paths["models"] / "model_registry.json"
        self.registry = self._load()

    def _load(self) -> dict:
        if self.registry_path.exists():
            with open(self.registry_path) as fh:
                return json.load(fh)
        return {"models": [], "best_model": None, "updated_at": None}

    def register_from_metrics(self, metrics: pd.DataFrame) -> dict:
        ts = datetime.now(timezone.utc).isoformat()
        for _, row in metrics.iterrows():
            self.registry["models"].append({
                "name": row["model"],
                "f1": float(row["f1"]),
                "roc_auc": float(row["roc_auc"]),
                "pr_auc": float(row["pr_auc"]),
                "accuracy": float(row["accuracy"]),
                "registered_at": ts,
                "artifact": str(self.paths["models"] / f"{row['model']}.pkl"),
            })
        best = metrics.sort_values("f1", ascending=False).iloc[0]
        self.registry["best_model"] = {
            "name": best["model"], "f1": float(best["f1"]),
            "roc_auc": float(best["roc_auc"]),
            "artifact": str(self.paths["models"] / f"{best['model']}.pkl"),
        }
        self.registry["updated_at"] = ts
        self._save()
        logger.info("Registered %d models; best=%s", len(metrics), best["model"])
        return self.registry

    def get_best_model(self) -> dict | None:
        return self.registry.get("best_model")

    def list_versions(self) -> list:
        return self.registry.get("models", [])

    def _save(self):
        with open(self.registry_path, "w") as fh:
            json.dump(self.registry, fh, indent=2)


if __name__ == "__main__":
    import pandas as pd
    m = pd.read_csv(get_paths()["reports"] / "model_evaluation.csv")
    ModelRegistry().register_from_metrics(m)
