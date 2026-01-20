"""
模块名称：OpenTelemetry 指标封装

本模块封装 OpenTelemetry 指标注册与采集，提供统一的计数器/直方图/观测仪表接口。
主要功能：
- 注册并创建自定义指标
- 校验标签并写入指标数据
- 支持 Prometheus reader
设计背景：统一指标采集入口，避免散落式埋点。
注意事项：指标必须先注册且标签需满足必填约束。
"""

import threading
from collections.abc import Mapping
from enum import Enum
from typing import Any
from weakref import WeakValueDictionary

from opentelemetry import metrics
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.metrics._internal.instrument import Counter, Histogram, UpDownCounter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource

# a default OpenTelemetry meter name
langflow_meter_name = "langflow"
"""默认 OpenTelemetry meter 名称。"""

"""
指标选型指引：
- 非可加：使用异步 Gauge（ObservableGauge）报告绝对值。
- 可加且单调递增：使用异步 Counter。
- 可加但非单调：使用异步 UpDownCounter，记录增减变化。
- 可加且需要分布：使用 Histogram。
"""


class MetricType(Enum):
    """指标类型枚举。"""

    COUNTER = "counter"
    OBSERVABLE_GAUGE = "observable_gauge"
    HISTOGRAM = "histogram"
    UP_DOWN_COUNTER = "up_down_counter"


mandatory_label = True
optional_label = False


class ObservableGaugeWrapper:
    """Wrapper class for ObservableGauge.

    Since OpenTelemetry does not provide a way to set the value of an ObservableGauge,
    instead it uses a callback function to get the value, we need to create a wrapper class.
    """

    def __init__(self, name: str, description: str, unit: str):
        self._values: dict[tuple[tuple[str, str], ...], float] = {}
        self._meter = metrics.get_meter(langflow_meter_name)
        self._gauge = self._meter.create_observable_gauge(
            name=name, description=description, unit=unit, callbacks=[self._callback]
        )

    def _callback(self, _options: CallbackOptions):
        return [Observation(value, attributes=dict(labels)) for labels, value in self._values.items()]

        # return [Observation(self._value)]

    def set_value(self, value: float, labels: Mapping[str, str]) -> None:
        self._values[tuple(sorted(labels.items()))] = value


class Metric:
    """指标注册结构体。"""

    def __init__(
        self,
        name: str,
        description: str,
        metric_type: MetricType,
        labels: dict[str, bool],
        unit: str = "",
    ):
        self.name = name
        self.description = description
        self.type = metric_type
        self.unit = unit
        self.labels = labels
        self.mandatory_labels = [label for label, required in labels.items() if required]
        self.allowed_labels = list(labels.keys())

    def validate_labels(self, labels: Mapping[str, str]) -> None:
        """校验指标标签是否满足必填约束。"""
        if labels is None or len(labels) == 0:
            msg = "Labels must be provided for the metric"
            raise ValueError(msg)

        missing_labels = set(self.mandatory_labels) - set(labels.keys())
        if missing_labels:
            msg = f"Missing required labels: {missing_labels}"
            raise ValueError(msg)

    def __repr__(self) -> str:
        return f"Metric(name='{self.name}', description='{self.description}', type={self.type}, unit='{self.unit}')"


class ThreadSafeSingletonMetaUsingWeakref(type):
    """Thread-safe Singleton metaclass using WeakValueDictionary."""

    _instances: WeakValueDictionary[Any, Any] = WeakValueDictionary()
    _lock: threading.Lock = threading.Lock()

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            with cls._lock:
                if cls not in cls._instances:
                    instance = super().__call__(*args, **kwargs)
                    cls._instances[cls] = instance
        return cls._instances[cls]


