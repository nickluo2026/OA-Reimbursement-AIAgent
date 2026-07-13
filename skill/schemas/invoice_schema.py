"""功能1 Schema：OCR 发票提取 Function Call 工具定义"""

EXTRACT_INVOICE_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "extract_invoice",
            "description": (
                "从发票文本中提取全部字段内容，包括发票头信息、"
                "购买方/销售方信息、金额明细、商品明细等。"
                "无数据的字段填空字符串 \"\"，无数据的数字填 0"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    # ---- 发票头 ----
                    "发票类型": {
                        "type": "string",
                        "description": "发票类型，如：增值税专用发票、增值税普通发票、电子普通发票等",
                    },
                    "发票号码": {
                        "type": "string",
                        "description": "发票号码",
                    },
                    "发票代码": {
                        "type": "string",
                        "description": "发票代码（部分发票有）",
                    },
                    "开票日期": {
                        "type": "string",
                        "description": "开票日期，格式 YYYY-MM-DD",
                    },
                    # ---- 购买方 ----
                    "购买方名称": {
                        "type": "string",
                        "description": "购买方公司名称",
                    },
                    "购买方税号": {
                        "type": "string",
                        "description": "购买方统一社会信用代码/纳税人识别号",
                    },
                    # ---- 销售方 ----
                    "销售方名称": {
                        "type": "string",
                        "description": "销售方公司名称",
                    },
                    "销售方税号": {
                        "type": "string",
                        "description": "销售方统一社会信用代码/纳税人识别号",
                    },
                    # ---- 金额明细 ----
                    "金额": {
                        "type": "string",
                        "description": "不含税金额（合计）",
                    },
                    "税率": {
                        "type": "string",
                        "description": "税率/征收率，如 6%、1%",
                    },
                    "税额": {
                        "type": "string",
                        "description": "税额",
                    },
                    "价税合计_大写": {
                        "type": "string",
                        "description": "价税合计大写金额，如 壹拾肆元壹角肆分",
                    },
                    "价税合计_小写": {
                        "type": "number",
                        "description": "价税合计小写金额（数字），如 14.14",
                    },
                    "发票金额": {
                        "type": "number",
                        "description": "发票实际金额数字，取自「价税合计_小写」，作为后续校验基准",
                    },
                    # ---- 商品明细 ----
                    "商品明细": {
                        "type": "array",
                        "description": "商品/服务明细列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "项目名称": {
                                    "type": "string",
                                    "description": "商品或服务名称",
                                },
                                "规格型号": {
                                    "type": "string",
                                    "description": "规格型号",
                                },
                                "单位": {
                                    "type": "string",
                                    "description": "计量单位",
                                },
                                "数量": {
                                    "type": "string",
                                    "description": "数量",
                                },
                                "单价": {
                                    "type": "string",
                                    "description": "不含税单价",
                                },
                                "金额": {
                                    "type": "string",
                                    "description": "该项不含税金额",
                                },
                                "税率": {
                                    "type": "string",
                                    "description": "该项税率",
                                },
                                "税额": {
                                    "type": "string",
                                    "description": "该项税额",
                                },
                            },
                        },
                    },
                },
                "required": [
                    "发票号码",
                    "开票日期",
                    "发票金额",
                ],
            },
        },
    }
]
