"""Backend CI must supply its required configuration before importing the app."""

from pathlib import Path

import pytest
import yaml

from modules.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("job_name", ["baseline", "python-315-compatibility"])
def test_backend_ci_environment_can_initialize_settings_without_dotenv(job_name):
    workflow = yaml.safe_load((PROJECT_ROOT / ".github/workflows/quality.yml").read_text())
    environment = workflow["jobs"][job_name]["env"]
    required = {name for name, field in Settings.model_fields.items() if field.is_required()}
    assert required <= environment.keys(), (
        f"Missing required settings: {required - environment.keys()}"
    )

    Settings(_env_file=None, **environment)
