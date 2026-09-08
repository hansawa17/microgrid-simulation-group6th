# -*- coding: utf-8 -*-
"""B / EMS PyQt6 SCADA GUI.

B 是闭环 EMS 主站和 A 的 TCP client。本界面保留原有 A IP/端口、TCP state/dispatch/ACK、
实时监控、曲线、参数、历史、报警等功能，并补充课程要求中的：
- 本地场景/手动调度验证；
- 功率不平衡、柴油备用余量、状态新鲜度；
- ems.db 历史读取与 CSV 导出；
- 通信诊断、full-sync/未决请求/ACK 状态；
- 自动闭环调度开关；
- 调度评价 KPI 与数据库事件回放。

本地演示输入明确标记为 mock，不替代 A 的正式场景曲线。B 只发送 target/enable，不发送 pitch。
"""
from __future__ import annotations

import csv
import math
import socket
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from PyQt6 import QtCore, QtGui, QtWidgets

try:
    from B_dispatch.models import DispatchConfig, DispatchResult, GridState
    from B_dispatch.operator_core import EMSCore
    from B_dispatch.tcpB import Ack, DispatchDeliveryUnknown, EMSTcpClient, ProtocolError
except ImportError:
    from .models import DispatchConfig, DispatchResult, GridState
    from .operator_core import EMSCore
    from .tcpB import Ack, DispatchDeliveryUnknown, EMSTcpClient, ProtocolError

COLORS={"bg":"#e9eff6","surface":"#fff","border":"#d7e2ed","nav":"#f6f9fc","primary":"#2f6fd6","dark":"#16324f","text":"#274056","soft":"#45607a","muted":"#8b9caf","good":"#1fa15a","warn":"#e19a1a","bad":"#d64545","load":"#355a78","avail":"#1fa15a","limit":"#2b93b5","target":"#e19a1a","actual":"#6b4fd8","diesel":"#8a5a2b"}
QSS=f"""
QMainWindow{{background:{COLORS['bg']};}} QWidget{{font-family:'Microsoft YaHei','Segoe UI';color:{COLORS['text']};}}
#header{{background:#fff;border-bottom:1px solid {COLORS['border']};}} #logo{{background:{COLORS['primary']};border-radius:6px;color:#fff;font-size:19px;font-weight:800;}}
#title{{color:{COLORS['dark']};font-size:20px;font-weight:700;}} #subtitle{{color:{COLORS['muted']};font-size:12px;}}
#nav{{background:{COLORS['nav']};border-right:1px solid #dbe5ef;}}
QPushButton[nav='true']{{background:transparent;border:none;text-align:left;border-radius:7px;padding:12px 18px;color:#33516d;font-size:14px;font-weight:600;}}
QPushButton[nav='true']:hover{{background:#e8f0f8;}} QPushButton[nav='true']:checked{{background:{COLORS['primary']};color:#fff;}}
QFrame[card='true']{{background:#fff;border:1px solid {COLORS['border']};border-radius:8px;}}
QLabel[section='true']{{color:{COLORS['dark']};font-size:19px;font-weight:700;}} QLabel[cardtitle='true']{{color:{COLORS['soft']};font-size:13px;font-weight:600;}}
QLabel[value='true']{{color:#123a5f;font-size:26px;font-weight:700;}} QLabel[hint='true']{{color:{COLORS['muted']};font-size:11px;}}
QPushButton[kind='primary']{{background:{COLORS['primary']};color:#fff;border:none;border-radius:6px;padding:9px 18px;font-weight:600;}}
QPushButton[kind='success']{{background:{COLORS['good']};color:#fff;border:none;border-radius:6px;padding:9px 18px;font-weight:600;}}
QPushButton[kind='danger']{{background:{COLORS['bad']};color:#fff;border:none;border-radius:6px;padding:9px 18px;font-weight:600;}}
QPushButton[kind='secondary']{{background:#eef3f8;color:#33516d;border:1px solid #d2dfea;border-radius:6px;padding:9px 18px;font-weight:600;}}
QLineEdit,QComboBox,QDoubleSpinBox,QSpinBox{{background:#fff;border:1px solid #c9d8e6;border-radius:5px;padding:6px 8px;min-height:20px;}}
QTableWidget{{background:#fff;border:1px solid #d5e2ed;gridline-color:#e4ecf3;alternate-background-color:#f7fafd;}}
QHeaderView::section{{background:#edf3f9;color:#33516d;font-weight:700;border:none;border-bottom:1px solid #d5e2ed;padding:8px;}}
QStatusBar{{background:#e6edf5;color:#5e7891;}}
"""