class OpenTelemetry(metaclass=ThreadSafeSingletonMetaUsingWeakref):
    """OpenTelemetry 指标注册与写入入口。"""

    _metrics_registry: dict[str, Metric] = {}
    _metrics: dict[str, Counter | ObservableGaugeWrapper | Histogram | UpDownCounter] = {}
    _meter_provider: MeterProvider | None = None
    _initialized: bool = False  # Add initialization flag
    prometheus_enabled: bool = True

    def _add_metric(
        self, name: str, description: str, unit: str, metric_type: MetricType, labels: dict[str, bool]
    ) -> None:
        """注册指标元数据并写入注册表。"""
        metric = Metric(name=name, description=description, metric_type=metric_type, unit=unit, labels=labels)
        self._metrics_registry[name] = metric
        if labels is None or len(labels) == 0:
            msg = "Labels must be provided for the metric upon registration"
            raise ValueError(msg)

    def _register_metric(self) -> None:
        """注册自定义指标集合。"""
        self._add_metric(
            name="file_uploads",
            description="The uploaded file size in bytes",
            unit="bytes",
            metric_type=MetricType.OBSERVABLE_GAUGE,
            labels={"flow_id": mandatory_label},
        )
        self._add_metric(
            name="num_files_uploaded",
            description="The number of file uploaded",
            unit="",
            metric_type=MetricType.COUNTER,
            labels={"flow_id": mandatory_label},
        )

    def __init__(self, *, prometheus_enabled: bool = True):
        """初始化 OpenTelemetry 并创建指标实例。"""
        # Only initialize once
        self.prometheus_enabled = prometheus_enabled
        if OpenTelemetry._initialized:
            return

        if not self._metrics_registry:
            self._register_metric()

        if self._meter_provider is None:
            # Get existing meter provider if any
            existing_provider = metrics.get_meter_provider()

            # Check if FastAPI instrumentation is already set up
            if hasattr(existing_provider, "get_meter") and existing_provider.get_meter("http.server"):
                self._meter_provider = existing_provider
            else:
                resource = Resource.create({"service.name": "langflow"})
                metric_readers = []
                if self.prometheus_enabled:
                    metric_readers.append(PrometheusMetricReader())

                self._meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
                metrics.set_meter_provider(self._meter_provider)

        self.meter = self._meter_provider.get_meter(langflow_meter_name)

        for name, metric in self._metrics_registry.items():
            if name != metric.name:
                msg = f"Key '{name}' does not match metric name '{metric.name}'"
                raise ValueError(msg)
            if name not in self._metrics:
                self._metrics[metric.name] = self._create_metric(metric)

        OpenTelemetry._initialized = True

    def _create_metric(self, metric):
        """按指标类型创建具体 OTEL 实例。"""
        # Remove _created_instruments check
        if metric.name in self._metrics:
            return self._metrics[metric.name]

        if metric.type == MetricType.COUNTER:
            return self.meter.create_counter(
                name=metric.name,
                unit=metric.unit,
                description=metric.description,
            )
        if metric.type == MetricType.OBSERVABLE_GAUGE:
            return ObservableGaugeWrapper(
                name=metric.name,
                description=metric.description,
                unit=metric.unit,
            )
        if metric.type == MetricType.UP_DOWN_COUNTER:
            return self.meter.create_up_down_counter(
                name=metric.name,
                unit=metric.unit,
                description=metric.description,
            )
        if metric.type == MetricType.HISTOGRAM:
            return self.meter.create_histogram(
                name=metric.name,
                unit=metric.unit,
                description=metric.description,
            )
        msg = f"Unknown metric type: {metric.type}"
        raise ValueError(msg)

    def validate_labels(self, metric_name: str, labels: Mapping[str, str]) -> None:
        """校验指定指标的标签完整性。"""
        reg = self._metrics_registry.get(metric_name)
        if reg is None:
            msg = f"Metric '{metric_name}' is not registered"
            raise ValueError(msg)
        reg.validate_labels(labels)

    def increment_counter(self, metric_name: str, labels: Mapping[str, str], value: float = 1.0) -> None:
        """累加计数器指标。"""
        self.validate_labels(metric_name, labels)
        counter = self._metrics.get(metric_name)
        if isinstance(counter, Counter):
            counter.add(value, labels)
        else:
            msg = f"Metric '{metric_name}' is not a counter"
            raise TypeError(msg)

    def up_down_counter(self, metric_name: str, value: float, labels: Mapping[str, str]) -> None:
        """更新可增可减计数器指标。"""
        self.validate_labels(metric_name, labels)
        up_down_counter = self._metrics.get(metric_name)
        if isinstance(up_down_counter, UpDownCounter):
            up_down_counter.add(value, labels)
        else:
            msg = f"Metric '{metric_name}' is not an up down counter"
            raise TypeError(msg)

    def update_gauge(self, metric_name: str, value: float, labels: Mapping[str, str]) -> None:
        """更新观察型 Gauge 指标。"""
        self.validate_labels(metric_name, labels)
        gauge = self._metrics.get(metric_name)
        if isinstance(gauge, ObservableGaugeWrapper):
            gauge.set_value(value, labels)
        else:
            msg = f"Metric '{metric_name}' is not a gauge"
            raise TypeError(msg)

    def observe_histogram(self, metric_name: str, value: float, labels: Mapping[str, str]) -> None:
        """记录直方图指标样本。"""
        self.validate_labels(metric_name, labels)
        histogram = self._metrics.get(metric_name)
        if isinstance(histogram, Histogram):
            histogram.record(value, labels)
        else:
            msg = f"Metric '{metric_name}' is not a histogram"
            raise TypeError(msg)
