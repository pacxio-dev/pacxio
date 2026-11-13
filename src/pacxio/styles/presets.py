"""Reference design starting points, independent of the structural registry."""

from dataclasses import dataclass
from typing import Any

from pacxio.messages import t, table


@dataclass(frozen=True)
class DesignPreset:
    id: str
    name: str
    use: str
    size: tuple[float, float, float]
    material: str
    style: str = "fefco_0427"

    def parameters(self) -> dict[str, Any]:
        """Return a fresh parameter dictionary; a saved job never depends on this preset."""
        return dict(zip(("L", "W", "H"), self.size, strict=True)) | {"dim_basis": "inner"}

    def describe(self) -> dict[str, Any]:
        """Serializable catalog entry; these sizes have not been individually fold-tested."""
        return {
            "id": self.id,
            "name": t(self.name),
            "name_zh": table("zh").get(self.name, self.name),
            "style": self.style,
            "use": t(self.use),
            "params": self.parameters(),
            "material": self.material,
            "liner": "white",
            "size_status": "reference",
            "note": t("Provisional reference dimensions. Adjust for the product and material. These presets share one structure and have not been individually verified with a physical sample."),
        }


PRESETS = (
    DesignPreset(
        "dtc_mailer_slim",
        "Slim mailer",
        "Starting point for a shallow single product or small bundle",
        (220, 160, 35),
        "flute_e",
    ),
    DesignPreset(
        "dtc_mailer_standard",
        "Standard mailer",
        "Starting point for a typical direct-to-consumer product or bundle",
        (260, 180, 70),
        "flute_e",
    ),
    DesignPreset(
        "dtc_mailer_deep", "Deep mailer", "Starting point for a deeper bundle", (300, 220, 110), "flute_b"
    ),
)


def design_presets(style: str | None = None) -> tuple[DesignPreset, ...]:
    """List immutable starting points, optionally restricted to one structure."""
    return tuple(p for p in PRESETS if style is None or p.style == style)


def get_preset(preset_id: str) -> DesignPreset:
    """Resolve a stable preset identifier; callers decide how to report unknown IDs."""
    for p in PRESETS:
        if p.id == preset_id:
            return p
    raise KeyError(preset_id)
