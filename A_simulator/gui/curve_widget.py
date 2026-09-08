"""Qt time-series curve editor adapted from the teaching-assistant example."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import math
from typing import Sequence

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
        self.set_data(times_s, values, maximum=maximum)
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
        self.update()

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
            painter.drawText(
                QRectF(x - 34, rect.bottom() + 7, 68, 20),
                Qt.AlignmentFlag.AlignCenter,
                format_sim_time(seconds),
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
            text = f"{format_sim_time(self._times_s[index])}  {value:.3f} {self.unit}"
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

