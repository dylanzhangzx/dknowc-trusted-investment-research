#!/usr/bin/env python3
"""深知可信投研配置：纯环境变量（公开版约定，不落本地 Key）

环境变量：
  DKNOWC_API_KEY                    深知可信搜索 Key（政策/标准检索必需）
  DKNOWC_TRUSTED_SEARCH_ENDPOINT    可选，覆盖检索接口地址
"""

import os
from typing import Any, Dict


def load_config() -> Dict[str, Any]:
    dknowc: Dict[str, Any] = {
        "base_url": "https://open.dknowc.cn/dependable/search",
    }
    if os.environ.get("DKNOWC_TRUSTED_SEARCH_ENDPOINT"):
        dknowc["base_url"] = os.environ["DKNOWC_TRUSTED_SEARCH_ENDPOINT"]

    search_params: Dict[str, Any] = {
        "service_area": "全国",
        "eff_time": "2026年",
        "policy": True,
        "item": True,
        "knowBase": True,
        "segmentCount": 3,
        "simplified": True,
    }

    return {"dknowc": dknowc, "search_params": search_params}


def validate_config(config: Dict[str, Any]) -> bool:
    key = os.environ.get("DKNOWC_API_KEY", "").strip()
    if not key.startswith("sk-"):
        print("[config] DKNOWC_API_KEY 未配置或格式不正确（sk- 开头）")
        return False
    return True


def get_dknowc_headers(config: Dict[str, Any]) -> Dict[str, str]:
    return {
        "api-key": os.environ["DKNOWC_API_KEY"],
        "Content-Type": "application/json",
    }