def local_ip():
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8',80)); return s.getsockname()[0]
    except OSError:
        try:return socket.gethostbyname(socket.gethostname())
        except OSError:return '未知'

def utc_now():return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')

class ValueCard(QtWidgets.QFrame):
    def __init__(self,title,unit=''):
        super().__init__();self.setProperty('card',True);l=QtWidgets.QVBoxLayout(self);l.setContentsMargins(15,12,15,12)
        t=QtWidgets.QLabel(title);t.setProperty('cardtitle',True);self.value=QtWidgets.QLabel('--');self.value.setProperty('value',True);u=QtWidgets.QLabel(unit);u.setProperty('hint',True);l.addWidget(t);l.addWidget(self.value);l.addWidget(u)
    def set(self,v):self.value.setText(str(v))

class TrendChart(QtWidgets.QWidget):
    def __init__(self,names:Iterable[str]):super().__init__();self.names=list(names);self.data={n:[] for n in self.names};self.setMinimumHeight(285)
    def push(self,values,max_points=120):
        for n in self.names:
            self.data[n].append(float(values.get(n,math.nan)))
            if len(self.data[n])>max_points:del self.data[n][:-max_points]
        self.update()
    def clear(self):
        for a in self.data.values():a.clear()
        self.update()
    def paintEvent(self,e):
        p=QtGui.QPainter(self);p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing);r=self.rect().adjusted(58,18,-18,-48);p.fillRect(self.rect(),QtGui.QColor('#f7fafd'));p.setPen(QtGui.QPen(QtGui.QColor('#dde6ef'),1))
        for i in range(6):p.drawLine(r.left(),int(r.top()+r.height()*i/5),r.right(),int(r.top()+r.height()*i/5))
        vals=[v for a in self.data.values() for v in a if math.isfinite(v)]
        if not vals:p.setPen(QtGui.QColor(COLORS['muted']));p.drawText(r,QtCore.Qt.AlignmentFlag.AlignCenter,'等待状态数据 / 本地演示数据');return
        lo,hi=min(vals),max(vals)
        if hi-lo<1e-9:lo-=1;hi+=1
        pal=[COLORS['load'],COLORS['avail'],COLORS['limit'],COLORS['target'],COLORS['actual'],COLORS['diesel']]
        for k,n in enumerate(self.names):
            pts=[];a=self.data[n]
            for i,v in enumerate(a):
                if not math.isfinite(v):continue
                x=r.left()+r.width()*i/max(1,len(a)-1);y=r.bottom()-r.height()*(v-lo)/(hi-lo);pts.append(QtCore.QPointF(x,y))
            if len(pts)>1:p.setPen(QtGui.QPen(QtGui.QColor(pal[k%len(pal)]),2));p.drawPolyline(QtGui.QPolygonF(pts))
        p.setPen(QtGui.QColor(COLORS['muted']));p.drawText(5,r.top(),45,18,QtCore.Qt.AlignmentFlag.AlignRight,f'{hi:.0f}');p.drawText(5,r.bottom()-18,45,18,QtCore.Qt.AlignmentFlag.AlignRight,f'{lo:.0f}')
        x=62;y=self.height()-20
        for k,n in enumerate(self.names):
            p.setPen(QtGui.QPen(QtGui.QColor(pal[k%len(pal)]),3));p.drawLine(x,y,x+14,y);p.setPen(QtGui.QColor(COLORS['soft']));p.drawText(x+18,y+4,n);x+=max(80,len(n)*9+40)

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle('南极孤立微电网 EMS 主站 B');self.resize(1920,1080);self.setMinimumSize(1400,800);self.setStyleSheet(QSS)
        self.client=None;self.state=None;self.core=None;self.last_decision=None;self.last_ack=None;self.auto_dispatch=False
        self.db_path=Path(__file__).resolve().parents[1]/'data'/'runtime'/'ems.db';self.demo_mode=True
        self.params={'wind_min_kw':0.0,'wind_max_kw':100.0,'diesel_max_kw':120.0,'reserve_kw':10.0,'max_age_s':2.0}
        self.build_ui();self.build_timers();self.set_connection('A 未连接','warn');self.log('GUI 启动：本地演示模式；可配置 A IP 后进入 TCP 模式')
    def card(self):x=QtWidgets.QFrame();x.setProperty('card',True);return x
    def section(self,t):x=QtWidgets.QLabel(t);x.setProperty('section',True);return x
    def build_ui(self):
        c=QtWidgets.QWidget();self.setCentralWidget(c);root=QtWidgets.QVBoxLayout(c);root.setContentsMargins(0,0,0,0);root.setSpacing(0);root.addWidget(self.header())
        body=QtWidgets.QHBoxLayout();body.setContentsMargins(0,0,0,0);body.setSpacing(0);body.addWidget(self.nav());self.stack=QtWidgets.QStackedWidget()
        for f in (self.monitor_page,self.curve_page,self.demo_page,self.dispatch_page,self.history_page,self.comm_page,self.alarm_page):self.stack.addWidget(f())
        body.addWidget(self.stack,1);root.addLayout(body,1);self.group=QtWidgets.QButtonGroup(self);self.group.setExclusive(True)
        for i,b in enumerate(self.nav_buttons):self.group.addButton(b,i);b.clicked.connect(lambda _,j=i:self.stack.setCurrentIndex(j))
        self.nav_buttons[0].setChecked(True);self.statusBar().showMessage('B EMS 就绪')
    def header(self):
        bar=QtWidgets.QFrame();bar.setObjectName('header');bar.setFixedHeight(72);l=QtWidgets.QHBoxLayout(bar);l.setContentsMargins(20,0,20,0);logo=QtWidgets.QLabel('B');logo.setObjectName('logo');logo.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter);logo.setFixedSize(38,38);l.addWidget(logo)
        t=QtWidgets.QVBoxLayout();a=QtWidgets.QLabel('南极孤立微电网 EMS 主站');a.setObjectName('title');b=QtWidgets.QLabel('PC-B · 能量管理与调度 · Closed Loop');b.setObjectName('subtitle');t.addWidget(a);t.addWidget(b);l.addLayout(t);l.addStretch();self.clock=QtWidgets.QLabel();l.addWidget(self.clock);self.a_status=QtWidgets.QLabel('A 未连接');l.addWidget(self.a_status);self.mode_status=QtWidgets.QLabel('本地演示');l.addWidget(self.mode_status);l.addWidget(QtWidgets.QLabel('C 优先'));return bar
    def nav(self):
        p=QtWidgets.QFrame();p.setObjectName('nav');p.setFixedWidth(205);l=QtWidgets.QVBoxLayout(p);l.setContentsMargins(0,16,0,16);cap=QtWidgets.QLabel('功能导航');cap.setProperty('hint',True);l.addWidget(cap);self.nav_buttons=[]
        for text in ['运行监控','实时曲线','本地场景 / 手动调度','EMS 调度','历史数据','通信诊断','报警与评价']:
            b=QtWidgets.QPushButton(text);b.setProperty('nav',True);b.setCheckable(True);self.nav_buttons.append(b);l.addWidget(b)
        l.addStretch();s=QtWidgets.QLabel('B / EMS\nTCP Client → A Server\nReserve = 10 kW\nC protection priority');s.setProperty('hint',True);l.addWidget(s);return p
    def monitor_page(self):
        w=QtWidgets.QWidget();l=QtWidgets.QVBoxLayout(w);l.setContentsMargins(22,18,22,18);l.addWidget(self.section('运行监控'));con=self.card();g=QtWidgets.QGridLayout(con);g.setContentsMargins(14,12,14,12);g.addWidget(QtWidgets.QLabel('A 服务器连接'),0,0,1,2);g.addWidget(QtWidgets.QLabel('A 可达 IP'),1,0);self.host=QtWidgets.QLineEdit('127.0.0.1');self.host.setPlaceholderText('例如 192.168.1.100');g.addWidget(self.host,1,1);g.addWidget(QtWidgets.QLabel('TCP 端口'),1,2);self.port=QtWidgets.QSpinBox();self.port.setRange(1,65535);self.port.setValue(5000);g.addWidget(self.port,1,3);self.connect_btn=QtWidgets.QPushButton('连接 A 服务器');self.connect_btn.setProperty('kind','success');self.connect_btn.clicked.connect(self.toggle_connection);g.addWidget(self.connect_btn,1,4);g.addWidget(QtWidgets.QLabel(f'本机 IP：{local_ip()}'),0,4,1,2,QtCore.Qt.AlignmentFlag.AlignRight);l.addWidget(con)
        grid=QtWidgets.QGridLayout();self.cards={};specs=[('load','负荷','kW'),('avail','风电 Available','kW'),('limit','风电 Operating Limit','kW'),('wind_actual','风电 Actual','kW'),('diesel_actual','柴油 Actual','kW'),('wind_target','风电 Target','kW'),('diesel_target','柴油 Target','kW'),('imbalance','功率不平衡','kW'),('wind_speed','风速','m/s'),('diesel_headroom','柴油备用余量','kW'),('state_age','状态年龄','s'),('pitch','桨距 Actual','deg')]
        for i,s in enumerate(specs):self.cards[s[0]]=ValueCard(s[1],s[2]);grid.addWidget(self.cards[s[0]],i//4,i%4)
        l.addLayout(grid);info=self.card();f=QtWidgets.QFormLayout(info);self.session=QtWidgets.QLabel('--');self.simtime=QtWidgets.QLabel('--');self.sampled=QtWidgets.QLabel('--');self.wind_state=QtWidgets.QLabel('--');self.last_reason=QtWidgets.QLabel('--');f.addRow('Session / Step',self.session);f.addRow('Simulation Time',self.simtime);f.addRow('A sampled_at_utc',self.sampled);f.addRow('风机状态 / Fault',self.wind_state);f.addRow('最近调度',self.last_reason);l.addWidget(info);l.addStretch();return w
    def curve_page(self):
        w=QtWidgets.QWidget();l=QtWidgets.QVBoxLayout(w);l.setContentsMargins(22,18,22,18);l.addWidget(self.section('实时曲线'));c=self.card();q=QtWidgets.QVBoxLayout(c);self.chart=TrendChart(['Load','Available','Operating Limit','Wind Target','Wind Actual','Diesel Actual']);q.addWidget(self.chart);l.addWidget(c,1);row=QtWidgets.QHBoxLayout();b=QtWidgets.QPushButton('清空曲线');b.setProperty('kind','secondary');b.clicked.connect(self.chart.clear);r=QtWidgets.QPushButton('立即请求 A 状态');r.setProperty('kind','primary');r.clicked.connect(self.request_state);row.addWidget(b);row.addWidget(r);row.addStretch();l.addLayout(row);return w
    def demo_page(self):
        w=QtWidgets.QWidget();l=QtWidgets.QVBoxLayout(w);l.setContentsMargins(22,18,22,18);l.addWidget(self.section('本地场景 / 手动调度'));n=QtWidgets.QLabel('仅用于 B 算法验证：输入明确标记为 mock，不替代 A 正式场景曲线。联网后以 A state 为唯一状态源。');n.setProperty('hint',True);l.addWidget(n);c=self.card();f=QtWidgets.QFormLayout(c)
        self.dw=self.spin(0,50,8);self.da=self.spin(0,100,60);self.dl=self.spin(0,100,60);self.dload=self.spin(0,300,100);self.dwa=self.spin(0,100,0);self.dda=self.spin(0,120,0);self.df=QtWidgets.QCheckBox('模拟 C fault / protection');self.dr=QtWidgets.QCheckBox('模拟风机运行');self.dr.setChecked(True)
        for a,b in [('风速 (m/s)',self.dw),('Available (kW)',self.da),('Operating Limit (kW)',self.dl),('负荷 (kW)',self.dload),('Wind Actual (kW)',self.dwa),('Diesel Actual (kW)',self.dda)]:f.addRow(a,b)
        f.addRow('Fault',self.df);f.addRow('Wind Running',self.dr);l.addWidget(c);row=QtWidgets.QHBoxLayout();x=QtWidgets.QPushButton('本地计算 Dispatch');x.setProperty('kind','primary');x.clicked.connect(self.demo_calculate);y=QtWidgets.QPushButton('设为当前状态');y.setProperty('kind','secondary');y.clicked.connect(self.demo_set_current);row.addWidget(x);row.addWidget(y);row.addStretch();l.addLayout(row);self.demo_result=QtWidgets.QPlainTextEdit();self.demo_result.setReadOnly(True);l.addWidget(self.demo_result,1);return w
    def dispatch_page(self):
        w=QtWidgets.QWidget();l=QtWidgets.QVBoxLayout(w);l.setContentsMargins(22,18,22,18);l.addWidget(self.section('EMS 调度'));row=QtWidgets.QHBoxLayout();self.req=QtWidgets.QPushButton('请求 A 状态');self.req.setProperty('kind','secondary');self.req.clicked.connect(self.request_state);self.calc_send=QtWidgets.QPushButton('计算并发送 Dispatch');self.calc_send.setProperty('kind','primary');self.calc_send.clicked.connect(self.dispatch_now);self.auto=QtWidgets.QCheckBox('自动闭环：每 5 s 决策一次');self.auto.stateChanged.connect(lambda v:setattr(self,'auto_dispatch',bool(v)));row.addWidget(self.req);row.addWidget(self.calc_send);row.addWidget(self.auto);row.addStretch();l.addLayout(row)
        r=self.card();f=QtWidgets.QFormLayout(r);self.out_wind=QtWidgets.QLabel('--');self.out_diesel=QtWidgets.QLabel('--');self.out_unserved=QtWidgets.QLabel('--');self.out_surplus=QtWidgets.QLabel('--');self.out_reason=QtWidgets.QLabel('--');self.out_ack=QtWidgets.QLabel('--')
        for a,b in [('Wind Target',self.out_wind),('Diesel Target',self.out_diesel),('Target Unserved',self.out_unserved),('Target Surplus',self.out_surplus),('Reason',self.out_reason),('ACK',self.out_ack)]:f.addRow(a,b)
        l.addWidget(r);p=self.card();pf=QtWidgets.QFormLayout(p);pf.addRow('风电约束','0 ≤ target ≤ C 发布的 operating_limit');pf.addRow('柴油常规上限','diesel_max - 10 kW');pf.addRow('柴油 OFF','target = 0 时 diesel_enable=false');pf.addRow('C 优先','fault / operating limit 优先于 B 正常请求');pf.addRow('Pitch','B 不发送 pitch_target_deg');pf.addRow('ACK','accepted 只表示 A 接收，actual 必须看后续 state');l.addWidget(p);l.addStretch();return w
    def history_page(self):
        w=QtWidgets.QWidget();l=QtWidgets.QVBoxLayout(w);l.setContentsMargins(22,18,22,18);l.addWidget(self.section('历史数据 / 可追溯'));row=QtWidgets.QHBoxLayout();self.db_label=QtWidgets.QLabel(f'数据库：{self.db_path}');self.db_label.setProperty('hint',True);load=QtWidgets.QPushButton('读取 ems.db');load.setProperty('kind','secondary');load.clicked.connect(self.load_db_history);exp=QtWidgets.QPushButton('导出 CSV');exp.setProperty('kind','primary');exp.clicked.connect(self.export_history);row.addWidget(self.db_label);row.addStretch();row.addWidget(load);row.addWidget(exp);l.addLayout(row);self.history=QtWidgets.QTableWidget(0,10);self.history.setHorizontalHeaderLabels(['received','session/step','sim s','Load','Avail','Limit','Wind Actual','Diesel Actual','Wind Target','Age']);self.history.horizontalHeader().setStretchLastSection(True);self.history.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers);l.addWidget(self.history,1);return w
    def comm_page(self):
        w=QtWidgets.QWidget();l=QtWidgets.QVBoxLayout(w);l.setContentsMargins(22,18,22,18);l.addWidget(self.section('通信诊断'));c=self.card();f=QtWidgets.QFormLayout(c);self.comm_tcp=QtWidgets.QLabel('--');self.comm_local=QtWidgets.QLabel(local_ip());self.comm_remote=QtWidgets.QLabel('--');self.comm_pending=QtWidgets.QLabel('--');self.comm_sync=QtWidgets.QLabel('--');self.comm_frames=QtWidgets.QLabel('0');self.comm_last_rx=QtWidgets.QLabel('--')
        for a,b in [('TCP 状态',self.comm_tcp),('本机 IP',self.comm_local),('A endpoint',self.comm_remote),('Pending state/ACK',self.comm_pending),('Full sync',self.comm_sync),('状态/ACK 帧数',self.comm_frames),('最近接收',self.comm_last_rx)]:f.addRow(a,b)
        l.addWidget(c);self.events=QtWidgets.QTableWidget(0,3);self.events.setHorizontalHeaderLabels(['UTC','级别','事件']);self.events.horizontalHeader().setStretchLastSection(True);self.events.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers);l.addWidget(self.events,1);return w
    def alarm_page(self):
        w=QtWidgets.QWidget();l=QtWidgets.QVBoxLayout(w);l.setContentsMargins(22,18,22,18);l.addWidget(self.section('报警与调度评价'));row=QtWidgets.QHBoxLayout();self.kpi_unserved=ValueCard('累计目标缺电','kW');self.kpi_surplus=ValueCard('累计目标过剩','kW');self.kpi_ack=ValueCard('ACK 接受率','%');self.kpi_fault=ValueCard('Fault 事件','count');self.kpi_state=ValueCard('状态样本','count')
        for x in [self.kpi_unserved,self.kpi_surplus,self.kpi_ack,self.kpi_fault,self.kpi_state]:row.addWidget(x)
        l.addLayout(row);self.alarm_table=QtWidgets.QTableWidget(0,4);self.alarm_table.setHorizontalHeaderLabels(['时间','级别','类型','说明']);self.alarm_table.horizontalHeader().setStretchLastSection(True);l.addWidget(self.alarm_table,1);b=QtWidgets.QPushButton('从 ems.db 刷新评价/日志');b.setProperty('kind','secondary');b.clicked.connect(self.refresh_kpi);l.addWidget(b);return w
    def spin(self,a,b,v):x=QtWidgets.QDoubleSpinBox();x.setRange(a,b);x.setDecimals(2);x.setValue(v);return x
    def build_timers(self):
        self.ct=QtCore.QTimer(self);self.ct.timeout.connect(lambda:self.clock.setText(datetime.now().strftime('%Y-%m-%d %H:%M:%S')));self.ct.start(1000);self.nt=QtCore.QTimer(self);self.nt.timeout.connect(self.poll_socket);self.nt.start(250);self.at=QtCore.QTimer(self);self.at.timeout.connect(self.auto_tick);self.at.start(5000);self.kt=QtCore.QTimer(self);self.kt.timeout.connect(self.refresh_kpi);self.kt.start(5000)
    def set_connection(self,text,level):self.a_status.setText(text);self.a_status.setStyleSheet(f'color:{COLORS[level]};font-weight:700;');self.comm_tcp.setText(text);self.comm_remote.setText(f'{self.client.host}:{self.client.port}' if self.client else '--')
    def log(self,text,level='INFO'):
        if hasattr(self,'events'):
            r=self.events.rowCount();self.events.insertRow(r);self.events.setItem(r,0,QtWidgets.QTableWidgetItem(utc_now()));self.events.setItem(r,1,QtWidgets.QTableWidgetItem(level));self.events.setItem(r,2,QtWidgets.QTableWidgetItem(text));self.events.scrollToBottom()
        self.statusBar().showMessage(text)
    def toggle_connection(self):self.disconnect_server() if self.client and self.client.connected else self.connect_server()
    def connect_server(self):
        host=self.host.text().strip();port=self.port.value()
        if not host or host=='0.0.0.0':self.log('A IP 无效：客户端必须填写 A 的可达地址','ERROR');return
        try:
            self.client=EMSTcpClient(host,port,timeout_s=2.0);self.client.connect();self.demo_mode=False;self.mode_status.setText('TCP 实时');self.connect_btn.setText('断开 A 服务器');self.set_connection(f'已连接 {host}:{port}','good');self.log(f'B → A TCP connected: {host}:{port}');self.request_state()
        except Exception as e:self.client=None;self.log(f'连接 A 失败：{e}','ERROR');self.set_connection('A 连接失败','bad')
    def disconnect_server(self):
        if self.client:
            try:self.client.close()
            except Exception:pass
        self.client=None;self.demo_mode=True;self.mode_status.setText('本地演示');self.connect_btn.setText('连接 A 服务器');self.set_connection('A 未连接','warn');self.log('已断开 A；回到本地演示模式')
    def request_state(self):
        if not self.client or not self.client.connected:self.log('当前未连接 A：请使用本地场景页','WARN');return
        try:self.client.request_state(full=self.client.needs_full_sync);self.log('发送 state_request')
        except Exception as e:self.log(f'state_request 失败：{e}','ERROR')
    def poll_socket(self):
        c=self.client
        if not c or not c.connected:return
        try:res=c.receive_once()
        except socket.timeout:return
        except (ConnectionError,OSError,ProtocolError) as e:self.log(f'TCP 中断：{e}','ERROR');self.set_connection('TCP 中断','bad');c.close();self.connect_btn.setText('连接 A 服务器');return
        for x in res:self.apply_state(x) if isinstance(x,GridState) else self.apply_ack(x)
        self.comm_pending.setText(f'state={c._pending_state_request_seq} / ack={c._pending_ack_seq}');self.comm_sync.setText(str(c.needs_full_sync))
        if c._pending_state_request_seq is None and c._pending_ack_seq is None:
            try:c.request_state(full=c.needs_full_sync)
            except Exception:pass
    def apply_state(self,s:GridState):
        self.state=s;imb=s.load_power_kw-s.wind_actual_kw-s.diesel_actual_kw;head=max(0.0,self.params['diesel_max_kw']-s.diesel_actual_kw)
        vals={'load':s.load_power_kw,'avail':s.wind_available_kw,'limit':s.wind_operating_limit_kw,'wind_actual':s.wind_actual_kw,'diesel_actual':s.diesel_actual_kw,'wind_target':s.wind_target_kw,'diesel_target':getattr(s,'diesel_target_kw',0.0),'imbalance':imb,'wind_speed':s.wind_speed_mps,'diesel_headroom':head,'state_age':s.received_age_s,'pitch':s.pitch_actual_deg or 0.0}
        for k,v in vals.items():self.cards[k].set(f'{v:.2f}')
        self.session.setText(f'{s.session_id} / {s.step}');self.simtime.setText(f'{s.sim_time_s:.2f} s');self.sampled.setText(s.sampled_at_utc or '--');self.wind_state.setText('FAULT · C PRIORITY' if s.fault else ('RUNNING' if s.wind_running else 'STOPPED'));self.last_reason.setText(self.last_decision.result.reason if self.last_decision else '--');self.chart.push({'Load':s.load_power_kw,'Available':s.wind_available_kw,'Operating Limit':s.wind_operating_limit_kw,'Wind Target':s.wind_target_kw,'Wind Actual':s.wind_actual_kw,'Diesel Actual':s.diesel_actual_kw});self.comm_last_rx.setText(utc_now());self.comm_frames.setText(str(int(self.comm_frames.text())+1));self.add_history_state(s)
        if s.fault:self.add_alarm('WARN','C_FAULT','A state fault：B 不请求正常风机出力')
        if s.received_age_s>self.params['max_age_s']:self.add_alarm('WARN','STALE_STATE',f'状态年龄 {s.received_age_s:.2f}s 超限')
    def apply_ack(self,a:Ack):
        self.last_ack=a;self.out_ack.setText(f'seq={a.ack_seq} · {"accepted" if a.accepted else "rejected"} · {a.reason}');self.comm_frames.setText(str(int(self.comm_frames.text())+1));self.log(f'ACK seq={a.ack_seq}: accepted={a.accepted}, reason={a.reason}','INFO' if a.accepted else 'WARN')
    def config(self):return DispatchConfig(wind_min_kw=self.params['wind_min_kw'],wind_max_kw=self.params['wind_max_kw'],diesel_max_kw=self.params['diesel_max_kw'],reserve_kw=10.0,max_state_age_s=self.params['max_age_s'],c_has_control_priority=True)
    def calculate(self,s):
        self.core=EMSCore(self.config());self.last_decision=self.core.decide(s);r=self.last_decision.result;self.out_wind.setText(f'{r.wind_target_kw:.2f} kW');self.out_diesel.setText(f'{r.diesel_target_kw:.2f} kW');self.out_unserved.setText(f'{r.target_unserved_kw:.2f} kW');self.out_surplus.setText(f'{r.target_surplus_kw:.2f} kW');self.out_reason.setText(r.reason);self.last_reason.setText(r.reason);return self.last_decision
    def dispatch_now(self):
        if self.state is None:self.log('没有有效 state，不能调度','WARN');return
        try:
            d=self.calculate(self.state)
            if not self.client or not self.client.connected:self.log('本地演示只计算，不发送 TCP dispatch');return
            seq=self.client.send_dispatch(d);a=self.client.get_ack(seq);self.log(f'dispatch seq={seq} 已发送；actual 等后续 state')
            if a:self.apply_ack(a)
        except DispatchDeliveryUnknown as e:self.log(f'delivery_unknown seq={e.seq}：禁止盲目重发','ERROR');self.add_alarm('ERROR','DELIVERY_UNKNOWN',str(e))
        except Exception as e:self.log(f'Dispatch 失败：{e}','ERROR')
    def auto_tick(self):
        if self.auto_dispatch and self.client and self.client.connected and self.state:self.dispatch_now()
    def demo_state(self):
        now=utc_now();return GridState(session_id='LOCAL-DEMO',step=0,sim_time_s=0.0,wind_speed_mps=self.dw.value(),wind_available_kw=self.da.value(),wind_operating_limit_kw=min(self.dl.value(),self.da.value()),load_power_kw=self.dload.value(),wind_actual_kw=self.dwa.value(),diesel_actual_kw=self.dda.value(),wind_running=self.dr.isChecked(),fault=self.df.isChecked(),received_age_s=0.0,sampled_at_utc=now,received_at_utc=now,wind_target_kw=0.0,pitch_actual_deg=0.0)
    def demo_calculate(self):
        try:
            s=self.demo_state();d=self.calculate(s);r=d.result;self.demo_result.setPlainText('\n'.join([f'LOCAL-DEMO（mock）',f'Wind target = {r.wind_target_kw:.2f} kW',f'Diesel target = {r.diesel_target_kw:.2f} kW',f'Wind enable = {r.wind_enable}',f'Diesel enable = {r.diesel_enable}',f'Target unserved = {r.target_unserved_kw:.2f} kW',f'Target surplus = {r.target_surplus_kw:.2f} kW',f'Reason = {r.reason}']));self.log('本地演示调度计算完成')
        except Exception as e:self.demo_result.setPlainText(str(e));self.log(f'本地调度失败：{e}','ERROR')
    def demo_set_current(self):
        if self.client and self.client.connected:self.log('已连接 A 时不能覆盖实时 state','WARN');return
        self.apply_state(self.demo_state());self.demo_mode=True;self.mode_status.setText('本地演示');self.log('已将本地演示状态设为当前 B state')
    def add_history_state(self,s):
        vals=[s.received_at_utc or utc_now(),f'{s.session_id}/{s.step}',f'{s.sim_time_s:.2f}',f'{s.load_power_kw:.2f}',f'{s.wind_available_kw:.2f}',f'{s.wind_operating_limit_kw:.2f}',f'{s.wind_actual_kw:.2f}',f'{s.diesel_actual_kw:.2f}',f'{s.wind_target_kw:.2f}',f'{s.received_age_s:.2f}'];r=self.history.rowCount();self.history.insertRow(r)
        for i,v in enumerate(vals):self.history.setItem(r,i,QtWidgets.QTableWidgetItem(v))
        if self.history.rowCount()>1000:self.history.removeRow(0)
    def load_db_history(self):
        if not self.db_path.exists():self.log(f'ems.db 不存在：{self.db_path}','WARN');return
        try:
            with sqlite3.connect(self.db_path) as db:rows=db.execute('SELECT received_at_utc,session_id,step,sim_time_s,load_power_kw,wind_available_kw,wind_operating_limit_kw,wind_actual_kw,diesel_actual_kw,wind_target_kw,received_age_s FROM state_history ORDER BY id DESC LIMIT 1000').fetchall()
            self.history.setRowCount(0)
            for row in reversed(rows):
                r=self.history.rowCount();self.history.insertRow(r)
                for i,v in enumerate(row):self.history.setItem(r,i,QtWidgets.QTableWidgetItem(str(v)))
            self.log(f'从 ems.db 读取 {len(rows)} 条状态历史')
        except Exception as e:self.log(f'读取 ems.db 失败：{e}','ERROR')
    def export_history(self):
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,'导出 B 状态历史','ems_state_history.csv','CSV (*.csv)')
        if not path:return
        try:
            with open(path,'w',newline='',encoding='utf-8-sig') as f:
                w=csv.writer(f);w.writerow([self.history.horizontalHeaderItem(i).text() for i in range(self.history.columnCount())]);
                for r in range(self.history.rowCount()):w.writerow([self.history.item(r,c).text() if self.history.item(r,c) else '' for c in range(self.history.columnCount())])
            self.log(f'历史数据已导出：{path}')
        except Exception as e:self.log(f'CSV 导出失败：{e}','ERROR')
    def refresh_kpi(self):
        if not self.db_path.exists():return
        try:
            with sqlite3.connect(self.db_path) as db:
                q=lambda s:db.execute(s).fetchone()[0] or 0
                self.kpi_unserved.set(f'{q("SELECT COALESCE(SUM(target_unserved_kw),0) FROM dispatch_evaluation"):.2f}');self.kpi_surplus.set(f'{q("SELECT COALESCE(SUM(target_surplus_kw),0) FROM dispatch_evaluation"):.2f}');total=q('SELECT COUNT(*) FROM dispatch_commands WHERE ack_accepted IS NOT NULL');accepted=q('SELECT COUNT(*) FROM dispatch_commands WHERE ack_accepted=1');self.kpi_ack.set(f'{100*accepted/total:.1f}' if total else '--');self.kpi_fault.set(str(q("SELECT COUNT(*) FROM event_log WHERE event_type=\'C_FAULT\'")));self.kpi_state.set(str(q('SELECT COUNT(*) FROM state_history')));logs=db.execute('SELECT created_at_utc,level,event_type,message FROM event_log ORDER BY id DESC LIMIT 100').fetchall()
            self.alarm_table.setRowCount(0)
            for row in reversed(logs):
                r=self.alarm_table.rowCount();self.alarm_table.insertRow(r)
                for i,v in enumerate(row):self.alarm_table.setItem(r,i,QtWidgets.QTableWidgetItem(str(v)))
        except Exception:pass
    def add_alarm(self,level,typ,msg):
        r=self.alarm_table.rowCount();self.alarm_table.insertRow(r)
        for i,v in enumerate([utc_now(),level,typ,msg]):self.alarm_table.setItem(r,i,QtWidgets.QTableWidgetItem(str(v)))
    def set_state_snapshot(self,state:GridState):self.apply_state(state)
    def apply_dispatch_result(self,result:DispatchResult):
        self.out_wind.setText(f'{result.wind_target_kw:.2f} kW');self.out_diesel.setText(f'{result.diesel_target_kw:.2f} kW');self.out_unserved.setText(f'{result.target_unserved_kw:.2f} kW');self.out_surplus.setText(f'{result.target_surplus_kw:.2f} kW');self.out_reason.setText(result.reason)
    def closeEvent(self,e):
        if self.client:
            try:self.client.close()
            except Exception:pass
        e.accept()

def main():
    app=QtWidgets.QApplication(sys.argv);win=MainWindow();win.show();return app.exec()
if __name__=='__main__':raise SystemExit(main())
