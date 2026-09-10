"""Entry point for the enhanced B/EMS GUI.

The existing TCP/dispatch implementation remains inherited from
``gui_b_legacy.MainWindow``. This layer only extends GUI presentation,
parameter persistence and local EMS history/evaluation recording; it does not
change the TCP transport implementation.
"""
from __future__ import annotations

import csv

from PyQt6 import QtWidgets

from .evaluation import EVALUATION_WEIGHTS, EvaluationResult, SCORING_RULES, evaluate_repository
from .gui_b_legacy import MainWindow as _LegacyMainWindow
from .models import DispatchConfig
from .repository import utc_now
from .tcpB import DispatchDeliveryUnknown, ProtocolError


class MainWindow(_LegacyMainWindow):
    """GUI-only extension over the existing B TCP/dispatch window."""

    def config(self) -> DispatchConfig:
        return DispatchConfig(
            wind_min_kw=self.params['wind_min_kw'],
            wind_max_kw=self.params['wind_max_kw'],
            diesel_max_kw=self.params['diesel_max_kw'],
            reserve_kw=self.params['reserve_kw'],
            diesel_min_kw=float(getattr(self, '_diesel_min_kw', 20.0)),
            max_state_age_s=self.params['max_age_s'],
            c_has_control_priority=True,
        )

    def _ensure_db(self) -> None:
        """Create the schema only when it is missing; never reset user parameters."""
        try:
            self.repo.get_parameters()
            self.repo.get_runtime_config()
            self.repo.get_physical_parameters()
        except Exception:
            self.repo.initialize()

    def params_page(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w)
        l.setContentsMargins(22,18,22,18); l.addWidget(self.section('参数设置'))
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
            try:
                p=self.repo.get_parameters(); r=self.repo.get_runtime_config(); physical=self.repo.get_physical_parameters()
            except Exception:
                self.repo.initialize(); p=self.repo.get_parameters(); r=self.repo.get_runtime_config(); physical=self.repo.get_physical_parameters()
            self.params.update(wind_min_kw=float(p['wind_min_kw']),wind_max_kw=float(p['wind_max_kw']),diesel_max_kw=float(p['diesel_max_kw']),reserve_kw=float(p['reserve_kw']),max_age_s=2.0,poll_period_s=float(r['poll_period_s']),dispatch_period_s=float(r['dispatch_period_s']),closed_loop=bool(r['closed_loop']))
            self._diesel_min_kw=float(physical['diesel_min_kw'])
            if hasattr(self,'c_physical_widgets'):
                for key,widget in self.c_physical_widgets.items(): widget.setValue(float(physical[key]))
        except Exception as exc:
            self.log(f'ems.db 尚未有完整参数，使用 GUI 默认值：{exc}')
        if hasattr(self,'p_wind_min'):
            self.p_wind_min.setValue(self.params['wind_min_kw']); self.p_wind_max.setValue(self.params['wind_max_kw']); self.p_diesel_max.setValue(self.params['diesel_max_kw']); self.p_reserve.setValue(self.params['reserve_kw']); self.p_age.setValue(self.params['max_age_s']); self.p_poll.setValue(self.params['poll_period_s']); self.p_dispatch.setValue(self.params['dispatch_period_s']); self.p_closed.setChecked(self.params['closed_loop'])
        self.core=self.core.__class__(self.config())
        if hasattr(self,'runtime_timer'): self.set_runtime_timer()

    def save_params(self) -> None:
        values={'wind_min_kw':self.p_wind_min.value(),'wind_max_kw':self.p_wind_max.value(),'diesel_max_kw':self.p_diesel_max.value(),'reserve_kw':self.p_reserve.value(),'max_age_s':self.p_age.value(),'poll_period_s':self.p_poll.value(),'dispatch_period_s':self.p_dispatch.value(),'closed_loop':self.p_closed.isChecked()}
        if values['wind_max_kw'] < values['wind_min_kw']:
            QtWidgets.QMessageBox.warning(self,'参数错误','风电容量上限必须不小于风电最小目标。'); return
        if values['diesel_max_kw'] < values['reserve_kw'] or values['reserve_kw'] <= 0:
            QtWidgets.QMessageBox.warning(self,'参数错误','柴油容量上限必须不小于 reserve，且 reserve 必须大于 0。'); return
        try:
            self._ensure_db(); self.repo.set_parameters(wind_min_kw=values['wind_min_kw'],wind_max_kw=values['wind_max_kw'],diesel_max_kw=values['diesel_max_kw'],reserve_kw=values['reserve_kw']); self.repo.set_runtime_config(poll_period_s=values['poll_period_s'],dispatch_period_s=values['dispatch_period_s'],closed_loop=values['closed_loop'])
            self.params.update(values); self.core=self.core.__class__(self.config()); self.set_runtime_timer()
            if self.client: self.client.state_poll_period_s=values['poll_period_s']
            self.log('B 自有参数已写入 ems.db；C 物理参数未被写入'); self.statusBar().showMessage('B EMS 参数已更新并写入 ems.db')
        except Exception as exc:
            self.log(f'参数写库失败：{exc}'); QtWidgets.QMessageBox.warning(self,'参数写库失败',str(exc))

    def history_page(self) -> QtWidgets.QWidget:
        w=QtWidgets.QWidget(); l=QtWidgets.QVBoxLayout(w); l.setContentsMargins(22,18,22,18); l.addWidget(self.section('历史数据'))
        bar=self.make_card(); g=QtWidgets.QGridLayout(bar); g.addWidget(QtWidgets.QLabel('Session'),0,0); self.h_session=QtWidgets.QLineEdit(); g.addWidget(self.h_session,0,1); g.addWidget(QtWidgets.QLabel('最多行数'),0,2); self.h_limit=QtWidgets.QSpinBox(); self.h_limit.setRange(10,5000); self.h_limit.setValue(200); g.addWidget(self.h_limit,0,3)
        q=QtWidgets.QPushButton('查询'); q.setProperty('kind','primary'); q.clicked.connect(self.refresh_history); ex=QtWidgets.QPushButton('导出 SCADA CSV'); ex.setProperty('kind','secondary'); ex.clicked.connect(self.export_history); g.addWidget(q,0,4); g.addWidget(ex,0,5); l.addWidget(bar)
        tabs=QtWidgets.QTabWidget()
        self.history=QtWidgets.QTableWidget(0,10); self.history.setHorizontalHeaderLabels(['时间','Session','Step','Load','Wind Avail','Wind Limit','Wind Target','Wind Actual','Diesel Target','Diesel Actual']); self.history.setAlternatingRowColors(True); self.history.horizontalHeader().setStretchLastSection(True); tabs.addTab(self.history,'SCADA 状态历史')
        self.dispatch_history=QtWidgets.QTableWidget(0,8); self.dispatch_history.setHorizontalHeaderLabels(['创建时间','Step','Seq','Wind Target','Diesel Target','Status','ACK','ACK Reason']); self.dispatch_history.setAlternatingRowColors(True); self.dispatch_history.horizontalHeader().setStretchLastSection(True); tabs.addTab(self.dispatch_history,'调度命令历史')
        self.log_history=QtWidgets.QTableWidget(0,4); self.log_history.setHorizontalHeaderLabels(['时间','Level','Event Type','Message']); self.log_history.setAlternatingRowColors(True); self.log_history.horizontalHeader().setStretchLastSection(True); tabs.addTab(self.log_history,'运行日志')
        l.addWidget(tabs,1); return w

    def refresh_history(self) -> None:
        try:
            self._ensure_db(); session=self.h_session.text().strip(); limit=int(self.h_limit.value())
            with self.repo.connection() as conn:
                where=' WHERE session_id=?' if session else ''; args=[session] if session else []
                state_rows=conn.execute('SELECT received_at_utc,session_id,step,load_power_kw,wind_available_kw,wind_operating_limit_kw,wind_target_kw,wind_actual_kw,diesel_target_kw,diesel_actual_kw FROM state_history'+where+' ORDER BY id DESC LIMIT ?',args+[limit]).fetchall()
                cmd_rows=conn.execute('SELECT created_at_utc,step,seq,wind_target_kw,diesel_target_kw,status,ack_accepted,ack_reason FROM dispatch_commands'+where+' ORDER BY id DESC LIMIT ?',args+[limit]).fetchall()
                log_where=' WHERE session_id=?' if session else ''; log_args=[session] if session else []
                log_rows=conn.execute('SELECT created_at_utc,level,event_type,message FROM event_log'+log_where+' ORDER BY id DESC LIMIT ?',log_args+[limit]).fetchall()
            for table,rows in ((self.history,state_rows),(self.dispatch_history,cmd_rows),(self.log_history,log_rows)):
                table.setRowCount(len(rows))
                for i,row in enumerate(rows):
                    for j,value in enumerate(row): table.setItem(i,j,QtWidgets.QTableWidgetItem(str(value)))
        except Exception as exc: self.log(f'历史查询失败：{exc}')

    def export_history(self) -> None:
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,'导出状态历史 CSV','ems_state_history.csv','CSV (*.csv)')
        if not path: return
        try:
            self._ensure_db()
            with self.repo.connection() as conn: rows=conn.execute('SELECT * FROM state_history ORDER BY id ASC').fetchall()
            with open(path,'w',newline='',encoding='utf-8-sig') as f:
                writer=csv.writer(f); writer.writerow(rows[0].keys() if rows else ['no_data']); writer.writerows([list(row) for row in rows])
            self.log(f'已导出：{path}')
        except Exception as exc: QtWidgets.QMessageBox.warning(self,'导出失败',str(exc))

    def save_state_best_effort(self) -> None:
        if self.state is None: return
        try: self._ensure_db(); self.repo.save_state(self.state)
        except Exception as exc: self.log(f'状态本地落库失败：{exc}')

    def _try_send_queued_dispatch(self) -> None:
        if not self._manual_dispatch_pending: return
        c=self.client
        if not c or not c.connected: self._cancel_queued_dispatch('A 已断开，取消排队的 dispatch',show_message=False); return
        if c._uncertain_dispatch_seq is not None: self._cancel_queued_dispatch(f'ACK 未知 seq={c._uncertain_dispatch_seq}，不自动重发',show_message=False); return
        if c._pending_state_request_seq is not None or c._pending_ack_seq is not None: return
        if self.state is None: self.log('最新 state 尚未进入 GUI，继续等待'); return
        try:
            self.calculate_current()
            if self.last_decision is None: self._cancel_queued_dispatch('最新 state 无法生成有效 EMS decision',show_message=False); return
            decision=self.last_decision; created_at=utc_now(); seq=c.send_dispatch(decision); ack=c.get_ack(seq)
            if ack is not None:
                self.last_ack=ack; status='accepted' if ack.accepted else 'rejected'; ack_received_at=utc_now()
                command_id=self.repo.record_command({'session_id':decision.state.session_id,'step':decision.state.step,'sim_time_s':decision.state.sim_time_s,'source':'B','seq':seq,'wind_target_kw':decision.result.wind_target_kw,'diesel_target_kw':decision.result.diesel_target_kw,'wind_enable':decision.result.wind_enable,'diesel_enable':decision.result.diesel_enable,'status':status,'reason':decision.result.reason,'ack_accepted':ack.accepted,'ack_reason':ack.reason,'ack_received_at_utc':ack_received_at,'created_at_utc':created_at})
                self.repo.record_evaluation(command_id=command_id,target_unserved_kw=decision.result.target_unserved_kw,target_surplus_kw=decision.result.target_surplus_kw)
                self.repo.record_log('INFO' if ack.accepted else 'WARNING','dispatch_ack',f'dispatch seq={seq} status={status} reason={ack.reason}',session_id=decision.state.session_id,step=decision.state.step)
                self.handle_ack(ack)
            else:
                self.repo.record_command({'session_id':decision.state.session_id,'step':decision.state.step,'sim_time_s':decision.state.sim_time_s,'source':'B','seq':seq,'wind_target_kw':decision.result.wind_target_kw,'diesel_target_kw':decision.result.diesel_target_kw,'wind_enable':decision.result.wind_enable,'diesel_enable':decision.result.diesel_enable,'status':'sent','reason':decision.result.reason,'ack_accepted':None,'ack_reason':None,'ack_received_at_utc':None,'created_at_utc':created_at})
                self.repo.record_log('WARNING','dispatch_sent_no_ack',f'dispatch seq={seq} sent without confirmed ACK',session_id=decision.state.session_id,step=decision.state.step)
            self.log(f'发送 dispatch seq={seq}'+(f'，ACK={ack.accepted}' if ack else '，ACK 未确认')); self._manual_dispatch_pending=False; self.send_btn.setEnabled(True); self.send_btn.setText('下发当前调度'); self.statusBar().showMessage(f'调度已下发 seq={seq}'+(f' / ACK={ack.accepted}' if ack else ''))
        except DispatchDeliveryUnknown as exc:
            decision=self.last_decision
            if decision is not None:
                try:
                    self.repo.record_command({'session_id':decision.state.session_id,'step':decision.state.step,'sim_time_s':decision.state.sim_time_s,'source':'B','seq':exc.seq,'wind_target_kw':decision.result.wind_target_kw,'diesel_target_kw':decision.result.diesel_target_kw,'wind_enable':decision.result.wind_enable,'diesel_enable':decision.result.diesel_enable,'status':'delivery_unknown','reason':str(exc),'ack_accepted':None,'ack_reason':None,'ack_received_at_utc':None,'created_at_utc':created_at})
                    self.repo.record_log('ERROR','dispatch_delivery_unknown',str(exc),session_id=decision.state.session_id,step=decision.state.step)
                except Exception as db_exc: self.log(f'delivery_unknown 写库失败：{db_exc}')
            self.last_ack=None; self._manual_dispatch_pending=False; self.send_btn.setEnabled(True); self.send_btn.setText('下发当前调度'); self.log(f'ACK 未知：seq={exc.seq}，禁止盲目重发'); QtWidgets.QMessageBox.warning(self,'ACK 未知','指令可能已到达 A，但 ACK 无法确认。请先恢复连接并核对历史，不要直接重发。')
        except ProtocolError as exc:
            if c._pending_state_request_seq is not None or c._pending_ack_seq is not None: self.log(f'dispatch 等待中：{exc}'); return
            self._cancel_queued_dispatch(f'dispatch 失败：{exc}',show_message=True)
        except Exception as exc: self._cancel_queued_dispatch(f'dispatch 失败：{exc}',show_message=True)

    def handle_ack(self, ack) -> None:
        super().handle_ack(ack)
        try:
            session_id=self.state.session_id if self.state else None; step=self.state.step if self.state else None; self._ensure_db(); self.repo.record_log('INFO' if ack.accepted else 'WARNING','ack_received',f'ACK seq={ack.ack_seq} accepted={ack.accepted} reason={ack.reason}',session_id=session_id,step=step)
        except Exception as exc: self.log(f'ACK 日志写库失败：{exc}')

    def refresh_alarms(self) -> None:
        if not hasattr(self,'alarm_table'): return
        rows=[]
        if self.state and self.state.fault: rows.append(('HIGH','C_FAULT','C protection/control priority，B 抑制风机目标',self.state.session_id,self.state.step))
        if self.state and self.state.received_age_s > self.params['max_age_s']: rows.append(('HIGH','STALE_STATE',f'state age={self.state.received_age_s:.2f}s',self.state.session_id,self.state.step))
        if self.client and self.client._uncertain_dispatch_seq is not None: rows.append(('HIGH','DELIVERY_UNKNOWN',f'dispatch seq={self.client._uncertain_dispatch_seq} ACK unknown',self.state.session_id if self.state else '',self.state.step if self.state else ''))
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
        w=QtWidgets.QWidget(); l=QtWidgets.QVBoxLayout(w); l.setContentsMargins(22,18,22,18); l.addWidget(self.section('报警与评价'))
        top=QtWidgets.QHBoxLayout(); self.eval_overall=self._eval_card('综合评价','/100'); self.eval_ems=self._eval_card('EMS 调度评分','/100'); self.eval_system=self._eval_card('系统运行评分','/100'); self.eval_grade=self._eval_card('评价等级',''); [top.addWidget(card) for card in (self.eval_overall,self.eval_ems,self.eval_system,self.eval_grade)]; l.addLayout(top)
        control=self.make_card(); g=QtWidgets.QGridLayout(control); g.addWidget(QtWidgets.QLabel('评价范围'),0,0); self.eval_period=QtWidgets.QComboBox(); [self.eval_period.addItem(text,data) for text,data in [('最近 1 分钟','1m'),('最近 5 分钟','5m'),('本次 Session','session'),('全部历史','all')]]; self.eval_period.setCurrentIndex(1); self.eval_period.currentIndexChanged.connect(self.refresh_evaluation); g.addWidget(self.eval_period,0,1); refresh=QtWidgets.QPushButton('刷新评价'); refresh.setProperty('kind','primary'); refresh.clicked.connect(self.refresh_evaluation); g.addWidget(refresh,0,2); self.eval_hint=QtWidgets.QLabel('综合评价仅由五项指标组成：供需平衡 30% · 风能利用 25% · 柴油经济性 15% · 运行约束 15% · 调度跟踪 15%'); self.eval_hint.setProperty('hint',True); self.eval_hint.setWordWrap(True); g.addWidget(self.eval_hint,0,3,1,3); l.addWidget(control)
        scores=self.make_card(); sg=QtWidgets.QGridLayout(scores); labels=[('balance','供需平衡'),('wind','风能利用'),('diesel','柴油经济性'),('constraint','运行约束'),('tracking','调度跟踪')]; self.eval_score_labels={}; self.eval_rule_combos={}
        for i,(key,title) in enumerate(labels):
            row=(i//3)*2; col=i%3; title_box=QtWidgets.QHBoxLayout(); title_box.setContentsMargins(0,0,0,0); title_label=QtWidgets.QLabel(f'{title} · {int(EVALUATION_WEIGHTS[key]*100)}%'); combo=QtWidgets.QComboBox(); combo.setMinimumWidth(190); combo.addItem('查看评分标准'); [combo.addItem(rule) for rule in SCORING_RULES[key]['rules']]; title_box.addWidget(title_label,1); title_box.addWidget(combo); title_widget=QtWidgets.QWidget(); title_widget.setLayout(title_box); sg.addWidget(title_widget,row,col); value=QtWidgets.QLabel('-- / 100'); value.setProperty('value',True); self.eval_score_labels[key]=value; sg.addWidget(value,row+1,col); self.eval_rule_combos[key]=combo
        l.addWidget(scores)
        detail=self.make_card(); dg=QtWidgets.QGridLayout(detail); detail_items=[('samples','评价样本'),('balance_error','平均功率不平衡'),('wind_util','风能利用率'),('diesel_share','柴油供电占比'),('unserved','平均未供电功率'),('surplus','平均过剩功率'),('violations','运行约束违反'),('tracking_error','平均目标跟踪误差'),('dispatches','调度次数')]; self.eval_detail_labels={}
        for i,(key,title) in enumerate(detail_items): dg.addWidget(QtWidgets.QLabel(title),i//5*2,i%5); value=QtWidgets.QLabel('--'); value.setProperty('cardtitle',True); self.eval_detail_labels[key]=value; dg.addWidget(value,i//5*2+1,i%5)
        l.addWidget(detail); self.eval_table=QtWidgets.QTableWidget(0,5); self.eval_table.setHorizontalHeaderLabels(['评价项','权重','评分','当前指标','说明']); self.eval_table.horizontalHeader().setStretchLastSection(True); self.eval_table.setAlternatingRowColors(True); l.addWidget(self.eval_table,1); return w

    def _eval_card(self,title:str,unit:str):
        card=self.make_card(); box=QtWidgets.QVBoxLayout(card); box.setContentsMargins(14,10,14,10); t=QtWidgets.QLabel(title); t.setProperty('cardtitle',True); value=QtWidgets.QLabel('--'); value.setProperty('value',True); hint=QtWidgets.QLabel(unit); hint.setProperty('hint',True); box.addWidget(t); box.addWidget(value); box.addWidget(hint); card.value=value; return card

    def refresh_evaluation(self) -> None:
        if not hasattr(self,'eval_overall'): return
        try: self._show_evaluation(evaluate_repository(self.repo,self.eval_period.currentData() or '5m'))
        except Exception as exc: self.log(f'调度评价计算失败：{exc}'); self.eval_hint.setText(f'评价暂不可用：{exc}')

    def _show_evaluation(self,r:EvaluationResult) -> None:
        self.eval_overall.value.setText(f'{r.overall_score:.1f}'); self.eval_ems.value.setText(f'{r.ems_score:.1f}'); self.eval_system.value.setText(f'{r.system_score:.1f}'); self.eval_grade.value.setText(r.grade)
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
