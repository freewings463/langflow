"""模块名称：ASCII 图渲染

本模块基于 DVC 的 DAG ASCII 绘制逻辑改造而来，用于渲染图结构的文本视图。
原始来源：
https://github.com/iterative/dvc/blob/c5bac1c8cfdb2c0f54d52ac61ff754e6f583822a/dvc/dagascii.py

使用场景：在无图形界面环境输出图的结构概览。
主要功能包括：
- 使用 `grandalf` 计算布局
- 以 ASCII 方式绘制顶点与边

设计背景：保留核心绘制逻辑，去除无关功能以适配本项目
注意事项：仅适用于 DAG；边需要至少两个路由点
"""

import math

from grandalf.graphs import Edge as GrandalfEdge
from grandalf.graphs import Graph as GrandalfGraph
from grandalf.graphs import Vertex as GrandalfVertex
from grandalf.layouts import SugiyamaLayout
from grandalf.routing import EdgeViewer, route_with_lines

MINIMUM_EDGE_VIEW_POINTS = 2


class VertexViewer:
    """顶点视图尺寸定义。"""

    HEIGHT = 3  # 上下边框 + 文本行

    def __init__(self, name) -> None:
        # 注意：高度固定为文本行+上下边框。
        self._h = self.HEIGHT
        # 注意：宽度包含左右边框与文本。
        self._w = len(name) + 2

    @property
    def h(self):
        return self._h

    @property
    def w(self):
        return self._w


class AsciiCanvas:
    """ASCII 画布，用于绘制线条与文本。"""

    def __init__(self, cols, lines) -> None:
        if cols <= 1:
            msg = "cols must be greater than 1"
            raise ValueError(msg)
        if lines <= 1:
            msg = "lines must be greater than 1"
            raise ValueError(msg)
        self.cols = cols
        self.lines = lines
        self.canvas = [[" "] * cols for _ in range(lines)]

    def get_lines(self):
        return map("".join, self.canvas)

    def draws(self):
        return "\n".join(self.get_lines())

    def draw(self) -> None:
        """打印当前画布内容。"""
        lines = self.get_lines()
        print("\n".join(lines))  # noqa: T201

    def point(self, x, y, char) -> None:
        """在画布上绘制单点。"""
        if len(char) != 1:
            msg = "char must be a single character"
            raise ValueError(msg)
        if x < 0 or x >= self.cols:
            msg = "x is out of bounds"
            raise ValueError(msg)
        if y < 0 or y >= self.lines:
            msg = "y is out of bounds"
            raise ValueError(msg)
        self.canvas[y][x] = char

    def line(self, x0, y0, x1, y1, char) -> None:
        """在画布上绘制线段。"""
        if x0 > x1:
            x1, x0 = x0, x1
            y1, y0 = y0, y1

        dx = x1 - x0
        dy = y1 - y0

        if dx == 0 and dy == 0:
            self.point(x0, y0, char)
        elif abs(dx) >= abs(dy):
            for x in range(x0, x1 + 1):
                y = y0 + round((x - x0) * dy / float(dx)) if dx else y0
                self.point(x, y, char)
        else:
            for y in range(min(y0, y1), max(y0, y1) + 1):
                x = x0 + round((y - y0) * dx / float(dy)) if dy else x0
                self.point(x, y, char)

    def text(self, x, y, text) -> None:
        """在画布上绘制文本。"""
        for i, char in enumerate(text):
            self.point(x + i, y, char)

    def box(self, x0, y0, width, height) -> None:
        """在画布上绘制矩形框。"""
        if width <= 1:
            msg = "width must be greater than 1"
            raise ValueError(msg)
        if height <= 1:
            msg = "height must be greater than 1"
            raise ValueError(msg)
        width -= 1
        height -= 1

        for x in range(x0, x0 + width):
            self.point(x, y0, "-")
            self.point(x, y0 + height, "-")
        for y in range(y0, y0 + height):
            self.point(x0, y, "|")
            self.point(x0 + width, y, "|")
        self.point(x0, y0, "+")
        self.point(x0 + width, y0, "+")
        self.point(x0, y0 + height, "+")
        self.point(x0 + width, y0 + height, "+")


def build_sugiyama_layout(vertexes, edges):
    """构建 Sugiyama 布局并返回布局对象。"""
    vertexes = {v: GrandalfVertex(v) for v in vertexes}
    edges = [GrandalfEdge(vertexes[s], vertexes[e]) for s, e in edges]
    graph = GrandalfGraph(vertexes.values(), edges)

    for vertex in vertexes.values():
        vertex.view = VertexViewer(vertex.data)

    minw = min(v.view.w for v in vertexes.values())

    for edge in edges:
        edge.view = EdgeViewer()

    sug = SugiyamaLayout(graph.C[0])
    roots = [v for v in sug.g.sV if len(v.e_in()) == 0]
    sug.init_all(roots=roots, optimize=True)

    sug.yspace = VertexViewer.HEIGHT
    sug.xspace = minw
    sug.route_edge = route_with_lines

    sug.draw()
    return sug


def draw_graph(vertexes, edges, *, return_ascii=True):
    """构建 DAG 并输出 ASCII 图。

    契约：`vertexes` 为顶点名称列表，`edges` 为边二元组列表
    失败语义：边路由点不足时抛 `ValueError`
    """
    sug = build_sugiyama_layout(vertexes, edges)

    xlist = []
    ylist = []

    for vertex in sug.g.sV:
        xlist.extend([vertex.view.xy[0] - vertex.view.w / 2.0, vertex.view.xy[0] + vertex.view.w / 2.0])
        ylist.extend([vertex.view.xy[1], vertex.view.xy[1] + vertex.view.h])

    for edge in sug.g.sE:
        for x, y in edge.view._pts:
            xlist.append(x)
            ylist.append(y)

    minx = min(xlist)
    miny = min(ylist)
    maxx = max(xlist)
    maxy = max(ylist)

    canvas_cols = math.ceil(maxx - minx) + 1
    canvas_lines = round(maxy - miny)

    canvas = AsciiCanvas(canvas_cols, canvas_lines)

    for edge in sug.g.sE:
        if len(edge.view._pts) < MINIMUM_EDGE_VIEW_POINTS:
            msg = "edge.view._pts must have at least 2 points"
            raise ValueError(msg)
        for index in range(1, len(edge.view._pts)):
            start = edge.view._pts[index - 1]
            end = edge.view._pts[index]
            canvas.line(
                round(start[0] - minx),
                round(start[1] - miny),
                round(end[0] - minx),
                round(end[1] - miny),
                "*",
            )

    for vertex in sug.g.sV:
        x = vertex.view.xy[0] - vertex.view.w / 2.0
        y = vertex.view.xy[1]
        canvas.box(round(x - minx), round(y - miny), vertex.view.w, vertex.view.h)
        canvas.text(round(x - minx) + 1, round(y - miny) + 1, vertex.data)
    if return_ascii:
        return canvas.draws()
    canvas.draw()
    return None
