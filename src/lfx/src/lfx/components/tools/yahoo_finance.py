"""
模块名称：`Yahoo Finance` 工具组件

本模块通过 `yfinance` 访问 Yahoo! Finance 数据并返回结构化结果。
主要功能包括：
- 根据方法枚举调用不同数据接口
- 支持新闻条数控制
- 将结果格式化为 `Data`

关键组件：
- `YfinanceToolComponent._yahoo_finance_tool`：核心数据获取
- `YahooFinanceMethod`：可用数据方法集合

设计背景：为金融数据查询提供统一入口。
注意事项：`yfinance` 为非官方库，数据稳定性依赖第三方。
"""

import ast
import pprint
from enum import Enum

from langchain.tools import StructuredTool
from langchain_core.tools import ToolException
from pydantic import BaseModel, Field

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.field_typing import Tool
from lfx.inputs.inputs import DropdownInput, IntInput, MessageTextInput
from lfx.log.logger import logger
from lfx.schema.data import Data


class YahooFinanceMethod(Enum):
    """Yahoo Finance 可用数据方法枚举。"""
    GET_INFO = "get_info"
    GET_NEWS = "get_news"
    GET_ACTIONS = "get_actions"
    GET_ANALYSIS = "get_analysis"
    GET_BALANCE_SHEET = "get_balance_sheet"
    GET_CALENDAR = "get_calendar"
    GET_CASHFLOW = "get_cashflow"
    GET_INSTITUTIONAL_HOLDERS = "get_institutional_holders"
    GET_RECOMMENDATIONS = "get_recommendations"
    GET_SUSTAINABILITY = "get_sustainability"
    GET_MAJOR_HOLDERS = "get_major_holders"
    GET_MUTUALFUND_HOLDERS = "get_mutualfund_holders"
    GET_INSIDER_PURCHASES = "get_insider_purchases"
    GET_INSIDER_TRANSACTIONS = "get_insider_transactions"
    GET_INSIDER_ROSTER_HOLDERS = "get_insider_roster_holders"
    GET_DIVIDENDS = "get_dividends"
    GET_CAPITAL_GAINS = "get_capital_gains"
    GET_SPLITS = "get_splits"
    GET_SHARES = "get_shares"
    GET_FAST_INFO = "get_fast_info"
    GET_SEC_FILINGS = "get_sec_filings"
    GET_RECOMMENDATIONS_SUMMARY = "get_recommendations_summary"
    GET_UPGRADES_DOWNGRADES = "get_upgrades_downgrades"
    GET_EARNINGS = "get_earnings"
    GET_INCOME_STMT = "get_income_stmt"


class YahooFinanceSchema(BaseModel):
    """Yahoo Finance 工具参数结构。"""
    symbol: str = Field(..., description="The stock symbol to retrieve data for.")
    method: YahooFinanceMethod = Field(YahooFinanceMethod.GET_INFO, description="The type of data to retrieve.")
    num_news: int | None = Field(5, description="The number of news articles to retrieve.")


class YfinanceToolComponent(LCToolComponent):
    """Yahoo Finance 工具组件。

    契约：输入股票代码与方法，输出 `Data` 列表。
    决策：基于枚举映射到 `yfinance` 的属性/方法。
    问题：接口繁多且参数不统一。
    方案：用枚举约束输入并统一调用路径。
    代价：新增方法需手动扩展枚举。
    重评：当方法稳定且覆盖率足够时保持现状。
    """
    display_name = "Yahoo! Finance"
    description = """Uses [yfinance](https://pypi.org/project/yfinance/) (unofficial package) \
to access financial data and market information from Yahoo! Finance."""
    icon = "trending-up"
    name = "YahooFinanceTool"
    legacy = True
    replacement = ["yahoosearch.YfinanceComponent"]

    inputs = [
        MessageTextInput(
            name="symbol",
            display_name="Stock Symbol",
            info="The stock symbol to retrieve data for (e.g., AAPL, GOOG).",
        ),
        DropdownInput(
            name="method",
            display_name="Data Method",
            info="The type of data to retrieve.",
            options=list(YahooFinanceMethod),
            value="get_news",
        ),
        IntInput(
            name="num_news",
            display_name="Number of News",
            info="The number of news articles to retrieve (only applicable for get_news).",
            value=5,
        ),
    ]

    def run_model(self) -> list[Data]:
        """执行查询并返回结果。"""
        return self._yahoo_finance_tool(
            self.symbol,
            self.method,
            self.num_news,
        )

    def build_tool(self) -> Tool:
        """构建可调用的 Yahoo Finance 工具。"""
        return StructuredTool.from_function(
            name="yahoo_finance",
            description="Access financial data and market information from Yahoo! Finance.",
            func=self._yahoo_finance_tool,
            args_schema=YahooFinanceSchema,
        )

    def _yahoo_finance_tool(
        self,
        symbol: str,
        method: YahooFinanceMethod,
        num_news: int | None = 5,
    ) -> list[Data]:
        """调用 `yfinance` 并返回结构化结果。

        失败语义：依赖缺失或请求异常会抛 `ToolException`。
        """
        try:
            import yfinance as yf
        except ImportError as e:
            msg = "yfinance is not installed. Please install it with `pip install yfinance`."
            raise ImportError(msg) from e

        ticker = yf.Ticker(symbol)

        try:
            if method == YahooFinanceMethod.GET_INFO:
                result = ticker.info
            elif method == YahooFinanceMethod.GET_NEWS:
                result = ticker.news[:num_news]
            else:
                result = getattr(ticker, method.value)()

            result = pprint.pformat(result)

            if method == YahooFinanceMethod.GET_NEWS:
                # 注意：新闻结果先格式化为字符串，再用 `literal_eval` 解析。
                data_list = [Data(data=article) for article in ast.literal_eval(result)]
            else:
                data_list = [Data(data={"result": result})]

        except Exception as e:
            error_message = f"Error retrieving data: {e}"
            logger.debug(error_message)
            self.status = error_message
            raise ToolException(error_message) from e

        return data_list
