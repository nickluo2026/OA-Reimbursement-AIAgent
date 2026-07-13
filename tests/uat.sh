#!/bin/bash
set -e
cd "$(dirname "$0")/.."

echo "========== UAT 开始 =========="
echo ""

echo "[1/7] 依赖安装检查..."
python3 -c "import flask, sqlalchemy, langgraph, pymupdf, yaml, structlog; print('  ✅ 依赖OK')" 2>/dev/null || echo "  ⚠️ 部分依赖缺失（需 pip install -r requirements.txt）"
echo ""

echo "[2/7] 单元测试..."
python3 -m pytest tests/ -v --tb=short 2>&1 | tail -20
echo ""

echo "[3/7] 配置加载..."
python3 -c "from skill.config import get_category_limits; print('  分类限额:', get_category_limits())" 2>&1
echo ""

echo "[4/7] 数据库初始化..."
OA_DB_PATH=/tmp/uat_test.db python3 -c "
from skill.database import init_db, get_all_tables
init_db()
print('  ✅ 表:', get_all_tables())
" 2>&1
rm -f /tmp/uat_test.db
echo ""

echo "[5/7] Git 安全检查..."
ISSUES=0
for pattern in "^\.env$" "\.db$" "\.pyc$" "__pycache__" "\.codebuddy" "^uploads/" "\.pytest_cache" "\.DS_Store"; do
    if git ls-files --cached | grep -q "$pattern"; then
        echo "  ❌ 发现敏感文件: $pattern"
        ISSUES=$((ISSUES+1))
    fi
done
[ $ISSUES -eq 0 ] && echo "  ✅ 无敏感文件在暂存区"
echo ""

echo "[6/7] 暂存区文件数..."
COUNT=$(git ls-files --cached | wc -l | tr -d ' ')
echo "  📁 共 $COUNT 个文件待提交"
echo ""

echo "[7/7] 文件清单:"
git ls-files --cached | sort
echo ""

echo "========== UAT 完成 =========="
