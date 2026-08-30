"""Config-file globs shared by market upgrade-mode installs.

Keep this list in one place so the legacy web installer and the versioned
``/api/v1`` planner exclude the same files.
"""

from __future__ import annotations

from collections.abc import Iterable

# Common configuration file extensions to exclude during upgrade mode.
# These files are typically user-configured and should be preserved.
CONFIG_FILE_EXTENSIONS = [
    # 最常見的核心配置格式（幾乎每個項目都會用到）
    ".ini",  # Windows 傳統、很多老專案、Python configparser
    ".cfg",  # 通用配置（遊戲、伺服器、軟體常見）
    ".conf",  # Linux/Unix 系統服務最愛（nginx.conf, apache2.conf）
    ".config",  # 一些框架/工具的偏好（.gitconfig 其實是 .git/config）
    ".json",  # 前端、後端 API、Node.js、VS Code settings
    ".jsonc",  # JSON with Comments（VS Code、TypeScript 常用）
    ".json_c",  # JSON with Comments（VS Code、TypeScript 常用）
    ".json5",  # JSON5（支援註解、尾隨逗號、無引號 key）
    ".yaml",  # DevOps 王者（Kubernetes、Docker Compose、GitHub Actions、Ansible）
    ".yml",  # YAML 的最常見縮寫形式
    ".toml",  # Python (pyproject.toml)、Rust (Cargo.toml)、現代新寵
    ".env",  # 環境變數（dotenv 最經典，幾乎所有後端框架都支援）
    # 傳統/企業/特定生態系
    ".xml",  # Java 生態、老企業系統、Maven pom.xml、Spring
    ".properties",  # Java Properties 格式（.properties / application.properties）
    ".prop",  # 少見但有些專案用
    ".setting",  # 某些軟體的設定檔
    ".settings",  # 多數情況是資料夾，但有些是 .settings 檔
    # 特定語言/工具專屬或高度相關
    ".hcl",  # HashiCorp 配置語言（Terraform .tf 其實是 HCL，但有時單獨 .hcl）
    ".tf",  # Terraform 配置（雖然不是純副檔名，但常被當配置掃描）
    ".tfvars",  # Terraform 變數檔
    ".php",  # WordPress wp-config.php、Laravel config/*.php
    ".py",  # Python 有時直接用 .py 當配置（settings.py）
    ".js",  # Next.js / Nuxt config、雖然不推薦但常見 .config.js
    ".cson",  # CoffeeScript Object Notation（Atom 編輯器用過）
    ".plist",  # macOS / iOS 偏好設定（Info.plist、.plist）
    # 備份、臨時、使用者覆蓋類
    ".bak",  # 備份配置（常見於手動修改前）
    ".old",  # 同上
    ".example",  # 範例配置（.env.example、config.yaml.example）
    ".dist",  # 分發用範例（config.dist.json）
    ".sample",  # 同上
    ".local",  # 個人本地覆蓋（settings.local.json）
    ".user",  # 使用者特定設定
    ".override",  # 有些框架用來覆蓋預設
    # 其他偶爾出現但真實存在的
    ".md",  # 極少，但有些人把配置寫在 markdown 裡（不推薦）
    ".yaml.tpl",  # Helm chart 的模板
    ".j2",  # Ansible Jinja2 模板（雖然是模板但常被掃描）
    ".envrc",  # direnv 工具用的本地環境變數
    ".secrets",  # 有時用來放機密（不安全，但存在）
    ".secret",
]


def apply_upgrade_mode_exclusions(exclude_files: Iterable[str]) -> list[str]:
    """Copy caller exclusions and append a ``*{ext}`` glob for each config type."""
    result = list(exclude_files)
    result.extend(f"*{ext}" for ext in CONFIG_FILE_EXTENSIONS)
    return result
