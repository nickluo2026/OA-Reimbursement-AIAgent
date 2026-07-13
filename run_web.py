#!/usr/bin/env python3
"""启动 Web 服务"""

import sys
import os

# 将项目根目录加入 Python 路径，确保 skill 包可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from web.app import app

if __name__ == "__main__":
    print("=" * 50)
    print("  报销智能化系统 — Web 服务")
    print("  访问地址: http://127.0.0.1:5001")
    print("=" * 50)
    app.run(debug=True, host="0.0.0.0", port=5001)
