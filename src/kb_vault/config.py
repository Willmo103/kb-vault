import json
from pathlib import Path
from kb_core.config import Config as BaseConfig


class Config(BaseConfig):
    fallback_config_path = Path("C:/Users/Will/Desktop/SKILLS/configs/scan_config.json")

    @property
    def vault_config_path(self) -> Path:
        return self.configs_dir / "kb-vault.json"

    def load_vault_config(self) -> dict:
        if self.vault_config_path.exists():
            try:
                return json.loads(self.vault_config_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Fallback to original scan_config.json
        if self.fallback_config_path.exists():
            try:
                original = json.loads(self.fallback_config_path.read_text(encoding="utf-8"))
                return {"scan_paths": original.get("vault", [])}
            except Exception:
                pass

        return {"scan_paths": []}

    def save_vault_config(self, config_data: dict):
        self.configs_dir.mkdir(parents=True, exist_ok=True)
        self.vault_config_path.write_text(
            json.dumps(config_data, indent=2), encoding="utf-8"
        )
