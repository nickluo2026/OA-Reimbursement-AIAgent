"""pytest fixtures 和通用配置"""

import os
import sys
import tempfile
from unittest.mock import MagicMock

import pytest

# 测试统一使用独立临时数据库，避免污染真实 oa_agent.db
_TEST_DB_PATH = os.path.join(tempfile.gettempdir(), "oa_test_agent.db")
os.environ.setdefault("OA_DB_PATH", _TEST_DB_PATH)

# 清理上一次测试残留的数据库文件，避免 init_db -> create_all 时 table already exists
if os.path.exists(_TEST_DB_PATH):
    os.unlink(_TEST_DB_PATH)

# 将项目根目录加入 Python 搜索路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ═══════════════════════════════════════════════
# Mock 数据
# ═══════════════════════════════════════════════


@pytest.fixture
def sample_invoice_data() -> dict:
    """标准发票样本（正常数据）"""
    return {
        "发票类型": "增值税普通发票",
        "发票号码": "12345678",
        "发票代码": "044001900111",
        "开票日期": "2026-06-01",
        "购买方名称": "XX科技有限公司",
        "购买方税号": "91110108MA01XXXXX",
        "销售方名称": "YY酒店管理有限公司",
        "销售方税号": "91110108MA02YYYYY",
        "金额": "283.02",
        "税率": "6%",
        "税额": "16.98",
        "价税合计_大写": "叁佰元整",
        "价税合计_小写": 300.00,
        "发票金额": 300.00,
        "商品明细": [
            {
                "项目名称": "住宿费",
                "规格型号": "",
                "单位": "天",
                "数量": "1",
                "单价": "283.02",
                "金额": "283.02",
                "税率": "6%",
                "税额": "16.98",
            }
        ],
    }


@pytest.fixture
def sample_invoice_missing_fields() -> dict:
    """缺失必填字段的发票数据"""
    return {
        "发票号码": "",
        "开票日期": "",
        "发票金额": 0,
        "销售方名称": "",
        "购买方名称": "XX公司",
        "价税合计_小写": 0,
    }


@pytest.fixture
def sample_invoice_high_amount() -> dict:
    """高金额发票（超过异常阈值）"""
    return {
        "发票类型": "增值税专用发票",
        "发票号码": "99999999",
        "发票代码": "044001900111",
        "开票日期": "2026-06-15",
        "购买方名称": "XX科技有限公司",
        "购买方税号": "91110108MA01XXXXX",
        "销售方名称": "ZZ设备有限公司",
        "销售方税号": "91110108MA03ZZZZZ",
        "金额": "18867.92",
        "税率": "6%",
        "税额": "1132.08",
        "价税合计_小写": 20000.00,
        "发票金额": 20000.00,
    }


@pytest.fixture
def sample_invoice_expired() -> dict:
    """过期发票"""
    return {
        "发票类型": "增值税普通发票",
        "发票号码": "11111111",
        "开票日期": "2025-01-01",
        "购买方名称": "XX公司",
        "销售方名称": "AA公司",
        "价税合计_小写": 500.00,
        "发票金额": 500.00,
    }


@pytest.fixture
def sample_anomaly_result_pass() -> dict:
    return {
        "总体结论": "通过",
        "异常明细": [],
        "检查摘要": "规则检查未发现异常",
    }


@pytest.fixture
def sample_anomaly_result_block() -> dict:
    return {
        "总体结论": "拦截",
        "异常明细": [
            {
                "异常类型": "字段缺失",
                "异常描述": "必填字段「发票号码」缺失或为空",
                "严重程度": "严重",
            },
        ],
        "检查摘要": "检测到 1 项异常: 字段缺失",
    }


@pytest.fixture
def sample_classify_result() -> dict:
    return {
        "费用分类": "餐饮",
        "分类依据": "根据发票项目名称判断",
        "发票金额": 450,
        "分类限额": 300,
        "是否超限": True,
        "校验结果": "金额450 > 限额300，超出150元，需人工审批",
    }


# ═══════════════════════════════════════════════
# 行程单 Mock 数据
# ═══════════════════════════════════════════════


@pytest.fixture
def sample_itinerary_data() -> dict:
    """标准行程单样本（正常数据，3段行程）"""
    return {
        "申请日期": "2026-06-10",
        "行程开始日期": "2026-06-08",
        "行程结束日期": "2026-06-09",
        "手机号": "138****1234",
        "总行程数": 3,
        "总金额_元": "85.50",
        "行程详情": [
            {
                "序号": 1,
                "车型": "经济型",
                "上车时间": "2026-06-08 09:30",
                "城市": "北京",
                "起点": "北京站",
                "终点": "国贸",
                "里程_公里": "8.5",
                "金额_元": "28.50",
            },
            {
                "序号": 2,
                "车型": "经济型",
                "上车时间": "2026-06-08 18:00",
                "城市": "北京",
                "起点": "国贸",
                "终点": "北京站",
                "里程_公里": "9.0",
                "金额_元": "30.00",
            },
            {
                "序号": 3,
                "车型": "舒适型",
                "上车时间": "2026-06-09 10:00",
                "城市": "北京",
                "起点": "酒店",
                "终点": "机场",
                "里程_公里": "12.0",
                "金额_元": "27.00",
            },
        ],
    }


