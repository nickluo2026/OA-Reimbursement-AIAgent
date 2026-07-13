"""功能2 Schema：费用分类与限额校验 Function Call 工具定义"""

CLASSIFY_LIMIT_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "classify_and_check_limit",
            "description": (
                "根据发票内容进行费用分类，并校验金额是否超过该分类的限额。"
                "仅对发票金额超过 100 元的发票执行此校验。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "费用分类": {
                        "type": "string",
                        "enum": ["差旅", "餐饮", "住宿", "交通", "办公", "其他"],
                        "description": "根据发票内容判断的费用类型",
                    },
                    "分类依据": {
                        "type": "string",
                        "description": "判断该分类的依据，如：发票项目名称为'住宿费'",
                    },
                    "发票金额": {
                        "type": "number",
                        "description": "发票实际金额",
                    },
                    "分类限额": {
                        "type": "number",
                        "description": "该费用分类的限额（元）",
                    },
                    "是否超限": {
                        "type": "boolean",
                        "description": "发票金额 > 分类限额 → true；否则 false",
                    },
                    "校验结果": {
                        "type": "string",
                        "description": (
                            "校验结论。通过则写「金额X ≤ 限额Y，通过」；"
                            "超限则写「金额X > 限额Y，超出(Z)元，需人工审批」"
                        ),
                    },
                },
                "required": [
                    "费用分类",
                    "分类依据",
                    "发票金额",
                    "分类限额",
                    "是否超限",
                    "校验结果",
                ],
            },
        },
    }
]
