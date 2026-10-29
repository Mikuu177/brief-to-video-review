from ipaddress import ip_address
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    database_url: str = "postgresql+psycopg://creator:creator@127.0.0.1:55432/creator_review"
    redis_url: str = "redis://127.0.0.1:56379/0"
    data_dir: Path = ROOT / "data"
    auth_mode: Literal["local", "session"] = "local"
    session_secret: str = ""
    admin_username: str = "admin"
    admin_password: str = ""
    reviewer_username: str = "reviewer"
    reviewer_password: str = ""
    cookie_secure: bool = False
    ark_api_key: str = ""
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ark_local_address: str = ""
    asr_local_address: str = ""
    tos_local_address: str = ""
    ark_video_model: str = ""
    ark_preprocess_model: str = ""
    ark_text_model: str = ""
    # Explicit acknowledgement after the live probe; never inferred from the model's name.
    ark_video_audio_verified: bool = False
    asr_api_key: str = ""
    asr_resource_id: str = "volc.bigasr.auc_turbo"
    storage_backend: Literal["local", "tos"] = "local"
    tos_access_key: str = ""
    tos_secret_key: str = ""
    tos_bucket: str = ""
    tos_region: str = "cn-beijing"
    tos_endpoint: str = "tos-cn-beijing.volces.com"
    review_max_bytes: int = 200 * 1024 * 1024
    review_max_duration_ms: int = 300000
    review_window_ms: int = 45000
    review_overlap_ms: int = 5000
    review_full_fps: float = 2
    review_detail_fps: float = 5
    review_live_evaluation_approved: bool = False
    provider_timeout_seconds: int = 600
    review_job_timeout_seconds: int = Field(default=7200, ge=60, le=86400)
    queue_name: str = "creator-review"
    source_integrity_enabled: bool = False
    video_content_enabled: bool = False

    @field_validator("ark_local_address", "asr_local_address", "tos_local_address")
    @classmethod
    def validate_ark_local_address(cls, value: str) -> str:
        return str(ip_address(value)) if value else ""

    def missing(self, purpose: str) -> list[str]:
        required = ["ark_api_key", "ark_text_model"]
        if purpose == "video":
            required += [
                "ark_video_model",
                "ark_preprocess_model",
                "ark_video_audio_verified",
                "asr_api_key",
                "tos_access_key",
                "tos_secret_key",
                "tos_bucket",
            ]
        return [key.upper() for key in required if not getattr(self, key)]


settings = Settings()
