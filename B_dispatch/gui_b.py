"""B/EMS operator GUI with direct TCP ownership, mirroring A/C communication.

The window owns the B->A TCP client (like C owns its serial/UART link) and runs
periodic closed-loop dispatch with a Qt timer. Received state and dispatch
history are still persisted to ``ems.db`` for evaluation and traceability.
"""
from __future__ import annotations

import csv
import os
import socket
import time
from dataclasses import replace
from datetime import datetime, timezone

from PyQt6 import QtCore, QtWidgets

from .evaluation import EVALUATION_WEIGHTS, EvaluationResult, SCORING_RULES, evaluate_repository
from .gui_b_legacy import MainWindow as _LegacyMainWindow, TrendChart
from .models import DispatchConfig, DispatchResult, GridState
from .tcpB import Ack, DispatchDeliveryUnknown, EMSTcpClient, ProtocolError, parse_endpoint
from .wind_execution import evaluate_pair, find_feedback


class MainWindow(_LegacyMainWindow):
    """Operator window that owns the B->A TCP client and the dispatch timer."""

    def __init__(self) -> None:
        self._communication_enabled = False
        self._initialized_repo_identity: int | None = None
        self._last_db_state_key: tuple[str, int, str] | None = None
        self._last_diag_text = ""
        self._last_sent_command: tuple[float, float, bool, bool] | None = None
        self._last_decision_monotonic = 0.0
        self._pending_dispatch_record: dict[int, object] = {}
        super().__init__()
        self.client = None
        self._ensure_db()
        comm = self.repo.get_communication_config()
        self._communication_enabled = bool(comm["enabled"])
        self.host.setText(str(comm["host"])); self.port.setValue(int(comm["port"]))
        try:
            self.auto_dispatch = bool(self.repo.get_runtime_config()["closed_loop"])
        except Exception:
            self.auto_dispatch = True
        self.auto_box.blockSignals(True); self.auto_box.setChecked(self.auto_dispatch); self.auto_box.blockSignals(False)
        self.safe_label.setText("正式模式：GUI 直连 A；闭环下每 1 s 检查 YK/YT 变化，仅变化时下发。")
        self.repo.heartbeat("B_GUI", pid=os.getpid(), state="RUNNING", detail="direct-socket operator GUI")
        self.poll_socket()
        self.log("GUI 启动：本窗口直连 A（TCP client），点“连接”后建立会话")

    def monitor_page(self) -> QtWidgets.QWidget:
        page = super().monitor_page(); layout = page.layout(); card = self.make_card(); box = QtWidgets.QVBoxLayout(card)
        title = QtWidgets.QLabel("RTU / SCADA 四遥实时点表"); title.setProperty("cardtitle", True); box.addWidget(title)
        self.scada_table = QtWidgets.QTableWidget(0, 6); self.scada_table.setHorizontalHeaderLabels(["类别", "点名", "值", "单位", "数据时刻", "说明"])
        self.scada_table.setAlternatingRowColors(True); self.scada_table.horizontalHeader().setStretchLastSection(True); self.scada_table.setMinimumHeight(230); box.addWidget(self.scada_table)
        layout.insertWidget(max(0, layout.count() - 1), card); return page

    def refresh_state_views(self) -> None:
        super().refresh_state_views(); self._refresh_scada_table()

    def set_state_snapshot(self, state: GridState) -> None:
        """Apply A-owned constraints before calculating from the fresh state."""
        parameters_changed = False
        if getattr(state, "parameters", None):
            try:
                self._ensure_db()
                parameters_changed = self.repo.apply_remote_parameters(state.parameters)
                if parameters_changed:
                    self._reload_physical_widgets()
                    # A parameter changes invalidate any decision calculated
                    # with the previous diesel/wind bounds.  The next 1 s
                    # runtime tick must recalculate even when the normal 5 s
                    # dispatch period has not elapsed yet.
                    self.last_decision = None
                    self._last_decision_monotonic = 0.0
            except Exception as exc:
                self.log(f"参数同步失败：{exc}")
        fresh_state = replace(state, received_age_s=0.0)
        super().set_state_snapshot(fresh_state)
        if parameters_changed:
            self.calculate_current()
            physical = self.repo.get_physical_parameters()
            self.log(
                "A 参数已应用到调度约束："
                f"柴油 {float(physical['diesel_min_kw']):g}–"
                f"{float(physical['diesel_max_kw']):g} kW；下一调度检查生效"
            )
        self._persist_wind_execution()

    def _reload_physical_widgets(self) -> None:
        try:
            physical = self.repo.get_physical_parameters()
        except Exception:
            return
        self._physical = {k: float(v) for k, v in dict(physical).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        self._sync_physical_to_params()
        self.core = self.core.__class__(self.config())
        for group in ("a_physical_widgets", "c_physical_widgets"):
            widgets = getattr(self, group, None)
            if not widgets:
                continue
            for key, widget in widgets.items():
                if key in self._physical:
                    widget.setValue(self._physical[key])

    def _sync_physical_to_params(self) -> None:
        self.params['diesel_max_kw'] = float(self._physical.get('diesel_max_kw', 120.0))
        self.params['wind_max_kw'] = float(self._physical.get('wind_rated_kw', 100.0))
        self.params['wind_min_kw'] = 0.0

    def _refresh_scada_table(self) -> None:
        if self.state is None or not hasattr(self, "scada_table"): return
        state = self.state; sampled = state.sampled_at_utc or "--"
        rows = [
            ("YC 遥测", "wind_speed_mps", f"{state.wind_speed_mps:.3f}", "m/s", sampled, "A 环境测量"),
            ("YC 遥测", "load_power_kw", f"{state.load_power_kw:.3f}", "kW", sampled, "A 负荷测量"),
            ("YC 遥测", "wind_available_kw", f"{state.wind_available_kw:.3f}", "kW", sampled, "C 能力经 A 转发"),
            ("YC 遥测", "wind_operating_limit_kw", f"{state.wind_operating_limit_kw:.3f}", "kW", sampled, "C 运行上限经 A 转发"),
            ("YC 遥测", "wind_actual_kw", f"{state.wind_actual_kw:.3f}", "kW", sampled, "A 仿真实际出力"),
            ("YC 遥测", "diesel_actual_kw", f"{state.diesel_actual_kw:.3f}", "kW", sampled, "A 仿真实际出力"),
            ("YC 遥测", "power_imbalance_kw", f"{state.power_imbalance_kw:+.3f}", "kW", sampled, "正缺电，负过剩"),
            ("YX 遥信", "wind_running", "合" if state.wind_running else "分", "bool", sampled, "风机运行状态"),
            ("YX 遥信", "diesel_running", "合" if state.diesel_running else "分", "bool", sampled, "柴发运行状态"),
            ("YX 遥信", "fault", "告警" if state.fault else "正常", "bool", sampled, "C 保护/故障状态"),
            ("YT 遥调", "wind_target_kw", f"{state.wind_target_kw:.3f}", "kW", sampled, "A 当前保存的 B 功率目标"),
            ("YT 遥调", "diesel_target_kw", f"{state.diesel_target_kw:.3f}", "kW", sampled, "A 当前保存的 B 功率目标"),
        ]
        try:
            with self.repo.connection() as conn: command = conn.execute("SELECT wind_enable,diesel_enable,status,created_at_utc FROM dispatch_outbox ORDER BY id DESC LIMIT 1").fetchone()
            if command is not None:
                command_time = str(command["created_at_utc"]); status = str(command["status"])
                rows.extend([("YK 遥控", "wind_enable", "合" if command["wind_enable"] else "分", "bool", command_time, f"本地命令状态={status}"), ("YK 遥控", "diesel_enable", "合" if command["diesel_enable"] else "分", "bool", command_time, f"本地命令状态={status}")])
            else:
                rows.extend([("YK 遥控", "wind_enable", "--", "bool", "--", "尚无本地命令"), ("YK 遥控", "diesel_enable", "--", "bool", "--", "尚无本地命令")])
        except Exception:
            rows.extend([("YK 遥控", "wind_enable", "--", "bool", "--", "命令队列暂不可读"), ("YK 遥控", "diesel_enable", "--", "bool", "--", "命令队列暂不可读")])
        self.scada_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row): self.scada_table.setItem(row_index, column_index, QtWidgets.QTableWidgetItem(str(value)))

    def config(self) -> DispatchConfig:
        ph = getattr(self, "_physical", {}) or {}
        return DispatchConfig(wind_min_kw=0.0, wind_max_kw=float(ph.get("wind_rated_kw", 100.0)), diesel_max_kw=float(ph.get("diesel_max_kw", 120.0)), reserve_kw=self.params['reserve_kw'], diesel_min_kw=float(ph.get("diesel_min_kw", 20.0)), max_state_age_s=self.params['max_age_s'], c_has_control_priority=True)

    def _ensure_db(self) -> None:
        """Initialize/migrate each repository object once without resetting rows."""
        identity = id(self.repo)
        if self._initialized_repo_identity == identity:
            return
        self.repo.initialize()
        self._initialized_repo_identity = identity

    def params_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w); l.setContentsMargins(22,18,22,18); l.addWidget(self.section('参数设置'))
        b_card = self.make_card(); bf = QtWidgets.QFormLayout(b_card)
        self.p_poll = QtWidgets.QDoubleSpinBox(); self.p_poll.setRange(0.1,60); self.p_poll.setDecimals(2); self.p_poll.setSuffix(' s')
        self.p_dispatch = QtWidgets.QDoubleSpinBox(); self.p_dispatch.setRange(0.1,300); self.p_dispatch.setDecimals(2); self.p_dispatch.setSuffix(' s')
        self.p_reserve = QtWidgets.QDoubleSpinBox(); self.p_reserve.setRange(0.1,1000); self.p_reserve.setDecimals(2); self.p_reserve.setSuffix(' kW')
        self.p_age = QtWidgets.QDoubleSpinBox(); self.p_age.setRange(0.1,60); self.p_age.setDecimals(2); self.p_age.setSuffix(' s')
        self.p_closed = QtWidgets.QCheckBox('B closed loop（默认开启）'); self.p_closed.setChecked(True)
        for label, widget in [('B：采集周期',self.p_poll),('B：调度周期',self.p_dispatch),('B：柴发余量 reserve',self.p_reserve),('B：最大状态年龄',self.p_age),('B：运行模式',self.p_closed)]: bf.addRow(label,widget)
        l.addWidget(b_card)
        a_card = self.make_card(); af = QtWidgets.QFormLayout(a_card); self.a_physical_widgets = {}
        for key,title,suffix in [('diesel_min_kw','柴油最小功率',' kW'),('diesel_max_kw','柴油最大功率',' kW')]:
            spin=QtWidgets.QDoubleSpinBox(); spin.setRange(0,1e9); spin.setDecimals(2); spin.setSuffix(suffix); spin.setEnabled(False); self.a_physical_widgets[key]=spin; af.addRow(title,spin)
        note_a=QtWidgets.QLabel('柴油最小/最大功率由 A 维护，B 只读接收。'); note_a.setProperty('hint',True); note_a.setWordWrap(True); af.addRow(note_a)
        l.addWidget(a_card)
        c_card = self.make_card(); cf = QtWidgets.QFormLayout(c_card); self.c_physical_widgets = {}
        for key,title,suffix in [('wind_rated_kw','风机额定功率',' kW'),('wind_cut_in_mps','切入风速',' m/s'),('wind_rated_speed_mps','额定风速',' m/s'),('wind_cut_out_mps','切出风速',' m/s'),('pitch_min_deg','桨距下限',' °'),('pitch_max_deg','桨距上限',' °')]:
            spin=QtWidgets.QDoubleSpinBox(); spin.setRange(-1e9,1e9); spin.setDecimals(2); spin.setSuffix(suffix); spin.setEnabled(False); self.c_physical_widgets[key]=spin; cf.addRow(title,spin)
        note_c=QtWidgets.QLabel('风机额定功率、切入/额定/切出风速与桨距角由 C 维护，经 A 转发，B 只读显示。'); note_c.setProperty('hint',True); note_c.setWordWrap(True); cf.addRow(note_c)
        l.addWidget(c_card)
        note=QtWidgets.QLabel('B 只能修改采集周期、调度周期和柴发余量；A/C 参数随 A/C 实时修改自动刷新。'); note.setProperty('hint',True); note.setWordWrap(True); l.addWidget(note)
        row=QtWidgets.QHBoxLayout(); load=QtWidgets.QPushButton('从 ems.db 读取'); load.setProperty('kind','secondary'); load.clicked.connect(self.load_db_config); save=QtWidgets.QPushButton('保存参数'); save.setProperty('kind','primary'); save.clicked.connect(self.save_params); row.addWidget(load); row.addWidget(save); row.addStretch(); l.addLayout(row); l.addStretch(); return w

    def load_db_config(self) -> None:
        self._physical = {}
        try:
            try: p=self.repo.get_parameters(); r=self.repo.get_runtime_config(); physical=self.repo.get_physical_parameters()
            except Exception: self.repo.initialize(); p=self.repo.get_parameters(); r=self.repo.get_runtime_config(); physical=self.repo.get_physical_parameters()
            self.params.update(reserve_kw=float(p['reserve_kw']),max_age_s=float(r['max_state_age_s']),poll_period_s=float(r['poll_period_s']),dispatch_period_s=float(r['dispatch_period_s']),closed_loop=bool(r['closed_loop']))
            self._physical={k:float(v) for k,v in dict(physical).items() if isinstance(v,(int,float)) and not isinstance(v,bool)}
            self._sync_physical_to_params()
        except Exception as exc: self.log(f'ems.db 尚未有完整参数，使用 GUI 默认值：{exc}')
        for group in ('a_physical_widgets','c_physical_widgets'):
            widgets=getattr(self,group,None)
            if not widgets: continue
            for key,widget in widgets.items():
                if key in self._physical: widget.setValue(self._physical[key])
        if hasattr(self,'p_poll'):
            self.p_poll.setValue(self.params['poll_period_s']); self.p_dispatch.setValue(self.params['dispatch_period_s']); self.p_reserve.setValue(self.params['reserve_kw']); self.p_age.setValue(self.params['max_age_s']); self.p_closed.setChecked(self.params['closed_loop'])
        self.core=self.core.__class__(self.config())
        if hasattr(self,'runtime_timer'): self.set_runtime_timer()

    def save_params(self) -> None:
        reserve=self.p_reserve.value(); poll=self.p_poll.value(); dispatch=self.p_dispatch.value(); max_age=self.p_age.value(); closed=self.p_closed.isChecked()
        if reserve<=0: QtWidgets.QMessageBox.warning(self,'参数错误','柴发余量必须大于 0。'); return
        try:
            self._ensure_db(); self.repo.set_reserve(reserve); self.repo.set_runtime_config(poll_period_s=poll,dispatch_period_s=dispatch,closed_loop=closed,max_state_age_s=max_age)
            self.params.update(reserve_kw=reserve,poll_period_s=poll,dispatch_period_s=dispatch,max_age_s=max_age,closed_loop=closed); self.core=self.core.__class__(self.config()); self.set_runtime_timer(); self.log('B 参数已写入 ems.db（仅采集/调度周期与柴发余量）'); self.statusBar().showMessage('B EMS 参数已更新')
        except Exception as exc: self.log(f'参数写库失败：{exc}'); QtWidgets.QMessageBox.warning(self,'参数写库失败',str(exc))

    def history_page(self) -> QtWidgets.QWidget:
        w=QtWidgets.QWidget(); l=QtWidgets.QVBoxLayout(w); l.setContentsMargins(22,18,22,18); l.addWidget(self.section('历史数据'))
        bar=self.make_card(); g=QtWidgets.QGridLayout(bar); g.addWidget(QtWidgets.QLabel('Session'),0,0); self.h_session=QtWidgets.QLineEdit(); g.addWidget(self.h_session,0,1)
        now=QtCore.QDateTime.currentDateTimeUtc(); self.h_start=QtWidgets.QDateTimeEdit(now.addSecs(-3600)); self.h_end=QtWidgets.QDateTimeEdit(now)
        for widget in (self.h_start,self.h_end): widget.setDisplayFormat('yyyy-MM-dd HH:mm:ss'); widget.setCalendarPopup(True)
        g.addWidget(QtWidgets.QLabel('起始 UTC'),0,2); g.addWidget(self.h_start,0,3); g.addWidget(QtWidgets.QLabel('结束 UTC'),0,4); g.addWidget(self.h_end,0,5); g.addWidget(QtWidgets.QLabel('最多行数'),1,0); self.h_limit=QtWidgets.QSpinBox(); self.h_limit.setRange(10,5000); self.h_limit.setValue(200); g.addWidget(self.h_limit,1,1)
        q=QtWidgets.QPushButton('查询'); q.setProperty('kind','primary'); q.clicked.connect(self.refresh_history); ex=QtWidgets.QPushButton('导出筛选结果'); ex.setProperty('kind','secondary'); ex.clicked.connect(self.export_history); g.addWidget(q,1,4); g.addWidget(ex,1,5); l.addWidget(bar)
        chart_card=self.make_card(); chart_layout=QtWidgets.QVBoxLayout(chart_card); self.history_chart=TrendChart(['Load','Wind Target','Wind Actual','Diesel Target','Diesel Actual']); self.history_chart.setMinimumHeight(230); chart_layout.addWidget(self.history_chart); l.addWidget(chart_card)
        tabs=QtWidgets.QTabWidget(); self.history=QtWidgets.QTableWidget(0,10); self.history.setHorizontalHeaderLabels(['时间','Session','Step','Load','Wind Avail','Wind Limit','Wind Target','Wind Actual','Diesel Target','Diesel Actual']); self.history.setAlternatingRowColors(True); self.history.horizontalHeader().setStretchLastSection(True); tabs.addTab(self.history,'SCADA 状态历史'); self.dispatch_history=QtWidgets.QTableWidget(0,10); self.dispatch_history.setHorizontalHeaderLabels(['创建时间','Step','Seq','Wind Target','Diesel Target','Status','ACK','ACK Reason','后续 Wind Actual','后续 Diesel Actual']); self.dispatch_history.setAlternatingRowColors(True); self.dispatch_history.horizontalHeader().setStretchLastSection(True); tabs.addTab(self.dispatch_history,'调度命令与反馈'); self.log_history=QtWidgets.QTableWidget(0,4); self.log_history.setHorizontalHeaderLabels(['时间','Level','Event Type','Message']); self.log_history.setAlternatingRowColors(True); self.log_history.horizontalHeader().setStretchLastSection(True); tabs.addTab(self.log_history,'运行日志'); l.addWidget(tabs,1); return w

    def _history_filter(self, time_column: str) -> tuple[str,list[object]]:
        start=self.h_start.dateTime().toUTC().toString("yyyy-MM-dd'T'HH:mm:ss.zzz'Z'"); end=self.h_end.dateTime().toUTC().toString("yyyy-MM-dd'T'HH:mm:ss.zzz'Z'")
        if end < start: raise ValueError('历史查询结束时间不能早于起始时间')
        clauses=[f'{time_column} BETWEEN ? AND ?']; args: list[object]=[start,end]; session=self.h_session.text().strip(); session_column=(time_column.split('.',1)[0]+'.session_id') if '.' in time_column else 'session_id'
        if session: clauses.append(f'{session_column}=?'); args.append(session)
        return ' WHERE '+' AND '.join(clauses),args

    def refresh_history(self) -> None:
        try:
            self._ensure_db(); limit=int(self.h_limit.value()); state_where,state_args=self._history_filter('received_at_utc'); cmd_where,cmd_args=self._history_filter('c.created_at_utc'); log_where,log_args=self._history_filter('created_at_utc')
            with self.repo.connection() as conn:
                state_rows=conn.execute('SELECT received_at_utc,session_id,step,load_power_kw,wind_available_kw,wind_operating_limit_kw,wind_target_kw,wind_actual_kw,diesel_target_kw,diesel_actual_kw FROM state_history'+state_where+' ORDER BY id DESC LIMIT ?',state_args+[limit]).fetchall()
                cmd_rows=conn.execute('''SELECT c.created_at_utc,c.step,c.seq,c.wind_target_kw,c.diesel_target_kw,c.status,c.ack_accepted,c.ack_reason,
                    (SELECT h.wind_actual_kw FROM state_history h WHERE h.session_id=c.session_id AND h.step>c.step ORDER BY h.step,h.id LIMIT 1),
                    (SELECT h.diesel_actual_kw FROM state_history h WHERE h.session_id=c.session_id AND h.step>c.step ORDER BY h.step,h.id LIMIT 1)
                    FROM dispatch_commands c'''+cmd_where+' ORDER BY c.id DESC LIMIT ?',cmd_args+[limit]).fetchall()
                log_rows=conn.execute('SELECT created_at_utc,level,event_type,message FROM event_log'+log_where+' ORDER BY id DESC LIMIT ?',log_args+[limit]).fetchall()
            for table,rows in ((self.history,state_rows),(self.dispatch_history,cmd_rows),(self.log_history,log_rows)):
                table.setRowCount(len(rows))
                for i,row in enumerate(rows):
                    for j,value in enumerate(row): table.setItem(i,j,QtWidgets.QTableWidgetItem(str(value)))
            self.history_chart.clear()
            for row in reversed(state_rows): self.history_chart.push({'Load':row[3],'Wind Target':row[6],'Wind Actual':row[7],'Diesel Target':row[8],'Diesel Actual':row[9]},max_points=limit)
        except Exception as exc: self.log(f'历史查询失败：{exc}')

    def export_history(self) -> None:
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,'导出状态历史 CSV','ems_state_history.csv','CSV (*.csv)')
        if not path: return
        try:
            self._ensure_db(); where,args=self._history_filter('received_at_utc')
            with self.repo.connection() as conn: rows=conn.execute('SELECT * FROM state_history'+where+' ORDER BY id ASC',args).fetchall()
            with open(path,'w',newline='',encoding='utf-8-sig') as f:
                writer=csv.writer(f); writer.writerow(rows[0].keys() if rows else ['no_data']); writer.writerows([list(row) for row in rows])
            self.log(f'已导出：{path}')
        except Exception as exc: QtWidgets.QMessageBox.warning(self,'导出失败',str(exc))

    def save_state_best_effort(self) -> None:
        if self.state is None: return
        try: self._ensure_db(); self.repo.save_state(self.state)
        except Exception as exc: self.log(f'状态本地落库失败：{exc}')

    def _process_snapshot(self) -> dict[str,object]: return {str(row["process_name"]): row for row in self.repo.get_process_status()}

    @staticmethod
    def _heartbeat_is_recent(row: object | None, *, max_age_s: float = 4.0) -> bool:
        if row is None: return False
        try:
            stamp=datetime.fromisoformat(str(row["heartbeat_at_utc"]).replace("Z","+00:00"))
            if stamp.tzinfo is None: stamp=stamp.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc)-stamp).total_seconds() <= max_age_s
        except (KeyError,TypeError,ValueError): return False

    def set_connection_state(self, connected: bool) -> None:
        """Reflect the GUI-owned TCP socket state on the connection button."""
        self.demo_mode = not connected
        self.a_status.setText("A 在线（GUI 直连）" if connected else "A 未在线")
        self.mode_status.setText("TCP 闭环" if connected else ("本地演示" if not self._connection_desired else "TCP 重连中"))
        self.connect_btn.setText("断开 A 服务器" if connected else "连接 A 服务器")
        self.connect_btn.setProperty("kind", "success" if connected else "danger")
        self.connect_btn.style().unpolish(self.connect_btn); self.connect_btn.style().polish(self.connect_btn)
        self.request_btn.setEnabled(connected)
        if not connected:
            self._manual_dispatch_pending = False
            self._last_sent_command = None
            self._last_decision_monotonic = 0.0
            if hasattr(self, "_pending_dispatch_record"):
                self._pending_dispatch_record.clear()
            if hasattr(self, "send_btn"):
                self.send_btn.setEnabled(True)
                self.send_btn.setText("下发当前调度")

    @staticmethod
    def _decision_command(decision) -> tuple[float, float, bool, bool]:
        """Extract the YK/YT (enable flags + power targets) that B transmits."""
        r = decision.result
        return (float(r.wind_target_kw), float(r.diesel_target_kw), bool(r.wind_enable), bool(r.diesel_enable))

    def _command_changed(self, command: tuple[float, float, bool, bool]) -> bool:
        """True when the candidate command differs from the last successfully sent one."""
        if self._last_sent_command is None:
            return True
        last = self._last_sent_command
        return (
            abs(command[0] - last[0]) > 1e-6
            or abs(command[1] - last[1]) > 1e-6
            or command[2] != last[2]
            or command[3] != last[3]
        )

    def _record_dispatch_result(self, decision, seq: int, status: str, ack) -> None:
        """Persist a B dispatch command so wind-execution evaluation has source data."""
        try:
            self._ensure_db()
            state = decision.state
            result = decision.result
            self.repo.record_command({
                "session_id": state.session_id,
                "step": state.step,
                "sim_time_s": state.sim_time_s,
                "source": "B",
                "seq": seq,
                "wind_target_kw": result.wind_target_kw,
                "diesel_target_kw": result.diesel_target_kw,
                "wind_enable": result.wind_enable,
                "diesel_enable": result.diesel_enable,
                "status": status,
                "reason": result.reason,
                "ack_accepted": None if ack is None else bool(ack.accepted),
                "ack_reason": None if ack is None else ack.reason,
                "ack_received_at_utc": None if ack is None else datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            })
        except Exception as exc:
            self.log(f"dispatch 命令落库失败：{exc}")

    def _persist_wind_execution(self) -> None:
        """Evaluate accepted dispatches against later A feedback and store the rows."""
        try:
            self._ensure_db()
            max_age = float(self.params.get("max_age_s", 2.0))
            keep = {
                "session_id", "dispatch_command_id", "dispatch_step", "feedback_step",
                "c_wind_action_seq", "dispatch_created_at_utc", "feedback_sampled_at_utc",
                "wind_action_applied_at_utc", "response_latency_s", "b_wind_enable",
                "b_wind_target_kw", "c_controller_wind_enable", "c_pitch_target_deg",
                "c_wind_available_kw", "c_wind_operating_limit_kw", "a_wind_running",
                "a_wind_actual_kw", "a_pitch_actual_deg", "a_fault", "start_stop_score",
                "power_tracking_score", "pitch_response_score", "capability_safety_score",
                "total_score", "verdict", "reason",
            }
            with self.repo.connection() as conn:
                cmds = conn.execute(
                    "SELECT c.* FROM dispatch_commands c "
                    "WHERE c.status='accepted' AND c.ack_accepted=1 "
                    "AND NOT EXISTS(SELECT 1 FROM wind_execution_evaluation w WHERE w.dispatch_command_id=c.id) "
                    "ORDER BY c.id"
                ).fetchall()
                for cmd in cmds:
                    feedback, _reason = find_feedback(conn, cmd, max_age_s=max_age)
                    if feedback is None:
                        continue
                    try:
                        result = evaluate_pair(cmd, feedback)
                    except ValueError:
                        continue
                    data = {key: getattr(result, key) for key in result.__dataclass_fields__ if key in keep}
                    data["outbox_id"] = None
                    data["evaluated_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                    self.repo.record_wind_execution_evaluation(data)
                    self.repo.record_log(
                        "INFO", "wind_execution_evaluated",
                        f"dispatch={cmd['id']} feedback_step={feedback['step']} score={result.total_score:.1f}",
                        session_id=cmd["session_id"], step=feedback["step"],
                    )
        except Exception as exc:
            self.log(f"风机执行评价落库失败：{exc}")

    def set_runtime_timer(self) -> None:
        """Run the YK/YT check every poll period (1 s); decisions are gated inside ``runtime_tick``."""
        if not hasattr(self, "runtime_timer"):
            return
        self.runtime_timer.stop()
        self.runtime_timer.start(max(100, int(float(self.params["poll_period_s"]) * 1000)))

    def toggle_connection(self) -> None:
        """Connect/disconnect the GUI-owned TCP socket (based on actual state)."""
        if self._connection_desired or (self.client and self.client.connected):
            self._connection_desired = False
            self._connection_generation += 1
            self._connect_in_progress = False
            if self.client:
                self.client.close()
            self.client = None
            self.set_connection_state(False)
            self.log("已断开 A 服务器")
            self.statusBar().showMessage("已断开 A 服务器")
            return
        try:
            host, port = parse_endpoint(self.host.text(), int(self.port.value()))
        except Exception as exc:
            self.log(f"地址无效：{exc}"); QtWidgets.QMessageBox.warning(self, "TCP 地址错误", str(exc)); return
        self.host.setText(host); self.port.setValue(port)
        try:
            self.repo.set_communication_config(host=host, port=port, enabled=True)
        except Exception:
            pass
        self._communication_enabled = True
        self._connection_endpoint = (host, port)
        self._connection_desired = True
        self._reconnect_attempt = 0
        self._reconnect_due = 0.0
        self._begin_connection()
        self.log(f"正在连接 A：{host}:{port}")
        self.statusBar().showMessage(f"正在连接 A {host}:{port} …")

    def request_state(self) -> None:
        """Immediately ask A for a fresh state over the GUI-owned socket."""
        if not self.client or not self.client.connected:
            self.log("未连接 A：无法请求状态"); return
        try:
            seq = self.client.request_state(full=self.client.needs_full_sync)
            self.log(f"发送 state_request seq={seq}")
        except Exception as exc:
            self._handle_connection_loss(f"state_request 失败：{exc}")

    def poll_socket(self) -> None:
        """Drive the GUI-owned TCP socket without blocking the Qt event loop."""
        self._progress_connection()
        c = self.client
        if not c or not c.connected:
            return
        try:
            results = c.receive_available()
            got_state = False
            got_ack = False
            for result in results:
                if isinstance(result, GridState):
                    got_state = True
                    self.set_state_snapshot(result)
                elif isinstance(result, Ack):
                    got_ack = True
                    self.last_ack = result
                    self.handle_ack(result)
            if got_state and self._manual_dispatch_pending:
                self._try_send_queued_dispatch()
            elif got_ack and self._manual_dispatch_pending and c._pending_state_request_seq is None and c._pending_ack_seq is None:
                self._try_send_queued_dispatch()
        except DispatchDeliveryUnknown as exc:
            decision = self._pending_dispatch_record.pop(exc.seq, None)
            if decision is not None:
                self._record_dispatch_result(decision, exc.seq, "delivery_unknown", None)
            self.last_ack = None
            self.auto_dispatch = False
            self.auto_box.blockSignals(True); self.auto_box.setChecked(False); self.auto_box.blockSignals(False)
            self.log(f"ACK 未知：seq={exc.seq}，已停止自动调度，禁止盲目重发")
            self._handle_connection_loss(str(exc))
        except socket.timeout:
            # A transient/racy recv timeout is not a broken connection; keep
            # the socket and let the next poll cycle read again.
            return
        except Exception as exc:
            self._handle_connection_loss(f"TCP 接收异常：{exc}")

    def send_current(self) -> None:
        """Send one dispatch now and, in closed-loop, enable periodic auto-dispatch."""
        if not self.client or not self.client.connected:
            self.log("未连接 A：拒绝发送 TCP dispatch"); QtWidgets.QMessageBox.information(self, "当前为本地模式", "先连接 A server 才会真正下发 dispatch。"); return
        if self.client._uncertain_dispatch_seq is not None:
            self.log(f"ACK 未知：seq={self.client._uncertain_dispatch_seq}，禁止盲目重发")
            QtWidgets.QMessageBox.warning(self, "ACK 未知", "指令可能已到达 A，但 ACK 无法确认。请先恢复连接并核对历史，不要直接重发。"); return
        if self._manual_dispatch_pending:
            return
        if not self.params.get("closed_loop"):
            self.log("开环模式：仅计算展示，不发送控制命令")
            QtWidgets.QMessageBox.information(self, "当前为开环", "开环模式仅计算和展示，不生成可下发控制命令。")
            return
        self.auto_dispatch = True
        self.auto_box.blockSignals(True); self.auto_box.setChecked(True); self.auto_box.blockSignals(False)
        self.log("闭环模式：开启按周期自动下发，无需重复点击")
        self._manual_dispatch_pending = True
        self.send_btn.setEnabled(False)
        self.send_btn.setText("等待 A 当前状态...")
        self.statusBar().showMessage("等待 A 返回最新 state，随后自动下发 EMS 调度")
        self.log("手动下发已排队：等待 A 当前 state")
        c = self.client
        try:
            c.request_state(full=c.needs_full_sync)
        except ProtocolError as exc:
            self.log(f"当前无法立即请求 state，保持排队：{exc}")
        except Exception as exc:
            self._cancel_queued_dispatch(f"state_request 失败：{exc}", show_message=True)
            return
        if c._pending_state_request_seq is None and c._pending_ack_seq is None:
            self._try_send_queued_dispatch()

    def _try_send_queued_dispatch(self) -> None:
        if not self._manual_dispatch_pending:
            return
        c = self.client
        if not c or not c.connected:
            self._cancel_queued_dispatch("A 已断开，取消排队的 dispatch", show_message=False)
            return
        if c._uncertain_dispatch_seq is not None:
            self._cancel_queued_dispatch(f"ACK 未知 seq={c._uncertain_dispatch_seq}，不自动重发", show_message=False)
            return
        if c._pending_state_request_seq is not None or c._pending_ack_seq is not None:
            return
        if self.state is None:
            self.log("最新 state 尚未进入 GUI，继续等待"); return
        try:
            self.calculate_current()
            if self.last_decision is None:
                self._cancel_queued_dispatch("最新 state 无法生成有效 EMS decision", show_message=False)
                return
            seq = c.send_dispatch_nowait(self.last_decision)
            self._pending_dispatch_record[seq] = self.last_decision
            self._last_sent_command = self._decision_command(self.last_decision)
            self._last_decision_monotonic = time.monotonic()
            ack = c.get_ack(seq)
            if ack is not None:
                self.last_ack = ack
                self.handle_ack(ack)
            self.log(f"发送 dispatch seq={seq}，等待 ACK")
            self._manual_dispatch_pending = False
            self.send_btn.setEnabled(True)
            self.send_btn.setText("下发当前调度")
            self.statusBar().showMessage(f"调度已下发 seq={seq}" + (f" / ACK={ack.accepted}" if ack else ""))
        except DispatchDeliveryUnknown as exc:
            decision = self._pending_dispatch_record.pop(exc.seq, None)
            if decision is not None:
                self._record_dispatch_result(decision, exc.seq, "delivery_unknown", None)
            self.last_ack = None
            self._manual_dispatch_pending = False
            self.send_btn.setEnabled(True)
            self.send_btn.setText("下发当前调度")
            self.log(f"ACK 未知：seq={exc.seq}，禁止盲目重发")
            QtWidgets.QMessageBox.warning(self, "ACK 未知", "指令可能已到达 A，但 ACK 无法确认。请先恢复连接并核对历史，不要直接重发。")
        except ProtocolError as exc:
            if c._pending_state_request_seq is not None or c._pending_ack_seq is not None:
                self.log(f"dispatch 等待中：{exc}")
                return
            self._cancel_queued_dispatch(f"dispatch 失败：{exc}", show_message=True)
        except Exception as exc:
            self._cancel_queued_dispatch(f"dispatch 失败：{exc}", show_message=True)

    def set_auto(self, checked: bool) -> None:
        """Persist open/closed-loop mode; the same GUI timer runs the loop."""
        try:
            runtime = self.repo.get_runtime_config()
            self.repo.set_runtime_config(poll_period_s=float(runtime["poll_period_s"]), dispatch_period_s=float(runtime["dispatch_period_s"]), command_timeout_s=runtime["command_timeout_s"], closed_loop=bool(checked), max_state_age_s=float(runtime["max_state_age_s"]))
            self.params["closed_loop"] = bool(checked)
            self.auto_dispatch = bool(checked)
            if checked:
                self._last_sent_command = None
                self._last_decision_monotonic = 0.0
            self.set_connection_state(self.client is not None and self.client.connected)
            self.log(f"运行模式已切换为{'闭环' if checked else '开环'}")
        except Exception as exc:
            self.log(f"运行模式保存失败：{exc}")

    def runtime_tick(self) -> None:
        """Periodic closed-loop dispatch from the GUI-owned socket.

        Fired every poll period (default 1 s). A new decision is recomputed
        only every dispatch period (default 5 s); the candidate YK/YT command
        is transmitted only when it changed since the last successful send,
        so unchanged targets are never re-generated or re-sent.
        """
        if not self.auto_dispatch or not self.params.get("closed_loop"):
            return
        if self._manual_dispatch_pending:
            return
        if not self.client or not self.client.connected:
            return
        if self.state is None:
            return
        if self.client._pending_state_request_seq is not None or self.client._pending_ack_seq is not None:
            return
        now = time.monotonic()
        dispatch_period = float(self.params.get("dispatch_period_s", 5.0))
        if self._last_decision_monotonic == 0.0 or (now - self._last_decision_monotonic) >= dispatch_period:
            self.calculate_current()
            self._last_decision_monotonic = now
        if self.last_decision is None:
            return
        command = self._decision_command(self.last_decision)
        if not self._command_changed(command):
            return
        try:
            seq = self.client.send_dispatch_nowait(self.last_decision)
            self._pending_dispatch_record[seq] = self.last_decision
            ack = self.client.get_ack(seq)
            if ack is not None:
                self.last_ack = ack
                self.handle_ack(ack)
            self._last_sent_command = command
            self.log(f"自动下发 dispatch seq={seq}" + (f" / ACK={ack.accepted}" if ack else ""))
        except DispatchDeliveryUnknown as exc:
            decision = self._pending_dispatch_record.pop(exc.seq, None)
            if decision is not None:
                self._record_dispatch_result(decision, exc.seq, "delivery_unknown", None)
            self.log(f"自动调度 ACK 未知 seq={exc.seq}，禁止盲目重发")
        except ProtocolError as exc:
            self.log(f"自动调度等待中：{exc}")
        except Exception as exc:
            self.log(f"自动调度异常：{exc}")

    def update_comm_diag(self) -> None:
        if not hasattr(self,"comm_state"): return
        c = self.client
        self.comm_state.setText("CONNECTED" if c and c.connected else "DISCONNECTED")
        if not c:
            self.comm_pending.setText("--"); self.comm_full.setText("--"); self.comm_seq.setText("--"); self.comm_ack.setText("--"); return
        self.comm_pending.setText(str(c._pending_state_request_seq))
        self.comm_full.setText(str(c.needs_full_sync))
        self.comm_seq.setText(str(c._last_incoming_seq))
        self.comm_ack.setText("--" if self.last_ack is None else f"{self.last_ack.ack_seq}/{self.last_ack.accepted}")

    def closeEvent(self,event) -> None:
        self.auto_dispatch = False
        self._manual_dispatch_pending = False
        self._connection_desired = False
        self._connection_generation += 1
        if self.client:
            self.client.close()
        try: self.repo.heartbeat("B_GUI",pid=os.getpid(),state="STOPPED",detail="window closed")
        finally: event.accept()

    def handle_ack(self,ack) -> None:
        super().handle_ack(ack)
        decision = self._pending_dispatch_record.pop(ack.ack_seq, None)
        if decision is not None:
            self._record_dispatch_result(decision, ack.ack_seq, "accepted" if ack.accepted else "rejected", ack)
            if ack.accepted:
                self._persist_wind_execution()
        try:
            session_id=self.state.session_id if self.state else None; step=self.state.step if self.state else None; self._ensure_db(); self.repo.record_log('INFO' if ack.accepted else 'WARNING','ack_received',f'ACK seq={ack.ack_seq} accepted={ack.accepted} reason={ack.reason}',session_id=session_id,step=step)
        except Exception as exc: self.log(f'ACK 日志写库失败：{exc}')

    def refresh_alarms(self) -> None:
        if not hasattr(self,'alarm_table'): return
        rows=[]
        if self.state and self.state.fault: rows.append(('HIGH','C_FAULT','C protection/control priority，B 抑制风机目标',self.state.session_id,self.state.step))
        if self.state and self.state.received_age_s > self.params['max_age_s']: rows.append(('HIGH','STALE_STATE',f'state age={self.state.received_age_s:.2f}s',self.state.session_id,self.state.step))
        try:
            self._ensure_db()
            with self.repo.connection() as conn: db_rows=conn.execute('SELECT level,event_type,message,session_id,step FROM event_log ORDER BY id DESC LIMIT 100').fetchall()
            rows.extend(tuple(x) for x in db_rows)
        except Exception: pass
        self.alarm_table.setRowCount(min(len(rows),300))
        for i,row in enumerate(rows[:300]):
            for j,v in enumerate(row): self.alarm_table.setItem(i,j,QtWidgets.QTableWidgetItem(str(v)))
        self.refresh_evaluation()

    def alarm_page(self) -> QtWidgets.QWidget:
        w=QtWidgets.QWidget(); l=QtWidgets.QVBoxLayout(w); l.setContentsMargins(22,18,22,18); l.addWidget(self.section('报警与评价')); top=QtWidgets.QHBoxLayout(); self.eval_overall=self._eval_card('综合评价','/100'); self.eval_ems=self._eval_card('EMS 调度结果','/100'); self.eval_system=self._eval_card('风机执行效果','/100'); self.eval_grade=self._eval_card('评价等级',''); [top.addWidget(card) for card in (self.eval_overall,self.eval_ems,self.eval_system,self.eval_grade)]; l.addLayout(top)
        control=self.make_card(); g=QtWidgets.QGridLayout(control); g.addWidget(QtWidgets.QLabel('评价范围'),0,0); self.eval_period=QtWidgets.QComboBox(); [self.eval_period.addItem(text,data) for text,data in [('最近 1 分钟','1m'),('最近 5 分钟','5m'),('本次 Session','session'),('全部历史','all')]]; self.eval_period.setCurrentIndex(1); self.eval_period.currentIndexChanged.connect(self.refresh_evaluation); g.addWidget(self.eval_period,0,1); refresh=QtWidgets.QPushButton('刷新评价'); refresh.setProperty('kind','primary'); refresh.clicked.connect(self.refresh_evaluation); g.addWidget(refresh,0,2); self.eval_hint=QtWidgets.QLabel('综合评价仅由五项指标组成：供需平衡 30% · 风能利用 25% · 柴油经济性 15% · 运行约束 15% · 调度跟踪 15%'); self.eval_hint.setProperty('hint',True); self.eval_hint.setWordWrap(True); g.addWidget(self.eval_hint,0,3,1,3); l.addWidget(control)
        scores=self.make_card(); sg=QtWidgets.QGridLayout(scores); labels=[('balance','供需平衡'),('wind','风能利用'),('diesel','柴油经济性'),('constraint','运行约束'),('tracking','调度跟踪')]; self.eval_score_labels={}; self.eval_rule_combos={}
        for i,(key,title) in enumerate(labels):
            row=(i//3)*2; col=i%3; title_box=QtWidgets.QHBoxLayout(); title_box.setContentsMargins(0,0,0,0); title_label=QtWidgets.QLabel(f'{title} · {int(EVALUATION_WEIGHTS[key]*100)}%'); combo=QtWidgets.QComboBox(); combo.setMinimumWidth(190); combo.addItem('查看评分标准'); [combo.addItem(rule) for rule in SCORING_RULES[key]['rules']]; title_box.addWidget(title_label,1); title_box.addWidget(combo); title_widget=QtWidgets.QWidget(); title_widget.setLayout(title_box); sg.addWidget(title_widget,row,col); value=QtWidgets.QLabel('-- / 100'); value.setProperty('value',True); self.eval_score_labels[key]=value; sg.addWidget(value,row+1,col); self.eval_rule_combos[key]=combo
        l.addWidget(scores); detail=self.make_card(); dg=QtWidgets.QGridLayout(detail); detail_items=[('samples','评价样本'),('balance_error','平均功率不平衡'),('wind_util','风能利用率'),('diesel_share','柴油供电占比'),('unserved','平均未供电功率'),('surplus','平均过剩功率'),('violations','运行约束违反'),('tracking_error','平均目标跟踪误差'),('dispatches','调度次数')]; self.eval_detail_labels={}
        for i,(key,title) in enumerate(detail_items): dg.addWidget(QtWidgets.QLabel(title),i//5*2,i%5); value=QtWidgets.QLabel('--'); value.setProperty('cardtitle',True); self.eval_detail_labels[key]=value; dg.addWidget(value,i//5*2+1,i%5)
        l.addWidget(detail); self.eval_table=QtWidgets.QTableWidget(0,5); self.eval_table.setHorizontalHeaderLabels(['评价项','权重','评分','当前指标','说明']); self.eval_table.horizontalHeader().setStretchLastSection(True); self.eval_table.setAlternatingRowColors(True); l.addWidget(self.eval_table,1); return w

    def _eval_card(self,title:str,unit:str):
        card=self.make_card(); box=QtWidgets.QVBoxLayout(card); box.setContentsMargins(14,10,14,10); t=QtWidgets.QLabel(title); t.setProperty('cardtitle',True); value=QtWidgets.QLabel('--'); value.setProperty('value',True); hint=QtWidgets.QLabel(unit); hint.setProperty('hint',True); box.addWidget(t); box.addWidget(value); box.addWidget(hint); card.value=value; return card

    def refresh_evaluation(self) -> None:
        if not hasattr(self,'eval_overall'): return
        try: self._show_evaluation(evaluate_repository(self.repo,self.eval_period.currentData() or '5m'))
        except Exception as exc: self.log(f'调度评价计算失败：{exc}'); self.eval_hint.setText(f'评价暂不可用：{exc}')

    def _show_evaluation(self,r:EvaluationResult) -> None:
        self.eval_overall.value.setText(f'{r.overall_score:.1f}'); self.eval_ems.value.setText(f'{r.ems_score:.1f}'); self.eval_system.value.setText('无数据' if r.wind_execution_count == 0 or r.wind_execution_score is None else f'{r.system_score:.1f}'); self.eval_grade.value.setText(r.grade)
        for key,score in [('balance',r.balance_score),('wind',r.wind_score),('diesel',r.diesel_score),('constraint',r.constraint_score),('tracking',r.tracking_score)]: self.eval_score_labels[key].setText(f'{score:.1f} / 100')
        details={'samples':str(r.sample_count),'balance_error':f'{r.avg_balance_error_kw:.2f} kW','wind_util':f'{r.wind_utilization_pct:.1f} %','diesel_share':f'{r.diesel_share_pct:.1f} %','unserved':f'{r.unserved_kw:.2f} kW','surplus':f'{r.surplus_kw:.2f} kW','violations':str(r.constraint_violations),'tracking_error':f'{r.tracking_error_kw:.2f} kW','dispatches':str(r.dispatch_count)}
        for key,text in details.items(): self.eval_detail_labels[key].setText(text)
        rows=[('供需平衡',EVALUATION_WEIGHTS['balance'],r.balance_score,f'{r.avg_balance_error_kw:.2f} kW','平均功率不平衡越小越好'),('风能利用',EVALUATION_WEIGHTS['wind'],r.wind_score,f'{r.wind_utilization_pct:.1f}%','实际风电 / 可利用风电'),('柴油经济性',EVALUATION_WEIGHTS['diesel'],r.diesel_score,f'{r.diesel_share_pct:.1f}%','柴油供电占负荷比例'),('运行约束',EVALUATION_WEIGHTS['constraint'],r.constraint_score,f'{r.constraint_violations} 次','检查风电运行边界'),('调度跟踪',EVALUATION_WEIGHTS['tracking'],r.tracking_score,f'{r.tracking_error_kw:.2f} kW','目标与实际平均偏差')]
        self.eval_table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,value in enumerate([row[0],f'{row[1]*100:.0f}%',f'{row[2]:.1f}',row[3],row[4]]): self.eval_table.setItem(i,j,QtWidgets.QTableWidgetItem(str(value)))
        self.eval_hint.setText(f'{r.period_label}：综合评价按五项权重计算；评分标准可在每项标题右侧下拉查看。评价模块只读历史数据，不修改 dispatch，不参与 TCP。')


def main() -> int:
    app=QtWidgets.QApplication([]); window=MainWindow(); window.show(); return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
