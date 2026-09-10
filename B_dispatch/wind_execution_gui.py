"""Standalone read-only PyQt6 view for wind execution evaluation."""
from __future__ import annotations
from PyQt6 import QtWidgets
from .repository import EMSRepository

class WindExecutionWindow(QtWidgets.QWidget):
    def __init__(self,repo:EMSRepository):
        super().__init__();self.repo=repo;self.setWindowTitle('风机指令执行评价（小组评价规则）');self.resize(1500,700)
        layout=QtWidgets.QVBoxLayout(self)
        controls=QtWidgets.QHBoxLayout();controls.addWidget(QtWidgets.QLabel('Session'));self.session=QtWidgets.QLineEdit();self.session.setPlaceholderText('留空=全部');controls.addWidget(self.session);refresh=QtWidgets.QPushButton('刷新');refresh.clicked.connect(self.refresh);controls.addWidget(refresh);layout.addLayout(controls)
        self.table=QtWidgets.QTableWidget(0,13);self.table.setHorizontalHeaderLabels(['反馈时间 UTC','Session','Dispatch Step','Feedback Step','B Wind Enable','B Wind Target','C Enable','C Pitch Target','C Available','C Limit','A Running','A Actual','总分/结论']);self.table.horizontalHeader().setStretchLastSection(True);layout.addWidget(self.table)
        note=QtWidgets.QLabel('四项评分：启停一致性 30% · 功率跟踪 35% · 桨距响应 20% · 能力与安全约束 15%。本页只读，不发送 TCP、不修改调度。完整反馈不足时显示 insufficient_data。');note.setWordWrap(True);layout.addWidget(note)
        self.refresh()
    def refresh(self):
        try:rows=self.repo.get_wind_execution_history(session_id=self.session.text().strip() or None,limit=500)
        except Exception as exc:QtWidgets.QMessageBox.warning(self,'查询失败',str(exc));return
        self.table.setRowCount(len(rows))
        for i,r in enumerate(rows):
            vals=[r['feedback_sampled_at_utc'],r['session_id'],r['dispatch_step'],r['feedback_step'],r['b_wind_enable'],f"{r['b_wind_target_kw']:.2f}",r['c_controller_wind_enable'],f"{r['c_pitch_target_deg']:.2f}",f"{r['c_wind_available_kw']:.2f}",f"{r['c_wind_operating_limit_kw']:.2f}",r['a_wind_running'],f"{r['a_wind_actual_kw']:.2f}",('insufficient_data' if r['total_score'] is None else f"{r['total_score']:.1f} / {r['verdict']}")]
            for j,v in enumerate(vals):self.table.setItem(i,j,QtWidgets.QTableWidgetItem(str(v)))


def main()->int:
    app=QtWidgets.QApplication([]);repo=EMSRepository('data/runtime/ems.db');repo.initialize();w=WindExecutionWindow(repo);w.show();return app.exec()
if __name__=='__main__':raise SystemExit(main())
