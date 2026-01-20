"""
模块名称：Yahoo Finance 数据组件

本模块封装 yfinance 的数据访问逻辑，用于查询 Yahoo! Finance 市场与公司数据。
主要功能：
- 根据 symbol 与 method 调用 yfinance；
- 将返回结果包装为 Data/DataFrame 输出。

关键组件：
- YfinanceComponent：Yahoo Finance 数据组件入口。

设计背景：为 LLM/RAG 流程提供可结构化消费的金融数据入口。
注意事项：yfinance 为非官方库，数据与可用性依赖外部服务。
"""

import ast
import pprint
from enum import Enum

import yfinance as yf
from langchain_core.tools import ToolException
from pydantic import BaseModel, Field

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import DropdownInput, IntInput, MessageTextInput
from lfx.io import Output
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame


class YahooFinanceMethod(Enum):
    """支持的 yfinance 调用方法枚举。"""
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
    """工具模式下的输入参数 Schema。"""
    symbol: str = Field(..., description="The stock symbol to retrieve data for.")
    method: YahooFinanceMethod = Field(YahooFinanceMethod.GET_INFO, description="The type of data to retrieve.")
    num_news: int | None = Field(5, description="The number of news articles to retrieve.")


class YfinanceComponent(Component):
    """Yahoo Finance 数据组件

    契约：输入 `symbol/method`，输出 `list[Data]` 或 `DataFrame`。
    关键路径：1) 创建 Ticker 2) 调用 yfinance 方法 3) 格式化结果。
    决策：通过枚举控制可调用方法
    问题：直接暴露任意方法存在不确定性
    方案：限制在 `YahooFinanceMethod` 中的能力
    代价：新增方法需更新枚举
    重评：当需要动态扩展方法时
    """
    display_name = "Yahoo! Finance"
    description = """Uses [yfinance](https://pypi.org/project/yfinance/) (unofficial package) \
to access financial data and market information from Yahoo! Finance."""
    icon = "trending-up"

    inputs = [
        MessageTextInput(
            name="symbol",
            display_name="Stock Symbol",
            info="The stock symbol to retrieve data for (e.g., AAPL, GOOG).",
            tool_mode=True,
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

    outputs = [
        Output(display_name="DataFrame", name="dataframe", method="fetch_content_dataframe"),
    ]

    def run_model(self) -> DataFrame:
        """兼容父类调用入口，返回 DataFrame 输出。"""
        return self.fetch_content_dataframe()

    def _fetch_yfinance_data(self, ticker: yf.Ticker, method: YahooFinanceMethod, num_news: int | None) -> str:
        """调用 yfinance 获取结果并格式化为字符串

        契约：返回格式化字符串；失败抛 `ToolException`。
        关键路径：1) 按 method 选择调用 2) pprint 格式化结果。
        异常流：调用失败抛 `ToolException` 并写入 status。
        """
        try:
            if method == YahooFinanceMethod.GET_INFO:
                result = ticker.info
            elif method == YahooFinanceMethod.GET_NEWS:
                result = ticker.news[:num_news]
            else:
                result = getattr(ticker, method.value)()
            return pprint.pformat(result)
        except Exception as e:
            error_message = f"Error retrieving data: {e}"
            logger.debug(error_message)
            self.status = error_message
            raise ToolException(error_message) from e

    def fetch_content(self) -> list[Data]:
        """获取数据并返回 Data 列表

        契约：返回 `list[Data]`；异常抛 `ToolException`。
        关键路径：1) 构造参数 2) 调用工具函数 3) 返回结果。
        异常流：非 ToolException 统一包装为 ToolException。
        """
        try:
            return self._yahoo_finance_tool(
                self.symbol,
                YahooFinanceMethod(self.method),
                self.num_news,
            )
        except ToolException:
            raise
        except Exception as e:
            error_message = f"Unexpected error: {e}"
            logger.debug(error_message)
            self.status = error_message
            raise ToolException(error_message) from e

    def _yahoo_finance_tool(
        self,
        symbol: str,
        method: YahooFinanceMethod,
        num_news: int | None = 5,
    ) -> list[Data]:
        """执行指定方法并返回 Data 列表

        契约：新闻模式返回多条 Data，其余模式返回单条 Data。
        关键路径：1) 创建 Ticker 2) 调用数据获取 3) 按类型封装输出。
        """
        ticker = yf.Ticker(symbol)
        result = self._fetch_yfinance_data(ticker, method, num_news)

        if method == YahooFinanceMethod.GET_NEWS:
            data_list = [
                Data(text=f"{article['title']}: {article['link']}", data=article)
                for article in ast.literal_eval(result)
            ]
        else:
            data_list = [Data(text=result, data={"result": result})]

        return data_list

    def fetch_content_dataframe(self) -> DataFrame:
        """将 Data 列表转换为 DataFrame。"""
        data = self.fetch_content()
        return DataFrame(data)