@pytest.fixture
def sample_itinerary_missing_fields() -> dict:
    """缺失必填字段的行程单数据"""
    return {
        "申请日期": "2026-06-10",
        "行程开始日期": "",
        "行程结束日期": "",
        "总金额_元": "",
        "行程详情": [],
    }


@pytest.fixture
def sample_itinerary_amount_mismatch() -> dict:
    """总金额与明细合计不一致的行程单"""
    return {
        "申请日期": "2026-06-10",
        "行程开始日期": "2026-06-08",
        "行程结束日期": "2026-06-09",
        "总金额_元": "100.00",  # 明细合计实际为 55
        "行程详情": [
            {
                "序号": 1,
                "车型": "经济型",
                "上车时间": "2026-06-08 09:30",
                "城市": "北京",
                "起点": "A",
                "终点": "B",
                "里程_公里": "5",
                "金额_元": "30.00",
            },
            {
                "序号": 2,
                "车型": "经济型",
                "上车时间": "2026-06-09 10:00",
                "城市": "北京",
                "起点": "B",
                "终点": "C",
                "里程_公里": "6",
                "金额_元": "25.00",
            },
        ],
    }


@pytest.fixture
def sample_itinerary_anomaly_pass() -> dict:
    """行程单异常检测通过结果"""
    return {
        "总体结论": "通过",
        "异常明细": [],
        "检查摘要": "[规则引擎] 规则检查未发现异常",
    }


@pytest.fixture
def sample_itinerary_anomaly_block() -> dict:
    """行程单异常检测拦截结果"""
    return {
        "总体结论": "拦截",
        "异常明细": [
            {
                "异常类型": "字段缺失",
                "异常描述": "必填字段「行程详情」缺失或为空",
                "严重程度": "严重",
            },
        ],
        "检查摘要": "[规则引擎] 检测到 1 项异常: 字段缺失",
    }


@pytest.fixture
def sample_itinerary_verify_pass() -> dict:
    """行程单合理性校验通过结果"""
    return {
        "校验结论": "通过",
        "总金额校验": "总金额 85.50 元与明细合计 85.50 元一致，不超过申请金额 100 元",
        "行程天数": 2,
        "单笔最高金额": "单笔最高金额 30.00 元，未超阈值 500 元",
        "日期合理性": "所有行程上车时间均在行程日期范围内",
        "行程连续性": "行程按时间排序连续性合理",
        "校验明细": [
            {
                "校验项目": "总金额匹配",
                "校验结果": "通过",
                "说明": "总金额 85.50 元与明细合计 85.50 元一致",
            },
            {"校验项目": "行程天数", "校验结果": "通过", "说明": "行程天数 2 天"},
            {"校验项目": "单笔最高金额", "校验结果": "通过", "说明": "单笔最高金额 30.00 元"},
            {"校验项目": "日期合理性", "校验结果": "通过", "说明": "均在范围内"},
            {"校验项目": "行程连续性", "校验结果": "通过", "说明": "连续性合理"},
        ],
    }


# ═══════════════════════════════════════════════
# Mock Helpers
# ═══════════════════════════════════════════════


def mock_deepseek_return(data: dict) -> MagicMock:
    """创建一个返回指定数据的 Mock"""
    mock = MagicMock()
    mock.return_value = data
    return mock


# ═══════════════════════════════════════════════
# 数据库 fixtures
# ═══════════════════════════════════════════════
@pytest.fixture
def fresh_db():
    """每次测试使用干净的数据库（重建全部表，结束后保留表结构供其他测试）"""
    from skill.database import Base, _engine

    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    yield


@pytest.fixture
def sample_reimbursement(fresh_db):
    """创建一条待审批报销单（含发票），返回 request_id"""
    from skill.utils.db_store import save_invoice, save_reimbursement

    rid = "REQ-TEST-001"
    save_reimbursement(
        request_id=rid,
        employee_id="EMP-2026",
        apply_amount=358.50,
        apply_date="2026-07-14",
        reason="北京出差住宿费",
        expense_category="差旅-住宿",
    )
    save_invoice(
        {
            "发票类型": "增值税普通发票",
            "发票号码": "88886666",
            "发票金额": 358.50,
            "销售方名称": "XX酒店",
            "开票日期": "2026-07-10",
        },
        rid,
        "",
    )
    return rid


@pytest.fixture
def client():
    """Flask 测试客户端（供 API / 端到端测试复用）"""
    from web.app import app as flask_app

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c
