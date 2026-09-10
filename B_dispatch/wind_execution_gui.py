"""Standalone read-only PyQt6 view for wind execution evaluation."""
from __future__ import annotations
from PyQt6 import QtCore,QtWidgets
from .repository import EMSRepository

class WindExecutionWindow(QtWidgets.QWidget):
 def __init__(self,repo:EMSRepository):
  super().__init__();self.repo=repo;self.setWindowTitle('风机指令执行评价（小组评价规则）');self.resize(1800,760);layout=QtWidgets.QVBoxLayout(self)
  controls=QtWidgets.QHBoxLayout();controls.addWidget(QtWidgets.QLabel('Session'));self.session=QtWidgets.QLineEdit();self.session.setPlaceholderText('留空=全部');controls.addWidget(self.session);controls.addWidget(QtWidgets.QLabel('起始 UTC'));self.start=QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTimeUtc().addSecs(-3600));self.start.setDisplayFormat('yyyy-MM-dd HH:mm:ss');self.start.setCalendarPopup(True);controls.addWidget(self.start);controls.addWidget(QtWidgets.QLabel('结束 UTC'));self.end=QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTimeUtc());self.end.setDisplayFormat('yyyy-MM-dd HH:mm:ss');self.end.setCalendarPopup(True);controls.addWidget(self.end);refresh=QtWidgets.QPushButton('刷新');refresh.clicked.connect(self.refresh);controls.addWidget(refresh);layout.addLayout(controls)
  self.table=QtWidgets.QTableWidget(0,20);self.table.setHorizontalHeaderLabels(['反馈时间 UTC','Session','Dispatch Step','Feedback Step','Action Seq','B Wind Enable','B Target kW','C Enable','C Pitch Target°','C Available kW','C Limit kW','A Running','A Actual kW','A Pitch°','Fault','时延 s','启停 30%','功率 35%','桨距 20%','能力 15% / 总分 / 结论']);self.table.horizontalHeader().setStretchLastSection(True);self.table.setAlternatingRowColors(True);layout.addWidget(self.table)
  note=QtWidgets.QLabel('小组评价规则（非课程原公式）：启停一致性 30% · 功率跟踪 35% · 桨距响应 20% · 能力与安全约束 15%。缺少完整 B→C→A 反馈时不生成伪造总分，记录状态为 insufficient_data。此页面只读，不发送 TCP。');note.setWordWrap(True);layout.addWidget(note);self.refresh()
 def refresh(self):
  s=self.session.text().strip() or None;start=self.start.dateTime().toUTC().toString("yyyy-MM-dd'T'HH:mm:ss.zzz'Z'");end=self.end.dateTime().toUTC().toString("yyyy-MM-dd'T'HH:mm:ss.zzz'Z'")
  if end<start:QtWidgets.QMessageBox.warning(self,'查询失败','结束时间不能早于起始时间');return
  try:rows=self.repo.get_wind_execution_history(session_id=s,start_utc=start,end_utc=end,limit=500)
  except Exception as exc:QtWidgets.QMessageBox.warning(self,'查询失败',str(exc));return
  self.table.setRowCount(len(rows))
  for i,r in enumerate(rows):
   score='insufficient_data' if r['total_score'] is None else f"{r['capability_safety_score']:.1f} / {r['total_score']:.1f} / {r['verdict']}";vals=[r['feedback_sampled_at_utc'],r['session_id'],r['dispatch_step'],r['feedback_step'],r['c_wind_action_seq'] or '--',r['b_wind_enable'],f"{r['b_wind_target_kw']:.2f}",r['c_controller_wind_enable'],f"{r['c_pitch_target_deg']:.2f}",f"{r['c_wind_available_kw']:.2f}",f"{r['c_wind_operating_limit_kw']:.2f}",r['a_wind_running'],f"{r['a_wind_actual_kw']:.2f}",f"{r['a_pitch_actual_deg']:.2f}",r['a_fault'],'--' if r['response_latency_s'] is None else f"{r['response_latency_s']:.3f}",'--' if r['start_stop_score'] is None else f"{r['start_stop_score']:.1f}",'--' if r['power_tracking_score'] is None else f"{r['power_tracking_score']:.1f}",'--' if r['pitch_response_score'] is None else f"{r['pitch_response_score']:.1f}",score]
   for j,v in enumerate(vals):self.table.setItem(i,j,QtWidgets.QTableWidgetItem(str(v)))

def main()->int:
 app=QtWidgets.QApplication([]);repo=EMSRepository('data/runtime/ems.db');repo.initialize();w=WindExecutionWindow(repo);w.show();return app.exec()
if __name__=='__main__':raise SystemExit(main())
