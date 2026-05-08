"""
配置加载 — 读取 ~/.memfabric/config.yaml，环境变量 fallback
"""

import os
import yaml
import secrets
from pathlib import Path
from dataclasses import dataclass, field


def _default_home() -> Path:
    return Path(os.environ.get("MEMFABRIC_HOME", Path.home() / ".memfabric"))


@dataclass
class MemFabricConfig:
    # 数据目录
    home: Path = field(default_factory=_default_home)

    # 记忆限制
    max_entry_chars: int = 2200        # 单个记忆条目最大字符
    max_search_results: int = 8        # 搜索返回最大条数
    min_search_score: float = 0.35     # 搜索最低分阈值

    # 向量搜索
    embedding_model: str = "all-MiniLM-L6-v2"  # sentence-transformers 模型
    vector_weight: float = 0.7         # 向量搜索权重
    text_weight: float = 0.3           # 关键词搜索权重

    # 命名空间
    default_namespace: str = "default"

    # 安全
    encrypt_memory: bool = False

    @property
    def store_path(self) -> Path:
        return self.home / "store.db"

    @property
    def vector_path(self) -> Path:
        return self.home / "vectors"

    @property
    def config_path(self) -> Path:
        return self.home / "config.yaml"


def load_config(home: Path | None = None) -> MemFabricConfig:
    """加载配置，优先级: env > config.yaml > 默认值"""
    home = home or _default_home()
    config = MemFabricConfig(home=home)

    config_path = home / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            yaml_config = yaml.safe_load(f) or {}
        for key, val in yaml_config.items():
            if hasattr(config, key):
                setattr(config, key, val)

    # 环境变量覆盖
    if os.environ.get("MEMFABRIC_MAX_ENTRY_CHARS"):
        config.max_entry_chars = int(os.environ["MEMFABRIC_MAX_ENTRY_CHARS"])
    if os.environ.get("MEMFABRIC_ENCRYPT"):
        config.encrypt_memory = os.environ["MEMFABRIC_ENCRYPT"].lower() in ("1", "true", "yes")

    # 确保目录存在
    config.home.mkdir(parents=True, exist_ok=True)
    config.vector_path.mkdir(parents=True, exist_ok=True)

    return config
