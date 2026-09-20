"""Lightweight model registry for CVD risk stratification models.

Tracks trained model versions, their performance metrics, hyperparameters,
and file locations in a JSON registry (``models/model_registry.json``).  This
gives the rest of the project a single source of truth for "which model is
best" and where its ``.pkl`` artifact lives.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import common

logger = logging.getLogger(__name__)


class ModelRegistry:
    """Register, query, and version trained models via a JSON manifest."""

    def __init__(self, path: str = common.REGISTRY_PATH):
        self.path = path
        common.ensure_dirs()
        self.registry: Dict[str, Any] = self._load()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def _load(self) -> Dict[str, Any]:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError):  # pragma: no cover
                logger.warning("Registry at %s corrupt; starting fresh.", self.path)
        return {"models": [], "best_model": None, "updated_at": None}

    def _save(self) -> None:
        self.registry["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.registry, fh, indent=2)
        logger.info("Registry saved to %s", self.path)

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register_model(
        self,
        name: str,
        artifact_path: str,
        metrics: Dict[str, float],
        params: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        selection_metric: str = "f1_macro",
    ) -> Dict[str, Any]:
        """Register a new model version and update the best-model pointer.

        A monotonically increasing integer version is assigned per model name.
        """
        existing = [m for m in self.registry["models"] if m["name"] == name]
        version = len(existing) + 1

        rel_artifact = os.path.relpath(artifact_path, common.PROJECT_ROOT)
        entry = {
            "name": name,
            "version": version,
            "artifact_path": rel_artifact,
            "metrics": {k: float(v) for k, v in metrics.items()},
            "params": params or {},
            "metadata": metadata or {},
            "selection_metric": selection_metric,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        self.registry["models"].append(entry)
        self._update_best(selection_metric)
        self._save()
        logger.info(
            "Registered %s v%d | %s=%.4f",
            name, version, selection_metric,
            metrics.get(selection_metric, float("nan")),
        )
        return entry

    def _update_best(self, selection_metric: str) -> None:
        best = None
        for m in self.registry["models"]:
            score = m["metrics"].get(selection_metric)
            if score is None:
                continue
            if best is None or score > best["metrics"][selection_metric]:
                best = m
        if best is not None:
            self.registry["best_model"] = {
                "name": best["name"],
                "version": best["version"],
                "artifact_path": best["artifact_path"],
                "selection_metric": selection_metric,
                "score": best["metrics"][selection_metric],
            }

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def get_best_model(self) -> Optional[Dict[str, Any]]:
        """Return the best-model pointer (name, version, artifact_path, score)."""
        return self.registry.get("best_model")

    def get_best_model_path(self) -> Optional[str]:
        """Return the absolute artifact path of the current best model."""
        best = self.get_best_model()
        if not best:
            return None
        return os.path.join(common.PROJECT_ROOT, best["artifact_path"])

    def list_versions(self, name: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all registered versions, optionally filtered by model name."""
        models = self.registry["models"]
        if name is not None:
            models = [m for m in models if m["name"] == name]
        return sorted(models, key=lambda m: (m["name"], m["version"]))

    def get_model(self, name: str, version: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Return a specific model entry (latest version if not specified)."""
        versions = self.list_versions(name)
        if not versions:
            return None
        if version is None:
            return versions[-1]
        for m in versions:
            if m["version"] == version:
                return m
        return None

    def summary(self) -> str:
        """Return a human-readable summary of the registry contents."""
        lines = ["Model Registry Summary", "=" * 60]
        best = self.get_best_model()
        if best:
            lines.append(
                f"BEST: {best['name']} v{best['version']} "
                f"({best['selection_metric']}={best['score']:.4f})"
            )
        lines.append("-" * 60)
        for m in self.list_versions():
            metrics = m["metrics"]
            lines.append(
                f"{m['name']:<22} v{m['version']} | "
                f"acc={metrics.get('accuracy', float('nan')):.4f} "
                f"f1={metrics.get('f1_macro', float('nan')):.4f} "
                f"auc={metrics.get('roc_auc_ovr', float('nan')):.4f}"
            )
        return "\n".join(lines)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    reg = ModelRegistry()
    print(reg.summary())


if __name__ == "__main__":
    main()
