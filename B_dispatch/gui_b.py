"""Entry point for the enhanced B/EMS GUI.

The existing communication/dispatch GUI is kept intact in gui_b_legacy.py.
This subclass only replaces the read-only evaluation page and its refresh logic;
TCP transport, dispatch calculation, connection management and ACK handling stay inherited.
"""
from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from .evaluation import EVALUATION_WEIGHTS, EvaluationResult, SCORING_RULES, evaluate_repository
from .gui_b_legacy import MainWindow as _LegacyMainWindow


class MainWindow(_LegacyMainWindow):
    def alarm_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        l = QtWidgets.QVBoxLayout(w)
        l.setContentsMargins(22, 18, 22, 18)
        l.addWidget(self.section("报警与评价"))

        top = QtWidgets.QHBoxLayout()
        self.eval_overall = self._eval_card("综合评价", "/100")
        self.eval_ems = self._eval_card("EMS 调度评分", "/100")
        self.eval_system = self._eval_card("系统运行评分", "/100")
        self.eval_grade = self._eval_card("评价等级", "")
        for card in (self.eval_overall, self.eval_ems, self.eval_system, self.eval_grade):
            top.addWidget(card)
        l.addLayout(top)

        control = self.make_card()
        g = QtWidgets.QGridLayout(control)
        g.addWidget(QtWidgets.QLabel("评价范围"), 0, 0)
        self.eval_period = QtWidgets.QComboBox()
        self.eval_period.addItem("最近 1 分钟", "1m")
        self.eval_period.addItem("最近 5 分钟", "5m")
        self.eval_period.addItem("本次 Session", "session")
        self.eval_period.addItem("全部历史", "all")
        self.eval_period.setCurrentIndex(1)
        self.eval_period.currentIndexChanged.connect(self.refresh_evaluation)
        g.addWidget(self.eval_period, 0, 1)
        refresh = QtWidgets.QPushButton("刷新评价")
        refresh.setProperty("kind", "primary")
        refresh.clicked.connect(self.refresh_evaluation)
        g.addWidget(refresh, 0, 2)
        self.eval_hint = QtWidgets.QLabel(
            "综合评价仅由五项指标组成：供需平衡 30% · 风能利用 25% · 柴油经济性 15% · 运行约束 15% · 调度跟踪 15%"
        )
        self.eval_hint.setProperty("hint", True)
        self.eval_hint.setWordWrap(True)
        g.addWidget(self.eval_hint, 0, 3, 1, 3)
        l.addWidget(control)

        scores = self.make_card()
        sg = QtWidgets.QGridLayout(scores)
        labels = [
            ("balance", "供需平衡"),
            ("wind", "风能利用"),
            ("diesel", "柴油经济性"),
            ("constraint", "运行约束"),
            ("tracking", "调度跟踪"),
        ]
        self.eval_score_labels = {}
        self.eval_rule_combos = {}
        for i, (key, title) in enumerate(labels):
            row = (i // 3) * 2
            col = i % 3
            title_box = QtWidgets.QHBoxLayout()
            title_box.setContentsMargins(0, 0, 0, 0)
            title_label = QtWidgets.QLabel(f"{title}  ·  {int(EVALUATION_WEIGHTS[key] * 100)}%")
            rule_combo = QtWidgets.QComboBox()
            rule_combo.setMinimumWidth(190)
            rule_combo.addItem("查看评分标准")
            for rule in SCORING_RULES[key]["rules"]:
                rule_combo.addItem(rule)
            title_box.addWidget(title_label, 1)
            title_box.addWidget(rule_combo)
            title_widget = QtWidgets.QWidget()
            title_widget.setLayout(title_box)
            sg.addWidget(title_widget, row, col)

            value = QtWidgets.QLabel("-- / 100")
            value.setProperty("value", True)
            self.eval_score_labels[key] = value
            sg.addWidget(value, row + 1, col)
            self.eval_rule_combos[key] = rule_combo

        l.addWidget(scores)

        detail = self.make_card()
        dg = QtWidgets.QGridLayout(detail)
        detail_items = [
            ("samples", "评价样本"),
            ("balance_error", "平均功率不平衡"),
            ("wind_util", "风能利用率"),
            ("diesel_share", "柴油供电占比"),
            ("unserved", "平均未供电功率"),
            ("surplus", "平均过剩功率"),
            ("violations", "运行约束违反"),
            ("tracking_error", "平均目标跟踪误差"),
            ("dispatches", "调度次数"),
        ]
        self.eval_detail_labels = {}
        for i, (key, title) in enumerate(detail_items):
            dg.addWidget(QtWidgets.QLabel(title), i // 5 * 2, i % 5)
            value = QtWidgets.QLabel("--")
            value.setProperty("cardtitle", True)
            self.eval_detail_labels[key] = value
            dg.addWidget(value, i // 5 * 2 + 1, i % 5)
        l.addWidget(detail)

        self.eval_table = QtWidgets.QTableWidget(0, 5)
        self.eval_table.setHorizontalHeaderLabels(["评价项", "权重", "评分", "当前指标", "说明"])
        self.eval_table.horizontalHeader().setStretchLastSection(True)
        self.eval_table.setAlternatingRowColors(True)
        l.addWidget(self.eval_table, 1)
        return w

    def _eval_card(self, title: str, unit: str):
        card = self.make_card()
        box = QtWidgets.QVBoxLayout(card)
        box.setContentsMargins(14, 10, 14, 10)
        t = QtWidgets.QLabel(title)
        t.setProperty("cardtitle", True)
        value = QtWidgets.QLabel("--")
        value.setProperty("value", True)
        hint = QtWidgets.QLabel(unit)
        hint.setProperty("hint", True)
        box.addWidget(t)
        box.addWidget(value)
        box.addWidget(hint)
        card.value = value
        return card

    def refresh_evaluation(self) -> None:
        if not hasattr(self, "eval_overall"):
            return
        try:
            period = self.eval_period.currentData()
            result = evaluate_repository(self.repo, period or "5m")
            self._show_evaluation(result)
        except Exception as exc:
            self.log(f"调度评价计算失败：{exc}")
            self.eval_hint.setText(f"评价暂不可用：{exc}")

    def _show_evaluation(self, r: EvaluationResult) -> None:
        self.eval_overall.value.setText(f"{r.overall_score:.1f}")
        self.eval_ems.value.setText(f"{r.ems_score:.1f}")
        self.eval_system.value.setText(f"{r.system_score:.1f}")
        self.eval_grade.value.setText(r.grade)

        for key, score in [
            ("balance", r.balance_score),
            ("wind", r.wind_score),
            ("diesel", r.diesel_score),
            ("constraint", r.constraint_score),
            ("tracking", r.tracking_score),
        ]:
            self.eval_score_labels[key].setText(f"{score:.1f} / 100")

        details = {
            "samples": str(r.sample_count),
            "balance_error": f"{r.avg_balance_error_kw:.2f} kW",
            "wind_util": f"{r.wind_utilization_pct:.1f} %",
            "diesel_share": f"{r.diesel_share_pct:.1f} %",
            "unserved": f"{r.unserved_kw:.2f} kW",
            "surplus": f"{r.surplus_kw:.2f} kW",
            "violations": str(r.constraint_violations),
            "tracking_error": f"{r.tracking_error_kw:.2f} kW",
            "dispatches": str(r.dispatch_count),
        }
        for key, text in details.items():
            self.eval_detail_labels[key].setText(text)

        rows = [
            ("供需平衡", EVALUATION_WEIGHTS["balance"], r.balance_score, f"{r.avg_balance_error_kw:.2f} kW", "平均功率不平衡越小越好"),
            ("风能利用", EVALUATION_WEIGHTS["wind"], r.wind_score, f"{r.wind_utilization_pct:.1f}%", "实际风电 / 可利用风电"),
            ("柴油经济性", EVALUATION_WEIGHTS["diesel"], r.diesel_score, f"{r.diesel_share_pct:.1f}%", "柴油供电占负荷比例"),
            ("运行约束", EVALUATION_WEIGHTS["constraint"], r.constraint_score, f"{r.constraint_violations} 次", "检查风电运行边界"),
            ("调度跟踪", EVALUATION_WEIGHTS["tracking"], r.tracking_score, f"{r.tracking_error_kw:.2f} kW", "目标与实际平均偏差"),
        ]
        self.eval_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            values = [row[0], f"{row[1] * 100:.0f}%", f"{row[2]:.1f}", row[3], row[4]]
            for j, value in enumerate(values):
                self.eval_table.setItem(i, j, QtWidgets.QTableWidgetItem(str(value)))

        self.eval_hint.setText(
            f"{r.period_label}：综合评价按五项权重计算；评分标准可在每项标题右侧下拉查看。评价模块只读历史数据，不修改 dispatch，不参与 TCP。"
        )

    def refresh_alarms(self) -> None:
        super().refresh_alarms()
        self.refresh_evaluation()


def main() -> int:
    app = QtWidgets.QApplication([])
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
