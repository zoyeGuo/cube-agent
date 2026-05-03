# -*- coding: utf-8 -*-
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_file=".env", env_file_encoding="utf-8")

    minimax_api_key: str
    minimax_base_url: str = "https://api.minimaxi.com/v1"
    model: str = "MiniMax-M2.7"
    model_fallbacks: str = ""
    max_tokens: int = 2048

    tts_base_url: str = "https://api.minimaxi.com/v1"
    tts_model: str = "speech-2.8-hd"
    tts_voice_id: str = "male-qn-qingse"
    tts_max_chars: int = 300

    sessions_db: str = str(Path.home() / ".secretary" / "sessions.db")
    soul_file: str = str(Path.home() / ".secretary" / "memory" / "SOUL.md")
    memory_dir: str = str(Path.home() / ".secretary" / "memory")
    memory_extract_every: int = 3

    compression_threshold: float = 0.50
    session_recent_turns: int = 8
    session_summary_trigger_turns: int = 12

    system_prompt: str = (
        "\u4f60\u662f\u4e00\u4e2a\u7b80\u6d01\u76f4\u63a5\u7684\u6570\u5b57\u52a9\u624b\u3002"
        "\u56de\u7b54\u8981\u77ed\uff0c\u53bb\u6389\u6240\u6709\u5e9f\u8bdd\u548c\u5ba2\u5957\uff0c\u76f4\u63a5\u7ed9\u7ed3\u8bba\u6216\u7b54\u6848\u3002"
        "\u4e0d\u8981\u7528\u300c\u60a8\u597d\u300d\u300c\u5f53\u7136\u300d\u300c\u597d\u7684\u300d\u7b49\u5f00\u573a\u767d\uff0c\u4e0d\u8981\u603b\u7ed3\uff0c\u4e0d\u8981\u91cd\u590d\u95ee\u9898\u3002"
        "\u5982\u679c\u53ef\u4ee5\u4e00\u53e5\u8bdd\u8bf4\u6e05\u695a\uff0c\u5c31\u4e00\u53e5\u8bdd\u3002"
        "\u7981\u6b62\u4f7f\u7528\u4efb\u4f55 Markdown \u683c\u5f0f\uff0c\u4e0d\u8981\u7528 **\u52a0\u7c97**\u3001# \u6807\u9898\u3001- \u5217\u8868\u3001`\u4ee3\u7801\u5757` \u7b49\u7b26\u53f7\uff0c\u53ea\u8f93\u51fa\u7eaf\u6587\u672c\u3002"
        "\n\n\u4f60\u6709\u5de5\u5177\u3002\u5f53\u7528\u6237\u7684\u8bf7\u6c42\u53ef\u4ee5\u7531\u67d0\u4e2a\u5de5\u5177\u5b8c\u6210\u65f6\uff0c\u5fc5\u987b\u8c03\u7528\u8be5\u5de5\u5177\uff0c"
        "\u4e0d\u5f97\u53e3\u5934\u63cf\u8ff0\u8bf4\u201c\u6211\u5c06\u2026\u201d\u6216\u201c\u5df2\u7ecf\u2026\u201d\u3002"
        "\n\u53e3\u5934\u627f\u8bfa\u800c\u4e0d\u8c03\u7528\u5de5\u5177\u662f\u9519\u8bef\u884c\u4e3a\u3002\u8c03\u7528\u5de5\u5177\u524d\u65e0\u9700\u544a\u77e5\u7528\u6237\uff0c\u76f4\u63a5\u6267\u884c\u3002"
    )


settings = Settings()  # type: ignore[call-arg]
