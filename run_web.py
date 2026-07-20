#!/usr/bin/env python3
"""启动 Web 服务"""

import os
import sys

# 将项目根目录加入 Python 路径，确保 skill 包可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from skill.config import self_check_model_config
from web.app import app

if __name__ == "__main__":
    # 启动期模型配置自检（不发起网络请求），异常仅告警不阻断
    _check = self_check_model_config()
    if _check["ok"]:
        print(f"[自检] DeepSeek 模型配置正常：{_check['model']} @ {_check['base_url']}")
    else:
        print(f"[自检] ⚠ DeepSeek 模型配置异常：{_check['issues']}")
    # [S-014/S-022] 生产环境必须关闭 debug 模式：
    #   - Werkzeug debugger 可执行任意 Python 代码（严重 RCE 风险）
    #   - debug 模式暴露完整堆栈跟踪和源代码路径
    # 通过 FLASK_DEBUG 环境变量控制，默认关闭。
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print("=" * 50)
    print("  企业报销智能化系统 — Web 服务")
    print(f"  访问地址: http://127.0.0.1:5001  (debug={debug})")
    print("=" * 50)
    app.run(debug=debug, host="127.0.0.1", port=5001)
