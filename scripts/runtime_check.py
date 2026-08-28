#!/usr/bin/env python3
"""检查并按需安装金融数据层依赖，始终绑定当前 Python 解释器。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from akshare_api import akshare_status, ensure_akshare  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="检查深知可信投研 Python 运行环境")
    parser.add_argument(
        "--install",
        action="store_true",
        help="缺少 akshare 时安装到当前 Python 解释器，不切换 pip 环境",
    )
    args = parser.parse_args()

    status = akshare_status()
    error = ""
    if args.install and not status["ready"]:
        try:
            ensure_akshare(auto_install=True)
        except SystemExit as exc:
            error = str(exc)
        status = akshare_status()

    status["install_attempted"] = bool(args.install)
    if error:
        status["error"] = error
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
