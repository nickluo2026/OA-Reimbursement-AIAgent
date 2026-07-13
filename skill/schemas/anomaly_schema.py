"""功能3 Schema：异常输入检查 Function Call 工具定义"""

ANOMALY_CHECK_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "detect_anomaly",
            "description": (
                "对发票数据进行异常检测，识别字段缺失、格式错误、重复报销、"
                "票据过期、金额异常、日期异常等风险，给出总体结论与明细。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "总体结论": {
                        "type": "string",
                        "enum": ["通过", "预警", "拦截"],
                        "description": (
                            "拦截：存在严重异常（如字段缺失/重复报销/金额异常），直接退回；"
                            "预警：存在轻微风险（如票据即将过期），提示审批人注意；"
                            "通过：无异常"
                        ),
                    },
                    "异常明细": {
                        "type": "array",
                        "description": "检测到的所有异常项列表，无异常则为空数组",
                        "items": {
                            "type": "object",
                            "properties": {
                                "异常类型": {
                                    "type": "string",
                                    "enum": [
                                        "字段缺失",
                                        "格式错误",
                                        "重复报销",
                                        "票据过期",
                                        "金额异常",
                                        "日期异常",
                                    ],
                                    "description": "异常类型枚举",
                                },
                                "异常描述": {
                                    "type": "string",
                                    "description": "具体异常描述，如：发票号码缺失",
                                },
                                "严重程度": {
                                    "type": "string",
                                    "enum": ["严重", "警告", "提示"],
                                    "description": "严重=拦截，警告=预警，提示=通过但记录",
                                },
                            },
                            "required": ["异常类型", "异常描述", "严重程度"],
                        },
                    },
                    "检查摘要": {
                        "type": "string",
                        "description": "本次异常检查的总结说明",
                    },
                },
                "required": ["总体结论", "异常明细", "检查摘要"],
            },
        },
    }
]
