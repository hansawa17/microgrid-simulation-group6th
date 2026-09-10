"""B / EMS package entry point."""
from __future__ import annotations
import argparse
from pathlib import Path
import socket,subprocess,sys
DEFAULT_DB=Path(__file__).resolve().parents[1]/'data'/'runtime'/'ems.db'

def _build_parser():
 p=argparse.ArgumentParser(prog='python -m B_dispatch',description='B / EMS 主站启动入口');s=p.add_subparsers(dest='command');s.add_parser('gui');s.add_parser('wind-evaluation');s.add_parser('run');i=s.add_parser('init');i.add_argument('--db',type=Path,default=DEFAULT_DB);o=s.add_parser('io');o.add_argument('--db',type=Path,default=DEFAULT_DB);o.add_argument('--host');o.add_argument('--port',type=int);c=s.add_parser('compute');c.add_argument('--db',type=Path,default=DEFAULT_DB);return p

def _get_local_ip():
 sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
 try:
  sock.settimeout(.5);sock.connect(('8.8.8.8',80));ip=sock.getsockname()[0]
  if ip:return ip
 except OSError:pass
 finally:sock.close()
 try:
  for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
   if ip and not ip.startswith('127.'):return ip
 except OSError:pass
 return '未获取'

def _launch_gui():
 from PyQt6 import QtWidgets
 from . import gui_b
 from .wind_execution_gui import WindExecutionWindow
 from .repository import EMSRepository
 class NetworkAwareMainWindow(gui_b.MainWindow):
  def __init__(self):
   super().__init__();self._wind_eval_window=None;ip=_get_local_ip()
   if hasattr(self,'appSubtitle'):self.appSubtitle.setText(f'PC-B · 能量管理与调度系统 · 闭环 EMS · 本机 IP：{ip}')
   self.setWindowTitle(f'南极孤立微电网 EMS 主站 B · 本机 IP：{ip}')
   button=QtWidgets.QPushButton('风机指令执行评价');button.clicked.connect(self.open_wind_evaluation);self.statusBar().addPermanentWidget(button);self.statusBar().addPermanentWidget(QtWidgets.QLabel(f'本机 IP：{ip}'))
  def open_wind_evaluation(self):
   if self._wind_eval_window is None:self._wind_eval_window=WindExecutionWindow(EMSRepository(DEFAULT_DB))
   self._wind_eval_window.show();self._wind_eval_window.raise_();self._wind_eval_window.activateWindow();self._wind_eval_window.refresh()
 app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([]);w=NetworkAwareMainWindow();w.show();return app.exec()

def _launch_full_runtime():
 from .repository import EMSRepository
 EMSRepository(DEFAULT_DB).initialize();flags=getattr(subprocess,'CREATE_NO_WINDOW',0);children=[subprocess.Popen([sys.executable,'-m','B_dispatch','compute','--db',str(DEFAULT_DB)],creationflags=flags),subprocess.Popen([sys.executable,'-m','B_dispatch','io','--db',str(DEFAULT_DB)],creationflags=flags)]
 try:return _launch_gui()
 finally:
  for child in children:
   if child.poll() is None:child.terminate()
  for child in children:
   try:child.wait(timeout=3)
   except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)

def _launch_wind_evaluation():
 from PyQt6 import QtWidgets
 from .repository import EMSRepository
 from .wind_execution_gui import WindExecutionWindow
 repo=EMSRepository(DEFAULT_DB);repo.initialize();app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([]);w=WindExecutionWindow(repo);w.show();return app.exec()

def main(argv=None):
 args=_build_parser().parse_args(argv)
 if args.command in (None,'run'):return _launch_full_runtime()
 if args.command=='gui':return _launch_gui()
 if args.command=='wind-evaluation':return _launch_wind_evaluation()
 from .repository import EMSRepository
 repo=EMSRepository(args.db);repo.initialize()
 if args.command=='init':return 0
 if args.command=='compute':
  from .compute_service import EMSComputeService
  EMSComputeService(repo).run();return 0
 if args.command=='io':
  current=repo.get_communication_config();host=args.host if args.host is not None else str(current['host']);port=args.port if args.port is not None else int(current['port']);repo.set_communication_config(host=host,port=port,enabled=True)
  from .communication_service import EMSCommunicationService
  EMSCommunicationService(repo).run();return 0
 raise AssertionError('unreachable')
if __name__=='__main__':raise SystemExit(main())
