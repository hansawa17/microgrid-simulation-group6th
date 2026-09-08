"""Qt time-series curve editor adapted from the teaching-assistant example."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
import math
from typing import Mapping, Sequence

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget


def format_sim_time(seconds: float) -> str:
    """Format relative simulation seconds without treating them as wall-clock time."""

    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_utc_time(value: str) -> str:
    """Return a compact UTC wall-clock label for a RFC 3339 timestamp."""

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(value)
    return parsed.strftime("%H:%M:%S")


class CurveEditor(QWidget):
    """Display or edit one engineering series on an explicit simulation time axis."""

    pointChanged = pyqtSignal(int, float)
    curveChanged = pyqtSignal(object)
    editingFinished = pyqtSignal()

    def __init__(
        self,
        *,
        key: str,
        name: str,
        unit: str,
        color: str,
        times_s: Sequence[float],
        values: Sequence[float],
        minimum: float = 0.0,
        maximum: float | None = None,
        editable: bool = True,
        time_labels: Sequence[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self.name = name
        self.unit = unit
        self.color = color
        self.minimum = float(minimum)
        self.maximum = 1.0
        self._times_s: list[float] = []
        self._values: list[float] = []
        self._editable = editable
        self._dragging = False
        self._last_sample: tuple[int, float] | None = None
        self._hover_sample: tuple[int, float] | None = None
        self._playhead_s: float | None = None
        self._time_labels: list[str] = []
        self.set_data(times_s, values, maximum=maximum)
        self.set_time_labels(time_labels)
        self.setMouseTracking(True)
        self.setCursor(
            Qt.CursorShape.CrossCursor if editable else Qt.CursorShape.ArrowCursor
        )
        self.setMinimumSize(420, 220)

    def sizeHint(self) -> QSize:
        return QSize(820, 280)

    def set_data(
        self,
        times_s: Sequence[float],
        values: Sequence[float],
        *,
        maximum: float | None = None,
    ) -> None:
        if len(times_s) != len(values) or not times_s:
            raise ValueError("times_s and values must have the same non-zero length")
        parsed_times = [float(value) for value in times_s]
        parsed_values = [float(value) for value in values]
        if any(not math.isfinite(value) or value < 0 for value in parsed_times):
            raise ValueError("simulation times must be finite and non-negative")
        if any(right <= left for left, right in zip(parsed_times, parsed_times[1:])):
            raise ValueError("simulation times must be strictly increasing")
        if any(not math.isfinite(value) or value < self.minimum for value in parsed_values):
            raise ValueError("curve values must be finite and within the display range")
        observed = max(parsed_values)
        display_max = float(maximum) if maximum is not None else max(1.0, observed * 1.1)
        if not math.isfinite(display_max) or display_max <= self.minimum:
            raise ValueError("maximum must be finite and greater than minimum")
        if observed > display_max:
            display_max = observed * 1.05
        self._times_s = parsed_times
        self._values = parsed_values
        self.maximum = display_max
        self._hover_sample = None
        if self._time_labels and len(self._time_labels) != len(parsed_times):
            self._time_labels = []
        self.update()

    def set_time_labels(self, labels: Sequence[str] | None) -> None:
        """Set display-only labels while keeping the numeric simulation axis."""

        if labels is None:
            self._time_labels = []
        else:
            parsed = [str(value) for value in labels]
            if len(parsed) != len(self._times_s):
                raise ValueError("time_labels must match the number of samples")
            self._time_labels = parsed
        self.update()

    def _label_at(self, index: int) -> str:
        if self._time_labels:
            return self._time_labels[index]
        return format_sim_time(self._times_s[index])

    def times_s(self) -> list[float]:
        return list(self._times_s)

    def values(self) -> list[float]:
        return list(self._values)

    def value_at(self, seconds: float) -> float:
        seconds = float(seconds)
        if seconds <= self._times_s[0]:
            return self._values[0]
        if seconds >= self._times_s[-1]:
            return self._values[-1]
        right = bisect_right(self._times_s, seconds)
        left = right - 1
        span = self._times_s[right] - self._times_s[left]
        ratio = (seconds - self._times_s[left]) / span
        return self._values[left] + ratio * (self._values[right] - self._values[left])

    def set_playhead(self, seconds: float | None) -> None:
        self._playhead_s = seconds
        self.update()

    def plot_rect(self) -> QRectF:
        return QRectF(
            72.0,
            38.0,
            max(1.0, self.width() - 94.0),
            max(1.0, self.height() - 82.0),
        )

    def _time_span(self) -> float:
        return max(self._times_s[-1] - self._times_s[0], 1.0)

    def _point(self, index: int, value: float) -> QPointF:
        rect = self.plot_rect()
        x_ratio = (self._times_s[index] - self._times_s[0]) / self._time_span()
        y_ratio = (value - self.minimum) / (self.maximum - self.minimum)
        return QPointF(
            rect.left() + rect.width() * x_ratio,
            rect.bottom() - rect.height() * y_ratio,
        )

    def position_to_sample(self, position: QPointF) -> tuple[int, float] | None:
        rect = self.plot_rect()
        if not rect.contains(position):
            return None
        x_ratio = (position.x() - rect.left()) / rect.width()
        target_time = self._times_s[0] + x_ratio * self._time_span()
        right = bisect_left(self._times_s, target_time)
        if right <= 0:
            index = 0
        elif right >= len(self._times_s):
            index = len(self._times_s) - 1
        else:
            index = min(
                (right - 1, right),
                key=lambda item: abs(self._times_s[item] - target_time),
            )
        y_ratio = (position.y() - rect.top()) / rect.height()
        value = self.maximum - y_ratio * (self.maximum - self.minimum)
        return index, min(max(value, self.minimum), self.maximum)

    def _apply_position(self, position: QPointF) -> None:
        sample = self.position_to_sample(position)
        if sample is None:
            return
        index, value = sample
        if self._last_sample is None or self._last_sample[0] == index:
            changed = [(index, value)]
        else:
            start_index, start_value = self._last_sample
            distance = abs(index - start_index)
            direction = 1 if index > start_index else -1
            changed = []
            for offset in range(distance + 1):
                ratio = offset / distance
                point_index = start_index + direction * offset
                point_value = start_value + (value - start_value) * ratio
                changed.append((point_index, point_value))
        for point_index, point_value in changed:
            self._values[point_index] = point_value
            self.pointChanged.emit(point_index, point_value)
        self._last_sample = (index, value)
        self._hover_sample = (index, self._values[index])
        self.curveChanged.emit(self.values())
        self.update()

    def mousePressEvent(self, event) -> None:
        if (
            self._editable
            and event.button() == Qt.MouseButton.LeftButton
            and self.position_to_sample(event.position()) is not None
        ):
            self._dragging = True
            self._last_sample = None
            self._apply_position(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._apply_position(event.position())
            event.accept()
            return
        sample = self.position_to_sample(event.position())
        self._hover_sample = None if sample is None else (sample[0], self._values[sample[0]])
        self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._apply_position(event.position())
            self._dragging = False
            self._last_sample = None
            self.editingFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._dragging:
            self._hover_sample = None
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        rect = self.plot_rect()

        painter.setPen(QColor("#26364a"))
        painter.drawText(12, 23, f"{self.name}  [{self.unit}]")
        painter.setPen(QPen(QColor("#aab5c3"), 1))
        painter.drawRoundedRect(rect, 4, 4)

        for step in range(5):
            ratio = step / 4
            y = rect.top() + rect.height() * ratio
            x = rect.left() + rect.width() * ratio
            painter.setPen(QPen(QColor("#e8edf3"), 1))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            value = self.maximum - ratio * (self.maximum - self.minimum)
            painter.setPen(QColor("#526275"))
            painter.drawText(
                QRectF(4, y - 10, 62, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{value:.1f}",
            )
            seconds = self._times_s[0] + ratio * self._time_span()
            label_index = min(
                range(len(self._times_s)),
                key=lambda index: abs(self._times_s[index] - seconds),
            )
            painter.drawText(
                QRectF(x - 42, rect.bottom() + 7, 84, 20),
                Qt.AlignmentFlag.AlignCenter,
                self._label_at(label_index),
            )

        path = QPainterPath()
        path.moveTo(self._point(0, self._values[0]))
        for index, value in enumerate(self._values[1:], start=1):
            path.lineTo(self._point(index, value))
        painter.save()
        painter.setClipRect(rect)
        painter.setPen(QPen(QColor(self.color), 2.2))
        painter.drawPath(path)
        painter.restore()

        if self._playhead_s is not None:
            seconds = min(max(self._playhead_s, self._times_s[0]), self._times_s[-1])
            x = rect.left() + rect.width() * (
                (seconds - self._times_s[0]) / self._time_span()
            )
            painter.setPen(QPen(QColor("#d97706"), 1.2, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

        if self._hover_sample is not None:
            index, value = self._hover_sample
            point = self._point(index, value)
            painter.setBrush(QColor(self.color))
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawEllipse(point, 5, 5)
            text = f"{self._label_at(index)}  {value:.3f} {self.unit}"
            box = QRectF(
                min(point.x() + 10, self.width() - 190),
                max(point.y() - 34, 6),
                180,
                26,
            )
            painter.setBrush(QColor(255, 255, 255, 240))
            painter.setPen(QPen(QColor("#aab5c3"), 1))
            painter.drawRoundedRect(box, 4, 4)
            painter.setPen(QColor("#26364a"))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


class TimeSeriesChart(QWidget):
    """Compact, read-only multi-series chart with a real UTC x-axis."""

    def __init__(
        self,
        *,
        title: str,
        unit: str,
        colors: Mapping[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.title = title
        self.unit = unit
        self.colors = dict(colors)
        self._timestamps: list[str] = []
        self._x_values: list[float] = []
        self._series: dict[str, list[float]] = {}
        self.setMinimumSize(380, 230)
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:
        return QSize(650, 270)

    @staticmethod
    def _timestamp_seconds(value: str, fallback: int) -> float:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OSError):
            return float(fallback)

    def set_series(
        self,
        timestamps: Sequence[str],
        series: Mapping[str, Sequence[float]],
    ) -> None:
        parsed_timestamps = [str(value) for value in timestamps]
        parsed_series: dict[str, list[float]] = {}
        for name, values in series.items():
            parsed = [float(value) for value in values]
            if len(parsed) != len(parsed_timestamps):
                raise ValueError("all chart series must match timestamps")
            if any(not math.isfinite(value) for value in parsed):
                raise ValueError("chart values must be finite")
            parsed_series[str(name)] = parsed
        self._timestamps = parsed_timestamps
        self._x_values = [
            self._timestamp_seconds(value, index)
            for index, value in enumerate(parsed_timestamps)
        ]
        self._series = parsed_series
        self.update()

    def clear(self) -> None:
        self._timestamps = []
        self._x_values = []
        self._series = {}
        self.update()

    def plot_rect(self) -> QRectF:
        return QRectF(
            66.0,
            54.0,
            max(1.0, self.width() - 84.0),
            max(1.0, self.height() - 92.0),
        )

    def _bounds(self) -> tuple[float, float, float, float]:
        x_min = min(self._x_values)
        x_max = max(self._x_values)
        if x_max <= x_min:
            x_max = x_min + 1.0
        values = [value for items in self._series.values() for value in items]
        y_min = min(0.0, min(values, default=0.0))
        y_max = max(1.0, max(values, default=1.0))
        if y_min < 0:
            padding = max((y_max - y_min) * 0.08, 1.0)
            y_min -= padding
            y_max += padding
        else:
            y_max *= 1.1
        return x_min, x_max, y_min, y_max

    @staticmethod
    def _point(
        rect: QRectF,
        x: float,
        y: float,
        bounds: tuple[float, float, float, float],
    ) -> QPointF:
        x_min, x_max, y_min, y_max = bounds
        return QPointF(
            rect.left() + rect.width() * ((x - x_min) / (x_max - x_min)),
            rect.bottom() - rect.height() * ((y - y_min) / (y_max - y_min)),
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.setPen(QColor("#26364a"))
        painter.drawText(14, 24, self.title)
        painter.setPen(QColor("#8b9caf"))
        painter.drawText(
            QRectF(self.width() - 240, 8, 225, 24),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"sampled_at_utc  ·  {self.unit}",
        )

        rect = self.plot_rect()
        painter.setPen(QPen(QColor("#aab5c3"), 1))
        painter.drawRoundedRect(rect, 4, 4)
        if not self._timestamps or not self._series:
            painter.setPen(QColor("#8b9caf"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "等待 UTC 遥测数据")
            return

        bounds = self._bounds()
        x_min, x_max, y_min, y_max = bounds
        for step in range(5):
            ratio = step / 4
            y = rect.top() + rect.height() * ratio
            x = rect.left() + rect.width() * ratio
            painter.setPen(QPen(QColor("#e8edf3"), 1))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.setPen(QColor("#526275"))
            value = y_max - ratio * (y_max - y_min)
            painter.drawText(
                QRectF(2, y - 10, 58, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{value:.1f}",
            )
            tick_time = x_min + ratio * (x_max - x_min)
            painter.drawText(
                QRectF(x - 40, rect.bottom() + 7, 80, 20),
                Qt.AlignmentFlag.AlignCenter,
                datetime.fromtimestamp(tick_time, timezone.utc).strftime("%H:%M:%S"),
            )

        painter.save()
        painter.setClipRect(rect)
        for name, values in self._series.items():
            if not values:
                continue
            path = QPainterPath()
            path.moveTo(self._point(rect, self._x_values[0], values[0], bounds))
            for x, value in zip(self._x_values[1:], values[1:]):
                path.lineTo(self._point(rect, x, value, bounds))
            painter.setPen(QPen(QColor(self.colors.get(name, "#2f6fd6")), 2.0))
            painter.drawPath(path)
        painter.restore()

        legend_x = rect.left() + 6
        for name in self._series:
            painter.setPen(QPen(QColor(self.colors.get(name, "#2f6fd6")), 2.5))
            painter.drawLine(QPointF(legend_x, 42), QPointF(legend_x + 16, 42))
            painter.setPen(QColor("#526275"))
            width = max(68, painter.fontMetrics().horizontalAdvance(name) + 28)
            painter.drawText(QRectF(legend_x + 21, 31, width, 22), name)
            legend_x += width + 18
