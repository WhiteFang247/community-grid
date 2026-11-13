"""Conservative offline model recommendations; no downloads or installations."""
from __future__ import annotations
from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class ModelOption:
    name: str
    minimum_ram_gib: float
    preferred_ram_gib: float
    purpose: str

CATALOG = (
    ModelOption("Qwen2.5-1.5B-Instruct-GGUF Q4", 2.5, 4, "lightweight chat"),
    ModelOption("Qwen2.5-3B-Instruct-GGUF Q4", 4.5, 7, "chat and simple tools"),
    ModelOption("Qwen2.5-7B-Instruct-GGUF Q4", 8, 12, "general assistant"),
    ModelOption("Qwen2.5-14B-Instruct-GGUF Q4", 13, 20, "higher quality, slower CPU inference"),
)

def recommend(capabilities: dict, reserve_gib: float = 3.0) -> dict:
    total = capabilities.get("memory_total_bytes")
    if not isinstance(total, int) or total <= 0:
        return {"selected": None, "reason": "RAM not detected; manual selection required", "candidates": []}
    available = max(0.0, total / (1024 ** 3) - reserve_gib)
    eligible = [model for model in CATALOG if model.minimum_ram_gib <= available]
    selected = eligible[-1] if eligible else None
    return {
        "selected": asdict(selected) if selected else None,
        "reason": "Heuristic only: benchmark model, context size and tool reliability before installing",
        "usable_ram_gib_estimate": round(available, 2),
        "candidates": [asdict(model) for model in eligible],
        "installation_performed": False,
    }
