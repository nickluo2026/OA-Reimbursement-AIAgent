"""行程单 Schema：Function Call 工具定义

支持火车票、机票、出租车票等行程类票据的结构化提取与合理性校验。

- ITINERARY_EXTRACT_TOOL：OCR 提取行程明细
- ITINERARY_VERIFY_TOOL：行程合理性校验（金额/日期/连续性）
"""

ITINERARY_EXTRACT_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "extract_itinerary",
            "description": (
                "从行程单文本中提取全部字段内容，包括申请日期、行程时间、"
                "行程详情（车型/上车时间/城市/起终点/里程/金额）等。"
                '无数据的字段填空字符串 ""，无数据的数字填 0'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "申请日期": {
                        "type": "string",
                        "description": "申请日期，格式 YYYY-MM-DD",
                    },
                    "行程开始日期": {
                        "type": "string",
                        "description": "行程开始日期，格式 YYYY-MM-DD",
                    },
                    "行程结束日期": {
                        "type": "string",
                        "description": "行程结束日期，格式 YYYY-MM-DD",
                    },
                    "手机号": {
                        "type": "string",
                        "description": "关联手机号（脱敏展示）",
                    },
                    "总行程数": {
                        "type": "number",
                        "description": "行程总条数",
                    },
                    "总金额_元": {
                        "type": "string",
                        "description": "所有行程合计金额",
                    },
                    "行程详情": {
                        "type": "array",
                        "description": "行程明细列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "序号": {
                                    "type": "number",
                                    "description": "行程序号",
                                },
                                "车型": {
                                    "type": "string",
                                    "description": "车型，如：经济型、舒适型、快车等",
                                },
                                "上车时间": {
                                    "type": "string",
                                    "description": "上车时间，格式 YYYY-MM-DD HH:MM",
                                },
                                "城市": {
                                    "type": "string",
                                    "description": "所在城市",
                                },
                                "起点": {
                                    "type": "string",
                                    "description": "行程起点",
                                },
                                "终点": {
                                    "type": "string",
                                    "description": "行程终点",
                                },
                                "里程_公里": {
                                    "type": "string",
                                    "description": "行程里程（公里）",
                                },
                                "金额_元": {
                                    "type": "string",
                                    "description": "该行程金额",
                                },
                            },
                        },
                    },
                },
                "required": [
                    "申请日期",
                    "行程开始日期",
                    "行程结束日期",
                    "总金额_元",
                    "行程详情",
                ],
            },
        },
    }
]


ITINERARY_VERIFY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "verify_itinerary",
            "description": (
                "对行程单进行合理性校验：总金额匹配、行程天数、单笔最高金额、"
                "日期合理性、行程连续性，给出总体校验结论与明细。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "校验结论": {
                        "type": "string",
                        "enum": ["通过", "预警", "拦截"],
                        "description": (
                            "拦截：存在严重不合理（如总金额与明细不符且差额大）；"
                            "预警：存在轻微风险（如单笔金额偏高）；"
                            "通过：校验均合理"
                        ),
                    },
                    "总金额校验": {
                        "type": "string",
                        "description": "总金额与明细合计是否一致，及与申请金额的匹配情况",
                    },
                    "行程天数": {
                        "type": "number",
                        "description": "行程天数（结束日期 - 开始日期 + 1）",
                    },
                    "单笔最高金额": {
                        "type": "string",
                        "description": "单笔最高行程金额及其是否超阈值",
                    },
                    "日期合理性": {
                        "type": "string",
                        "description": "所有行程上车时间是否在行程日期范围内",
                    },
                    "行程连续性": {
                        "type": "string",
                        "description": "按时间排序后行程间隔是否合理",
                    },
                    "校验明细": {
                        "type": "array",
                        "description": "各项校验明细列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "校验项目": {
                                    "type": "string",
                                    "description": "如：总金额匹配、行程天数、单笔最高金额等",
                                },
                                "校验结果": {
                                    "type": "string",
                                    "enum": ["通过", "预警", "拦截"],
                                    "description": "该项校验结论",
                                },
                                "说明": {
                                    "type": "string",
                                    "description": "该项校验的具体说明",
                                },
                            },
                            "required": ["校验项目", "校验结果", "说明"],
                        },
                    },
                },
                "required": [
                    "校验结论",
                    "总金额校验",
                    "行程天数",
                    "单笔最高金额",
                    "日期合理性",
                    "行程连续性",
                    "校验明细",
                ],
            },
        },
    }
]
