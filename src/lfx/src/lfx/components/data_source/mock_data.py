"""
模块名称：模拟数据生成组件

本模块提供用于测试与演示的模拟数据生成能力，支持输出 `Message`、`Data` 与 `DataFrame`。
主要功能包括：
- 生成随机文本消息
- 生成结构化 `JSON` 数据
- 生成业务风格的表格数据

关键组件：
- `MockDataGeneratorComponent`

设计背景：在缺少真实数据源时提供可复用的测试输入。
注意事项：`DataFrame` 输出依赖 `pandas`，缺失时会走降级路径。
"""

import secrets
from datetime import datetime, timedelta, timezone

from lfx.custom.custom_component.component import Component
from lfx.io import Output
from lfx.schema import Data, DataFrame
from lfx.schema.message import Message


class MockDataGeneratorComponent(Component):
    """模拟数据生成组件

    契约：
    - 输入：无
    - 输出：`Message`/`Data`/`DataFrame`
    - 副作用：记录日志并更新 `self.status`
    - 失败语义：生成失败时返回包含错误信息的降级输出
    """

    display_name = "Mock Data"
    description = "Generate mock data for testing and development."
    icon = "database"
    name = "MockDataGenerator"

    inputs = []

    outputs = [
        Output(display_name="Result", name="dataframe_output", method="generate_dataframe_output"),
        Output(display_name="Result", name="message_output", method="generate_message_output"),
        Output(display_name="Result", name="data_output", method="generate_data_output"),
    ]

    def build(self) -> DataFrame:
        """默认构建入口，独立运行时返回 `DataFrame`

        契约：
        - 输入：无
        - 输出：`DataFrame`
        - 副作用：触发 `DataFrame` 生成流程
        - 失败语义：生成失败时返回错误 `DataFrame`
        """
        return self.generate_dataframe_output()

    def generate_message_output(self) -> Message:
        """生成 `Message` 输出

        契约：
        - 输入：无
        - 输出：`Message`
        - 副作用：更新 `self.status`
        - 失败语义：异常时返回包含错误提示的 `Message`
        """
        try:
            self.log("Generating Message mock data")
            message = self._generate_message()
            self.status = f"Generated Lorem Ipsum message ({len(message.text)} characters)"
        except (ValueError, TypeError) as e:
            error_msg = f"Error generating Message data: {e!s}"
            self.log(error_msg)
            self.status = f"Error: {error_msg}"
            return Message(text=f"Error: {error_msg}")
        else:
            return message

    def generate_data_output(self) -> Data:
        """生成 `Data` 输出

        契约：
        - 输入：无
        - 输出：`Data`
        - 副作用：更新 `self.status`
        - 失败语义：异常时返回包含错误信息的 `Data`
        """
        try:
            # 注意：`Data` 输出固定为单条记录
            record_count = 1
            self.log(f"Generating Data mock data with {record_count} record")
            data = self._generate_data(record_count)
            self.status = f"Generated JSON data with {len(data.data.get('records', []))} record(s)"
        except (ValueError, TypeError) as e:
            error_msg = f"Error generating Data: {e!s}"
            self.log(error_msg)
            self.status = f"Error: {error_msg}"
            return Data(data={"error": error_msg, "success": False})
        else:
            return data

    def generate_dataframe_output(self) -> DataFrame:
        """生成 `DataFrame` 输出

        契约：
        - 输入：无
        - 输出：`DataFrame`
        - 副作用：记录日志
        - 失败语义：异常时返回包含错误信息的降级 `DataFrame`
        """
        try:
            # 注意：`DataFrame` 输出固定为 50 条记录
            record_count = 50
            self.log(f"Generating DataFrame mock data with {record_count} records")
            return self._generate_dataframe(record_count)
        except (ValueError, TypeError) as e:
            error_msg = f"Error generating DataFrame: {e!s}"
            self.log(error_msg)

            try:
                import pandas as pd

                error_df = pd.DataFrame({"error": [error_msg]})
                return DataFrame(error_df)
            except ImportError:
                # 注意：无 `pandas` 时仍返回 `DataFrame` 包装
                return DataFrame({"error": [error_msg]})

    def _generate_message(self) -> Message:
        """生成 `Message` 文本内容

        契约：
        - 输入：无
        - 输出：`Message`
        - 副作用：无
        - 失败语义：无
        """
        lorem_ipsum_texts = [
            (
                "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor "
                "incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud "
                "exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat."
            ),
            (
                "Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
                "pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt "
                "mollit anim id est laborum."
            ),
            (
                "Sed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium doloremque laudantium, "
                "totam rem aperiam, eaque ipsa quae ab illo inventore veritatis et quasi architecto "
                "beatae vitae dicta sunt explicabo."
            ),
            (
                "Nemo enim ipsam voluptatem quia voluptas sit aspernatur aut odit aut fugit, "
                "sed quia consequuntur magni dolores eos qui ratione voluptatem sequi nesciunt."
            ),
            (
                "Neque porro quisquam est, qui dolorem ipsum quia dolor sit amet, consectetur, "
                "adipisci velit, sed quia non numquam eius modi tempora incidunt ut labore et dolore "
                "magnam aliquam quaerat voluptatem."
            ),
        ]

        selected_text = secrets.choice(lorem_ipsum_texts)
        return Message(text=selected_text)

    def _generate_data(self, record_count: int) -> Data:
        """生成包含 `JSON` 结构的 `Data`

        契约：
        - 输入：记录数量
        - 输出：`Data`
        - 副作用：无
        - 失败语义：无
        """
        # 注意：样例分类数据
        companies = [
            "TechCorp",
            "DataSystems",
            "CloudWorks",
            "InnovateLab",
            "DigitalFlow",
            "SmartSolutions",
            "FutureTech",
            "NextGen",
        ]
        departments = ["Engineering", "Sales", "Marketing", "HR", "Finance", "Operations", "Support", "Research"]
        statuses = ["active", "pending", "completed", "cancelled", "in_progress"]
        categories = ["A", "B", "C", "D"]

        # 生成样例记录
        records = []
        base_date = datetime.now(tz=timezone.utc) - timedelta(days=365)

        for i in range(record_count):
            record = {
                "id": f"REC-{1000 + i}",
                "name": f"Sample Record {i + 1}",
                "company": secrets.choice(companies),
                "department": secrets.choice(departments),
                "status": secrets.choice(statuses),
                "category": secrets.choice(categories),
                "value": round(secrets.randbelow(9901) + 100 + secrets.randbelow(100) / 100, 2),
                "quantity": secrets.randbelow(100) + 1,
                "rating": round(secrets.randbelow(41) / 10 + 1, 1),
                "is_active": secrets.choice([True, False]),
                "created_date": (base_date + timedelta(days=secrets.randbelow(366))).isoformat(),
                "tags": [
                    secrets.choice(
                        [
                            "important",
                            "urgent",
                            "review",
                            "approved",
                            "draft",
                            "final",
                        ]
                    )
                    for _ in range(secrets.randbelow(3) + 1)
                ],
            }
            records.append(record)

        # 构造主数据结构
        data_structure = {
            "records": records,
            "summary": {
                "total_count": record_count,
                "active_count": sum(1 for r in records if r["is_active"]),
                "total_value": sum(r["value"] for r in records),
                "average_rating": round(sum(r["rating"] for r in records) / record_count, 2),
                "categories": list({r["category"] for r in records}),
                "companies": list({r["company"] for r in records}),
            },
        }

        return Data(data=data_structure)

    def _generate_dataframe(self, record_count: int) -> DataFrame:
        """生成业务风格的 `DataFrame`

        关键路径（三步）：
        1) 尝试导入 `pandas`
        2) 生成行数据并构建 `DataFrame`
        3) 追加计算列并返回结果

        异常流：`pandas` 缺失或生成失败走降级路径。
        性能瓶颈：大规模数据生成与 `pandas` 处理。
        排障入口：日志信息与异常消息。
        
        契约：
        - 输入：记录数量
        - 输出：`DataFrame`
        - 副作用：记录日志
        - 失败语义：异常时返回降级 `DataFrame`
        """
        try:
            import pandas as pd

            self.log(f"pandas imported successfully, version: {pd.__version__}")
        except ImportError as e:
            self.log(f"pandas not available: {e!s}, creating simple DataFrame fallback")
            # 注意：无 `pandas` 时构建简易结构
            data_result = self._generate_data(record_count)
            # 将 `Data` 转为简易表格结构
            try:
                # 注意：从 `Data` 构建基本表结构
                records = data_result.data.get("records", [])
                if records:
                    # 注意：使用首行字段作为列名
                    columns = list(records[0].keys()) if records else ["error"]
                    rows = [list(record.values()) for record in records]
                else:
                    columns = ["error"]
                    rows = [["pandas not available"]]

                # 生成字典式表格表示
                simple_df_data = {
                    col: [row[i] if i < len(row) else None for row in rows] for i, col in enumerate(columns)
                }

                # 注意：返回 `DataFrame` 包装（由 `LangFlow` 负责展示）
                return DataFrame(simple_df_data)
            except (ValueError, TypeError):
                # 注意：终极降级为字符串化 `Data`
                return DataFrame({"data": [str(data_result.data)]})

        try:
            self.log(f"Starting DataFrame generation with {record_count} records")

            # 注意：业务风格样例数据
            first_names = [
                "John",
                "Jane",
                "Michael",
                "Sarah",
                "David",
                "Emily",
                "Robert",
                "Lisa",
                "William",
                "Jennifer",
            ]
            last_names = [
                "Smith",
                "Johnson",
                "Williams",
                "Brown",
                "Jones",
                "Garcia",
                "Miller",
                "Davis",
                "Rodriguez",
                "Martinez",
            ]
            cities = [
                "New York",
                "Los Angeles",
                "Chicago",
                "Houston",
                "Phoenix",
                "Philadelphia",
                "San Antonio",
                "San Diego",
                "Dallas",
                "San Jose",
            ]
            countries = ["USA", "Canada", "UK", "Germany", "France", "Australia", "Japan", "Brazil", "India", "Mexico"]
            products = [
                "Product A",
                "Product B",
                "Product C",
                "Product D",
                "Product E",
                "Service X",
                "Service Y",
                "Service Z",
            ]

            # 生成 `DataFrame` 行数据
            data = []
            base_date = datetime.now(tz=timezone.utc) - timedelta(days=365)

            self.log("Generating row data...")
            for i in range(record_count):
                row = {
                    "customer_id": f"CUST-{10000 + i}",
                    "first_name": secrets.choice(first_names),
                    "last_name": secrets.choice(last_names),
                    "email": f"user{i + 1}@example.com",
                    "age": secrets.randbelow(63) + 18,
                    "city": secrets.choice(cities),
                    "country": secrets.choice(countries),
                    "product": secrets.choice(products),
                    "order_date": (base_date + timedelta(days=secrets.randbelow(366))).strftime("%Y-%m-%d"),
                    "order_value": round(secrets.randbelow(991) + 10 + secrets.randbelow(100) / 100, 2),
                    "quantity": secrets.randbelow(10) + 1,
                    "discount": round(secrets.randbelow(31) / 100, 2),
                    "is_premium": secrets.choice([True, False]),
                    "satisfaction_score": secrets.randbelow(10) + 1,
                    "last_contact": (base_date + timedelta(days=secrets.randbelow(366))).strftime("%Y-%m-%d"),
                }
                data.append(row)
            # 创建 `DataFrame`
            self.log("Creating pandas DataFrame...")
            df = pd.DataFrame(data)
            self.log(f"DataFrame created with shape: {df.shape}")

            # 添加计算列
            self.log("Adding calculated columns...")
            df["full_name"] = df["first_name"] + " " + df["last_name"]
            df["discounted_value"] = df["order_value"] * (1 - df["discount"])
            df["total_value"] = df["discounted_value"] * df["quantity"]

            # 年龄分组边界常量
            age_group_18_25 = 25
            age_group_26_35 = 35
            age_group_36_50 = 50
            age_group_51_65 = 65

            # 注意：年龄分组构建，失败时走降级路径
            try:
                df["age_group"] = pd.cut(
                    df["age"],
                    bins=[
                        0,
                        age_group_18_25,
                        age_group_26_35,
                        age_group_36_50,
                        age_group_51_65,
                        100,
                    ],
                    labels=[
                        "18-25",
                        "26-35",
                        "36-50",
                        "51-65",
                        "65+",
                    ],
                )
            except (ValueError, TypeError) as e:
                self.log(f"Error creating age groups with pd.cut: {e!s}, using simple categorization")
                df["age_group"] = df["age"].apply(
                    lambda x: "18-25"
                    if x <= age_group_18_25
                    else "26-35"
                    if x <= age_group_26_35
                    else "36-50"
                    if x <= age_group_36_50
                    else "51-65"
                    if x <= age_group_51_65
                    else "65+"
                )

            self.log(f"Successfully generated DataFrame with shape: {df.shape}, columns: {list(df.columns)}")
            # 注意：必须使用 `LangFlow` `DataFrame` 包装
            # 注意：返回 `DataFrame` 时不要设置 `self.status`，避免展示异常
            return DataFrame(df)

        except (ValueError, TypeError) as e:
            error_msg = f"Error generating DataFrame: {e!s}"
            self.log(error_msg)
            # 注意：返回 `DataFrame` 时不要设置 `self.status`，避免展示异常
            # 返回包含错误信息的降级 `DataFrame`
            try:
                error_df = pd.DataFrame(
                    {
                        "error": [error_msg],
                        "timestamp": [datetime.now(tz=timezone.utc).isoformat()],
                        "attempted_records": [record_count],
                    }
                )
                return DataFrame(error_df)
            except (ValueError, TypeError) as fallback_error:
                # 最终降级：返回最简错误表
                self.log(f"Fallback also failed: {fallback_error!s}")
                simple_error_df = pd.DataFrame({"error": [error_msg]})
                return DataFrame(simple_error_df)
