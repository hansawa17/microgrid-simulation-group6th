"""Database-only operator GUI for B/EMS.

The formal runtime keeps TCP ownership in :mod:`communication_service` and
dispatch calculation in :mod:`compute_service`.  This window only reads and
writes ``ems.db``; the inherited legacy widgets are reused for presentation.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timezone

from PyQt6 import QtCore, QtWidgets

from .evaluation import EVALUATION_WEIGHTS, EvaluationResult, SCORING_RULES, evaluate_repository
from .gui_b_legacy import MainWindow as _LegacyMainWindow, TrendChart
from .models import DispatchConfig, DispatchResult
from .tcpB import parse_endpoint


class MainWindow(_LegacyMainWindow):
    """Operator window with no socket ownership and no automatic scheduler."""

    def __init__(self) -> None:
        self._communication_enabled = False
        self._last_db_state_key: tuple[str, int, str] | None = None
        self._last_diag_text = ""
        super().__init__()
        self.client = None
        self._ensure_db()
        comm = self.repo.get_communication_config()
        self._communication_enabled = bool(comm["enabled"])
        self.host.setText(str(comm["host"])); self.port.setValue(int(comm["port"]))
        self.auto_dispatch = bool(self.params["closed_loop"])
        self.auto_box.blockSignals(True); self.auto_box.setChecked(self.auto_dispatch); self.auto_box.blockSignals(False)
        self.safe_label.setText("正式模式：GUI 仅写入 ems.db；B_COMPUTE 生成调度，B_IO 独占 TCP、核对状态并等待 ACK。")
        self.repo.heartbeat("B_GUI", pid=os.getpid(), state="RUNNING", detail="database-only operator GUI")
        self.poll_socket()
        self.log("GUI 启动：数据库模式；本窗口不直接打开 TCP，也不执行自动调度")

    def monitor_page(self) -> QtWidgets.QWidget:
        page = super().monitor_page(); layout = page.layout(); card = self.make_card(); box = QtWidgets.QVBoxLayout(card)
        title = QtWidgets.QLabel("RTU / SCADA 四遥实时点表"); title.setProperty("cardtitle", True); box.addWidget(title)
        self.scada_table = QtWidgets.QTableWidget(0, 6); self.scada_table.setHorizontalHeaderLabels(["类别", "点名", "值", "单位", "数据时刻", "说明"])
        self.scada_table.setAlternatingRowColors(True); self.scada_table.horizontalHeader().setStretchLastSection(True); self.scada_table.setMinimumHeight(230); box.addWidget(self.scada_table)
        layout.insertWidget(max(0, layout.count() - 1), card); return page

    def refresh_state_views(self) -> None:
        super().refresh_state_views(); self._refresh_scada_table()

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
        return DispatchConfig(wind_min_kw=self.params['wind_min_kw'], wind_max_kw=self.params['wind_max_kw'], diesel_max_kw=self.params['diesel_max_kw'], reserve_kw=self.params['reserve_kw'], diesel_min_kw=float(getattr(self, '_diesel_min_kw', 20.0)), max_state_age_s=self.params['max_age_s'], c_has_control_priority=True)

    def _ensure_db(self) -> None:
        """Create the schema only when it is missing; never reset user parameters."""
        try: self.repo.get_parameters(); self.repo.get_runtime_config(); self.repo.get_physical_parameters()
        except Exception: self.repo.initialize()

    def params_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w); l.setContentsMargins(22,18,22,18); l.addWidget(self.section('参数设置'))
        b_card = self.make_card(); bf = QtWidgets.QFormLayout(b_card)
        self.p_wind_min = QtWidgets.QDoubleSpinBox(); self.p_wind_min.setRange(0,1000); self.p_wind_min.setSuffix(' kW')
        self.p_wind_max = QtWidgets.QDoubleSpinBox(); self.p_wind_max.setRange(0,1000); self.p_wind_max.setSuffix(' kW')
        self.p_diesel_max = QtWidgets.QDoubleSpinBox(); self.p_diesel_max.setRange(0,2000); self.p_diesel_max.setSuffix(' kW')
        self.p_reserve = QtWidgets.QDoubleSpinBox(); self.p_reserve.setRange(0.1,1000); self.p_reserve.setDecimals(2); self.p_reserve.setSuffix(' kW')
        self.p_age = QtWidgets.QDoubleSpinBox(); self.p_age.setRange(0.1,60); self.p_age.setDecimals(2); self.p_age.setSuffix(' s')
        self.p_poll = QtWidgets.QDoubleSpinBox(); self.p_poll.setRange(0.1,60); self.p_poll.setDecimals(2); self.p_poll.setSuffix(' s')
        self.p_dispatch = QtWidgets.QDoubleSpinBox(); self.p_dispatch.setRange(0.1,300); self.p_dispatch.setDecimals(2); self.p_dispatch.setSuffix(' s')
        self.p_closed = QtWidgets.QCheckBox('B closed loop（默认开启）'); self.p_closed.setChecked(True)
        for label, widget in [('B：风电最小目标',self.p_wind_min),('B：风电容量上限',self.p_wind_max),('B：柴油容量上限',self.p_diesel_max),('B：柴油 reserve',self.p_reserve),('B：最大状态年龄',self.p_age),('B：采集周期',self.p_poll),('B：调度周期',self.p_dispatch),('B：运行模式',self.p_closed)]: bf.addRow(label,widget)
        l.addWidget(b_card)
        c_card = self.make_card(); cf = QtWidgets.QFormLayout(c_card); self.c_physical_widgets = {}
        physical_fields = [('wind_rated_kw','风机额定功率',' kW'),('wind_cut_in_mps','切入风速',' m/s'),('wind_rated_speed_mps','额定风速',' m/s'),('wind_cut_out_mps','切出风速',' m/s'),('pitch_min_deg','桨距下限',' °'),('pitch_max_deg','桨距上限',' °'),('wind_ramp_up_kw_s','风电升爬坡率',' kW/s'),('wind_ramp_down_kw_s','风电降爬坡率',' kW/s'),('diesel_min_kw','柴发最小功率',' kW'),('diesel_max_kw','柴发最大功率',' kW')]
        for key,title,suffix in physical_fields:
            spin=QtWidgets.QDoubleSpinBox(); spin.setRange(-1e9,1e9); spin.setDecimals(2); spin.setSuffix(suffix); spin.setEnabled(False); self.c_physical_widgets[key]=spin; cf.addRow(title,spin)
        note_c=QtWidgets.QLabel('以上物理参数由 C 维护，B 只读副本展示；当前值按 ems.db 中的统一基线保存，不提供 B 编辑入口。'); note_c.setProperty('hint',True); note_c.setWordWrap(True); cf.addRow(note_c)
        l.addWidget(c_card)
        note=QtWidgets.QLabel('B 可写参数仅包括 reserve、调度上下限、采集周期、决策周期和闭环模式。保存后立即更新本地定时器；采集周期在当前连接中同步到 B TCP client 的轮询配置。'); note.setProperty('hint',True); note.setWordWrap(True); l.addWidget(note)
        row=QtWidgets.QHBoxLayout(); load=QtWidgets.QPushButton('从 ems.db 读取'); load.setProperty('kind','secondary'); load.clicked.connect(self.load_db_config); save=QtWidgets.QPushButton('保存参数'); save.setProperty('kind','primary'); save.clicked.connect(self.save_params); row.addWidget(load); row.addWidget(save); row.addStretch(); l.addLayout(row); l.addStretch(); return w

    def load_db_config(self) -> None:
        try:
            try: p=self.repo.get_parameters(); r=self.repo.get_runtime_config(); physical=self.repo.get_physical_parameters()
            except Exception: self.repo.initialize(); p=self.repo.get_parameters(); r=self.repo.get_runtime_config(); physical=self.repo.get_physical_parameters()
            self.params.update(wind_min_kw=float(p['wind_min_kw']),wind_max_kw=float(p['wind_max_kw']),diesel_max_kw=float(p['diesel_max_kw']),reserve_kw=float(p['reserve_kw']),max_age_s=float(r['max_state_age_s']),poll_period_s=float(r['poll_period_s']),dispatch_period_s=float(r['dispatch_period_s']),closed_loop=bool(r['closed_loop']))
            self._diesel_min_kw=float(physical['diesel_min_kw'])
            if hasattr(self,'c_physical_widgets'):
                for key,widget in self.c_physical_widgets.items(): widget.setValue(float(physical[key]))
        except Exception as exc: self.log(f'ems.db 尚未有完整参数，使用 GUI 默认值：{exc}')
        if hasattr(self,'p_wind_min'):
            self.p_wind_min.setValue(self.params['wind_min_kw']); self.p_wind_max.setValue(self.params['wind_max_kw']); self.p_diesel_max.setValue(self.params['diesel_max_kw']); self.p_reserve.setValue(self.params['reserve_kw']); self.p_age.setValue(self.params['max_age_s']); self.p_poll.setValue(self.params['poll_period_s']); self.p_dispatch.setValue(self.params['dispatch_period_s']); self.p_closed.setChecked(self.params['closed_loop'])
        self.core=self.core.__class__(self.config())
        if hasattr(self,'runtime_timer'): self.set_runtime_timer()

    def save_params(self) -> None:
        values={'wind_min_kw':self.p_wind_min.value(),'wind_max_kw':self.p_wind_max.value(),'diesel_max_kw':self.p_diesel_max.value(),'reserve_kw':self.p_reserve.value(),'max_age_s':self.p_age.value(),'poll_period_s':self.p_poll.value(),'dispatch_period_s':self.p_dispatch.value(),'closed_loop':self.p_closed.isChecked()}
        if values['wind_max_kw'] < values['wind_min_kw']: QtWidgets.QMessageBox.warning(self,'参数错误','风电容量上限必须不小于风电最小目标。'); return
        if values['diesel_max_kw'] < values['reserve_kw'] or values['reserve_kw'] <= 0: QtWidgets.QMessageBox.warning(self,'参数错误','柴油容量上限必须不小于 reserve，且 reserve 必须大于 0。'); return
        try:
            self._ensure_db(); self.repo.set_parameters(wind_min_kw=values['wind_min_kw'],wind_max_kw=values['wind_max_kw'],diesel_max_kw=values['diesel_max_kw'],reserve_kw=values['reserve_kw']); self.repo.set_runtime_config(poll_period_s=values['poll_period_s'],dispatch_period_s=values['dispatch_period_s'],closed_loop=values['closed_loop'],max_state_age_s=values['max_age_s'])
            self.params.update(values); self.core=self.core.__class__(self.config()); self.set_runtime_timer(); self.log('B 自有参数已写入 ems.db；C 物理参数未被写入'); self.statusBar().showMessage('B EMS 参数已更新并写入 ems.db')
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
        """Present B_IO state without taking ownership of its socket."""
        self.demo_mode = not connected
        self.a_status.setText("A 在线（经 B_IO）" if connected else "A 未在线")
        self.mode_status.setText("数据库闭环" if self.params.get("closed_loop") else "数据库开环")
        self.connect_btn.setText("停用 B_IO 通信" if connected else "启用 B_IO 通信")
        self.connect_btn.setProperty("kind", "danger" if connected else "success")
        self.connect_btn.style().unpolish(self.connect_btn); self.connect_btn.style().polish(self.connect_btn)
        self.request_btn.setEnabled(connected)

    def toggle_connection(self) -> None:
        """Store the desired endpoint; B_IO applies it on its next cycle."""
        try: host,port=parse_endpoint(self.host.text(),int(self.port.value())); enabled=not self._communication_enabled; self.repo.set_communication_config(host=host,port=port,enabled=enabled)
        except Exception as exc:
            self.log(f"通信配置无效：{exc}"); QtWidgets.QMessageBox.warning(self,"通信配置错误",str(exc)); return
        self.host.setText(host); self.port.setValue(port); self._communication_enabled=enabled; self.set_connection_state(False); action="启用" if enabled else "停用"; self.log(f"已{action} B_IO 通信配置：{host}:{port}"); self.statusBar().showMessage(f"通信配置已写入 ems.db；由 B_IO {action}连接")

    def request_state(self) -> None:
        """State polling belongs to B_IO; the GUI never emits protocol frames."""
        self.statusBar().showMessage("B_IO 会按采集周期自动请求状态；GUI 不直接发送 TCP 报文"); self.log("状态请求由 B_IO 自动执行，GUI 未直接访问网络")

    def poll_socket(self) -> None:
        """Poll SQLite without blocking the Qt event loop."""
        try:
            state=self.repo.get_current_grid_state()
            if state is not None:
                key=(state.session_id,state.step,state.sampled_at_utc); self.state=state
                if key != self._last_db_state_key:
                    self._last_db_state_key=key; self.refresh_state_views(); self.log(f"从 ems.db 读取 state session={state.session_id} step={state.step}")
                elif hasattr(self,"cards"): self.cards["age"].set_value(f"{state.received_age_s:.2f}")
            config=self.repo.get_communication_config(); self._communication_enabled=bool(config["enabled"]); io_row=self._process_snapshot().get("B_IO")
            online=bool(io_row is not None and str(io_row["state"]) == "ONLINE" and self._heartbeat_is_recent(io_row))
            self.set_connection_state(online)
            self.repo.heartbeat("B_GUI",pid=os.getpid(),state="RUNNING",detail="database-only operator GUI")
        except Exception as exc:
            text=str(exc)
            if text != self._last_diag_text: self._last_diag_text=text; self.log(f"读取 ems.db 失败：{text}")

    def send_current(self) -> None:
        """Queue one manual command; B_IO remains the sole network sender."""
        try:
            self._ensure_db(); runtime=self.repo.get_runtime_config()
            if not bool(runtime["closed_loop"]): QtWidgets.QMessageBox.information(self,"当前为开环","开环模式仅计算和展示，不生成可下发控制命令。"); return
            state=self.repo.get_current_grid_state()
            if state is None: raise RuntimeError("ems.db 尚无 A 实时状态")
            if state.received_age_s > float(runtime["max_state_age_s"]): raise RuntimeError(f"状态已过期：{state.received_age_s:.2f}s > {float(runtime['max_state_age_s']):.2f}s")
            self.state=state; self.calculate_current()
            if self.last_decision is None: return
            outbox_id,created=self.repo.queue_decision(state,self.last_decision.result,executable=True); message=f"调度已进入数据库队列 outbox={outbox_id}，等待 B_IO 下发" if created else f"该状态的调度已存在 outbox={outbox_id}，未重复下发"; self.log(message); self.statusBar().showMessage(message)
        except Exception as exc: self.log(f"调度入队失败：{exc}"); QtWidgets.QMessageBox.warning(self,"调度未入队",str(exc))

    def _try_send_queued_dispatch(self) -> None:
        self._manual_dispatch_pending=False; self.send_btn.setEnabled(True); self.send_btn.setText("下发当前调度"); self.send_current()

    def set_auto(self, checked: bool) -> None:
        """Persist open/closed-loop mode for the independent compute process."""
        try:
            runtime=self.repo.get_runtime_config(); self.repo.set_runtime_config(poll_period_s=float(runtime["poll_period_s"]),dispatch_period_s=float(runtime["dispatch_period_s"]),command_timeout_s=runtime["command_timeout_s"],closed_loop=bool(checked),max_state_age_s=float(runtime["max_state_age_s"])); self.params["closed_loop"]=bool(checked); self.auto_dispatch=bool(checked); self.set_connection_state(False); self.log(f"运行模式已切换为{'闭环' if checked else '开环'}；由 B_COMPUTE 执行")
        except Exception as exc: self.log(f"运行模式保存失败：{exc}")

    def runtime_tick(self) -> None:
        """Refresh the latest DB decision; never calculate or transmit here."""
        try:
            with self.repo.connection() as conn: row=conn.execute("SELECT * FROM dispatch_outbox ORDER BY id DESC LIMIT 1").fetchone()
            if row is None: return
            result=DispatchResult(wind_target_kw=float(row["wind_target_kw"]),diesel_target_kw=float(row["diesel_target_kw"]),wind_enable=bool(row["wind_enable"]),diesel_enable=bool(row["diesel_enable"]),target_unserved_kw=float(row["target_unserved_kw"]),target_surplus_kw=float(row["target_surplus_kw"]),reason=str(row["reason"])); self.d_w.setText(f"{result.wind_target_kw:.2f} kW"); self.d_d.setText(f"{result.diesel_target_kw:.2f} kW"); self.d_unserved.setText(f"{result.target_unserved_kw:.2f} kW"); self.d_surplus.setText(f"{result.target_surplus_kw:.2f} kW"); self.d_enable.setText(f"Wind={result.wind_enable} / Diesel={result.diesel_enable}"); self.d_reason.setText(result.reason); self.ack_label.setText(f"outbox={row['id']} / {row['status']} / {row['ack_reason'] or '--'}")
        except Exception as exc: self.log(f"调度队列刷新失败：{exc}")

    def update_comm_diag(self) -> None:
        if not hasattr(self,"comm_state"): return
        try:
            statuses=self._process_snapshot(); io_row=statuses.get("B_IO"); compute_row=statuses.get("B_COMPUTE"); io_state="未启动" if io_row is None else str(io_row["state"]); compute_state="未启动" if compute_row is None else str(compute_row["state"]); self.comm_state.setText(f"B_IO={io_state} / B_COMPUTE={compute_state}")
            with self.repo.connection() as conn:
                counts={str(row["status"]):int(row["n"]) for row in conn.execute("SELECT status,COUNT(*) AS n FROM dispatch_outbox GROUP BY status")}; latest=conn.execute("SELECT id,status,protocol_seq,ack_accepted FROM dispatch_outbox ORDER BY id DESC LIMIT 1").fetchone()
            self.comm_pending.setText(f"pending={counts.get('pending',0)} / sending={counts.get('sending',0)}"); self.comm_full.setText("SQLite outbox / GUI 无套接字"); self.comm_seq.setText("--" if latest is None else str(latest["protocol_seq"] or "--")); self.comm_ack.setText("--" if latest is None else f"outbox={latest['id']} / {latest['status']} / {latest['ack_accepted']}")
        except Exception as exc: self.comm_state.setText(f"数据库诊断失败：{exc}")

    def closeEvent(self,event) -> None:
        try: self.repo.heartbeat("B_GUI",pid=os.getpid(),state="STOPPED",detail="window closed")
        finally: event.accept()

    def handle_ack(self,ack) -> None:
        super().handle_ack(ack)
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
