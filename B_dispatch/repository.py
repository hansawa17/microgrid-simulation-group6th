"""Local SQLite repository for B EMS state, commands and evaluations."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import asdict,is_dataclass
from datetime import datetime,timezone
from pathlib import Path
import math,sqlite3
from typing import Iterator,Mapping,Optional
from .models import DispatchConfig,DispatchResult,GridState

SCHEMA='''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS physical_parameters(id INTEGER PRIMARY KEY CHECK(id=1),wind_rated_kw REAL NOT NULL DEFAULT 100,wind_cut_in_mps REAL NOT NULL DEFAULT 3,wind_rated_speed_mps REAL NOT NULL DEFAULT 12,wind_cut_out_mps REAL NOT NULL DEFAULT 25,pitch_min_deg REAL NOT NULL DEFAULT 0,pitch_max_deg REAL NOT NULL DEFAULT 90,wind_ramp_up_kw_s REAL NOT NULL DEFAULT 40,wind_ramp_down_kw_s REAL NOT NULL DEFAULT 60,diesel_min_kw REAL NOT NULL DEFAULT 20,diesel_max_kw REAL NOT NULL DEFAULT 120,diesel_ramp_up_kw_s REAL NOT NULL DEFAULT 30,diesel_ramp_down_kw_s REAL NOT NULL DEFAULT 40,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dispatch_parameters(id INTEGER PRIMARY KEY CHECK(id=1),wind_min_kw REAL NOT NULL CHECK(wind_min_kw>=0),wind_max_kw REAL NOT NULL CHECK(wind_max_kw>=wind_min_kw),diesel_max_kw REAL NOT NULL CHECK(diesel_max_kw>=0),reserve_kw REAL NOT NULL CHECK(reserve_kw>0),updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ems_runtime_config(id INTEGER PRIMARY KEY CHECK(id=1),poll_period_s REAL NOT NULL DEFAULT 1 CHECK(poll_period_s>0),dispatch_period_s REAL NOT NULL DEFAULT 5 CHECK(dispatch_period_s>0),closed_loop INTEGER NOT NULL DEFAULT 1 CHECK(closed_loop IN(0,1)),command_timeout_s REAL CHECK(command_timeout_s IS NULL OR command_timeout_s>0),max_state_age_s REAL NOT NULL DEFAULT 2 CHECK(max_state_age_s>0),updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS current_state(id INTEGER PRIMARY KEY CHECK(id=1),session_id TEXT NOT NULL,step INTEGER NOT NULL,sim_time_s REAL NOT NULL,wind_speed_mps REAL NOT NULL,wind_available_kw REAL NOT NULL,wind_operating_limit_kw REAL NOT NULL,load_power_kw REAL NOT NULL,wind_actual_kw REAL NOT NULL,diesel_actual_kw REAL NOT NULL,wind_target_kw REAL NOT NULL,diesel_target_kw REAL NOT NULL DEFAULT 0,pitch_actual_deg REAL NOT NULL,wind_running INTEGER NOT NULL,fault INTEGER NOT NULL,diesel_running INTEGER NOT NULL DEFAULT 0,power_imbalance_kw REAL NOT NULL DEFAULT 0,sampled_at_utc TEXT NOT NULL,received_at_utc TEXT NOT NULL,received_age_s REAL NOT NULL,controller_wind_enable INTEGER,pitch_target_deg REAL,last_wind_action_seq INTEGER,last_wind_action_step INTEGER,wind_action_applied_at_utc TEXT,extension_status TEXT NOT NULL DEFAULT 'legacy_or_incomplete');
CREATE TABLE IF NOT EXISTS state_history(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,step INTEGER NOT NULL,sim_time_s REAL NOT NULL,wind_speed_mps REAL NOT NULL,wind_available_kw REAL NOT NULL,wind_operating_limit_kw REAL NOT NULL,load_power_kw REAL NOT NULL,wind_actual_kw REAL NOT NULL,diesel_actual_kw REAL NOT NULL,wind_target_kw REAL NOT NULL,diesel_target_kw REAL NOT NULL DEFAULT 0,pitch_actual_deg REAL NOT NULL,wind_running INTEGER NOT NULL,fault INTEGER NOT NULL,diesel_running INTEGER NOT NULL DEFAULT 0,power_imbalance_kw REAL NOT NULL DEFAULT 0,sampled_at_utc TEXT NOT NULL,received_at_utc TEXT NOT NULL,received_age_s REAL NOT NULL,controller_wind_enable INTEGER,pitch_target_deg REAL,last_wind_action_seq INTEGER,last_wind_action_step INTEGER,wind_action_applied_at_utc TEXT,extension_status TEXT NOT NULL DEFAULT 'legacy_or_incomplete');
CREATE TABLE IF NOT EXISTS dispatch_commands(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,step INTEGER NOT NULL,sim_time_s REAL NOT NULL,source TEXT NOT NULL,seq INTEGER NOT NULL,wind_target_kw REAL NOT NULL,diesel_target_kw REAL NOT NULL,wind_enable INTEGER NOT NULL,diesel_enable INTEGER NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL,ack_accepted INTEGER,ack_reason TEXT,ack_received_at_utc TEXT,created_at_utc TEXT NOT NULL,UNIQUE(session_id,source,seq));
CREATE TABLE IF NOT EXISTS dispatch_evaluation(id INTEGER PRIMARY KEY AUTOINCREMENT,command_id INTEGER NOT NULL REFERENCES dispatch_commands(id),target_unserved_kw REAL NOT NULL,target_surplus_kw REAL NOT NULL,actual_unserved_kw REAL,actual_surplus_kw REAL,evaluated_at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wind_execution_evaluation(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,dispatch_command_id INTEGER NOT NULL REFERENCES dispatch_commands(id),outbox_id INTEGER,c_wind_action_seq INTEGER,dispatch_step INTEGER NOT NULL,feedback_step INTEGER NOT NULL,dispatch_created_at_utc TEXT NOT NULL,feedback_sampled_at_utc TEXT NOT NULL,wind_action_applied_at_utc TEXT,response_latency_s REAL,b_wind_enable INTEGER NOT NULL,b_wind_target_kw REAL NOT NULL,c_controller_wind_enable INTEGER NOT NULL,c_pitch_target_deg REAL NOT NULL,c_wind_available_kw REAL NOT NULL,c_wind_operating_limit_kw REAL NOT NULL,a_wind_running INTEGER NOT NULL,a_wind_actual_kw REAL NOT NULL,a_pitch_actual_deg REAL NOT NULL,a_fault INTEGER NOT NULL,start_stop_score REAL,power_tracking_score REAL,pitch_response_score REAL,capability_safety_score REAL,total_score REAL,verdict TEXT NOT NULL,reason TEXT NOT NULL,evaluated_at_utc TEXT NOT NULL,UNIQUE(dispatch_command_id));
CREATE TABLE IF NOT EXISTS event_log(id INTEGER PRIMARY KEY AUTOINCREMENT,level TEXT NOT NULL,event_type TEXT NOT NULL,message TEXT NOT NULL,session_id TEXT,step INTEGER,created_at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dispatch_outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,state_step INTEGER NOT NULL,sim_time_s REAL NOT NULL,wind_target_kw REAL NOT NULL,diesel_target_kw REAL NOT NULL,wind_enable INTEGER NOT NULL,diesel_enable INTEGER NOT NULL,target_unserved_kw REAL NOT NULL,target_surplus_kw REAL NOT NULL,reason TEXT NOT NULL,executable INTEGER NOT NULL,status TEXT NOT NULL,protocol_seq INTEGER,command_id INTEGER,ack_accepted INTEGER,ack_reason TEXT,detail TEXT,created_at_utc TEXT NOT NULL,claimed_at_utc TEXT,completed_at_utc TEXT,UNIQUE(session_id,state_step,executable));
CREATE INDEX IF NOT EXISTS idx_dispatch_outbox_status_id ON dispatch_outbox(status,id);
CREATE INDEX IF NOT EXISTS idx_wind_exec_session_step ON wind_execution_evaluation(session_id,feedback_step);
CREATE TABLE IF NOT EXISTS process_status(process_name TEXT PRIMARY KEY,pid INTEGER,state TEXT NOT NULL,detail TEXT NOT NULL,heartbeat_at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS communication_config(id INTEGER PRIMARY KEY CHECK(id=1),host TEXT NOT NULL,port INTEGER NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,updated_at_utc TEXT NOT NULL);
'''
UNIFIED_PHYSICAL={"wind_rated_kw":100.0,"wind_cut_in_mps":3.0,"wind_rated_speed_mps":12.0,"wind_cut_out_mps":25.0,"pitch_min_deg":0.0,"pitch_max_deg":90.0,"wind_ramp_up_kw_s":40.0,"wind_ramp_down_kw_s":60.0,"diesel_min_kw":20.0,"diesel_max_kw":120.0,"diesel_ramp_up_kw_s":30.0,"diesel_ramp_down_kw_s":40.0}

REMOTE_PARAM_MAP={
    "diesel_min_power_kw":"diesel_min_kw",
    "diesel_max_power_kw":"diesel_max_kw",
    "wind_rated_power_kw":"wind_rated_kw",
    "cut_in_speed_mps":"wind_cut_in_mps",
    "rated_speed_mps":"wind_rated_speed_mps",
    "cut_out_speed_mps":"wind_cut_out_mps",
    "pitch_full_output_deg":"pitch_min_deg",
    "pitch_feather_deg":"pitch_max_deg",
}

def utc_now()->str:return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
class EMSRepository:
 def __init__(self,db_path:str|Path):self.db_path=Path(db_path)
 @contextmanager
 def connection(self)->Iterator[sqlite3.Connection]:
  self.db_path.parent.mkdir(parents=True,exist_ok=True);c=sqlite3.connect(self.db_path,timeout=5);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=5000')
  try:yield c;c.commit()
  except Exception:c.rollback();raise
  finally:c.close()
 @staticmethod
 def _columns(c:sqlite3.Connection,t:str)->set[str]:return {str(r[1]) for r in c.execute(f'PRAGMA table_info({t})')}
 def _add(self,c,t,n,sql):
  if n not in self._columns(c,t):c.execute(f'ALTER TABLE {t} ADD COLUMN {sql}')
 def _migrate(self,c):
  for t in ('current_state','state_history'):
   for n,sql in [('wind_available_kw','wind_available_kw REAL NOT NULL DEFAULT 0'),('wind_operating_limit_kw','wind_operating_limit_kw REAL NOT NULL DEFAULT 0'),('diesel_target_kw','diesel_target_kw REAL NOT NULL DEFAULT 0'),('diesel_running','diesel_running INTEGER NOT NULL DEFAULT 0'),('power_imbalance_kw','power_imbalance_kw REAL NOT NULL DEFAULT 0'),('received_age_s','received_age_s REAL NOT NULL DEFAULT 0'),('controller_wind_enable','controller_wind_enable INTEGER'),('pitch_target_deg','pitch_target_deg REAL'),('last_wind_action_seq','last_wind_action_seq INTEGER'),('last_wind_action_step','last_wind_action_step INTEGER'),('wind_action_applied_at_utc','wind_action_applied_at_utc TEXT'),('extension_status',"extension_status TEXT NOT NULL DEFAULT 'legacy_or_incomplete'")]:self._add(c,t,n,sql)
   c.execute(f"UPDATE {t} SET extension_status=CASE WHEN controller_wind_enable IS NOT NULL AND pitch_target_deg IS NOT NULL THEN 'complete' ELSE 'legacy_or_incomplete' END")
  for n,sql in [('ack_accepted','ack_accepted INTEGER'),('ack_reason','ack_reason TEXT'),('ack_received_at_utc','ack_received_at_utc TEXT')]:self._add(c,'dispatch_commands',n,sql)
 def initialize(self):
  with self.connection() as c:
   c.executescript(SCHEMA);self._migrate(c);now=utc_now();keys=','.join(UNIFIED_PHYSICAL);qs=','.join('?'*1 for _ in UNIFIED_PHYSICAL)
   c.execute(f'INSERT INTO physical_parameters(id,{keys},updated_at) VALUES(1,{qs},?) ON CONFLICT(id) DO NOTHING',tuple(UNIFIED_PHYSICAL.values())+(now,))
   c.execute('INSERT INTO dispatch_parameters(id,wind_min_kw,wind_max_kw,diesel_max_kw,reserve_kw,updated_at) VALUES(1,0,100,120,10,?) ON CONFLICT(id) DO NOTHING',(now,));c.execute('INSERT INTO ems_runtime_config(id,poll_period_s,dispatch_period_s,closed_loop,command_timeout_s,max_state_age_s,updated_at) VALUES(1,1,5,1,3,2,?) ON CONFLICT(id) DO NOTHING',(now,));c.execute("INSERT INTO communication_config(id,host,port,enabled,updated_at_utc) VALUES(1,'127.0.0.1',5000,1,?) ON CONFLICT(id) DO NOTHING",(now,));c.execute("INSERT INTO schema_meta(key,value) VALUES('schema_version','6') ON CONFLICT(key) DO UPDATE SET value='6'")
 def set_parameters(self,*,wind_min_kw,wind_max_kw,diesel_max_kw,reserve_kw=10.0):
  v=tuple(float(x) for x in (wind_min_kw,wind_max_kw,diesel_max_kw,reserve_kw))
  if any(not math.isfinite(x) or x<0 for x in v):raise ValueError('dispatch parameters must be finite non-negative numbers')
  if v[1]<v[0]:raise ValueError('wind_max_kw must be >= wind_min_kw')
  if v[2]<v[3]:raise ValueError('diesel_max_kw must be >= reserve_kw')
  if v[3]<=0:raise ValueError('reserve_kw must be > 0')
  with self.connection() as c:
   if c.execute('UPDATE dispatch_parameters SET wind_min_kw=?,wind_max_kw=?,diesel_max_kw=?,reserve_kw=?,updated_at=? WHERE id=1',v+(utc_now(),)).rowcount!=1:raise RuntimeError('dispatch parameters are not initialized')
 def get_parameters(self):
  with self.connection() as c:r=c.execute('SELECT * FROM dispatch_parameters WHERE id=1').fetchone()
  if r is None:raise RuntimeError('dispatch parameters are not initialized')
  return r
 def get_physical_parameters(self):
  with self.connection() as c:r=c.execute('SELECT * FROM physical_parameters WHERE id=1').fetchone()
  if r is None:raise RuntimeError('physical parameters are not initialized')
  return r
 def set_reserve(self,reserve_kw):
  reserve_kw=float(reserve_kw)
  if not math.isfinite(reserve_kw) or reserve_kw<=0:raise ValueError('reserve_kw must be > 0')
  with self.connection() as c:
   ph=c.execute('SELECT diesel_max_kw FROM physical_parameters WHERE id=1').fetchone()
   if ph is None:raise RuntimeError('physical parameters are not initialized')
   if reserve_kw>float(ph['diesel_max_kw']):raise ValueError('reserve_kw must be <= diesel_max_kw')
   if c.execute('UPDATE dispatch_parameters SET reserve_kw=?,updated_at=? WHERE id=1',(reserve_kw,utc_now())).rowcount!=1:raise RuntimeError('dispatch parameters are not initialized')
 def apply_remote_parameters(self,parameters):
  if not isinstance(parameters,dict) or not parameters:return False
  updates={}
  for remote,local in REMOTE_PARAM_MAP.items():
   if remote in parameters:
    value=parameters[remote]
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:continue
    updates[local]=float(value)
  if not updates:return False
  with self.connection() as c:
   row=c.execute('SELECT * FROM physical_parameters WHERE id=1').fetchone()
   if row is None:return False
   old=dict(row);changed={k:v for k,v in updates.items() if abs(float(old[k])-v)>1e-9}
   if not changed:return False
   merged=dict(old);merged.update(changed)
   if not merged['wind_cut_in_mps']<merged['wind_rated_speed_mps']<merged['wind_cut_out_mps']:return False
   if not merged['pitch_min_deg']<merged['pitch_max_deg']:return False
   if not merged['diesel_min_kw']<merged['diesel_max_kw']:return False
   setclause=', '.join(f'{k}=?' for k in changed)+', updated_at=?'
   c.execute(f'UPDATE physical_parameters SET {setclause} WHERE id=1',list(changed.values())+[utc_now()])
  return True
 def build_dispatch_config(self):
  d=self.get_parameters();ph=self.get_physical_parameters();rt=self.get_runtime_config()
  return DispatchConfig(wind_min_kw=0.0,wind_max_kw=float(ph['wind_rated_kw']),diesel_max_kw=float(ph['diesel_max_kw']),reserve_kw=float(d['reserve_kw']),diesel_min_kw=float(ph['diesel_min_kw']),max_state_age_s=float(rt['max_state_age_s']),c_has_control_priority=True)
 def set_runtime_config(self,*,poll_period_s=1.0,dispatch_period_s=5.0,closed_loop=True,command_timeout_s=3.0,max_state_age_s=2.0):
  p,d,a=float(poll_period_s),float(dispatch_period_s),float(max_state_age_s);t=None if command_timeout_s is None else float(command_timeout_s)
  if not all(math.isfinite(x) and x>0 for x in (p,d,a)):raise ValueError('runtime periods and max_state_age_s must be positive finite numbers')
  if t is not None and (not math.isfinite(t) or t<=0):raise ValueError('command_timeout_s must be > 0 or None')
  with self.connection() as c:
   if c.execute('UPDATE ems_runtime_config SET poll_period_s=?,dispatch_period_s=?,closed_loop=?,command_timeout_s=?,max_state_age_s=?,updated_at=? WHERE id=1',(p,d,int(bool(closed_loop)),t,a,utc_now())).rowcount!=1:raise RuntimeError('runtime config is not initialized')
 def get_runtime_config(self):
  with self.connection() as c:r=c.execute('SELECT * FROM ems_runtime_config WHERE id=1').fetchone()
  if r is None:raise RuntimeError('runtime config is not initialized')
  return r
 def set_communication_config(self,*,host,port,enabled=True):
  host=str(host).strip()
  if not host or any(ch.isspace() for ch in host):raise ValueError('host must be non-empty')
  if isinstance(port,bool) or not isinstance(port,int) or not 1<=port<=65535:raise ValueError('invalid port')
  with self.connection() as c:c.execute('UPDATE communication_config SET host=?,port=?,enabled=?,updated_at_utc=? WHERE id=1',(host,port,int(bool(enabled)),utc_now()))
 def get_communication_config(self):
  with self.connection() as c:r=c.execute('SELECT * FROM communication_config WHERE id=1').fetchone()
  if r is None:raise RuntimeError('communication config is not initialized')
  return r
 def save_state(self,state:Mapping[str,object]):
  if is_dataclass(state):state=asdict(state)
  if state.get('pitch_actual_deg') is None:raise ValueError('pitch_actual_deg is required')
  ctrl=None if state.get('controller_wind_enable') is None else int(bool(state['controller_wind_enable']));pt=state.get('pitch_target_deg');ea='complete' if ctrl is not None and pt is not None else 'legacy_or_incomplete';cols='session_id,step,sim_time_s,wind_speed_mps,wind_available_kw,wind_operating_limit_kw,load_power_kw,wind_actual_kw,diesel_actual_kw,wind_target_kw,diesel_target_kw,pitch_actual_deg,wind_running,fault,diesel_running,power_imbalance_kw,sampled_at_utc,received_at_utc,received_age_s,controller_wind_enable,pitch_target_deg,last_wind_action_seq,last_wind_action_step,wind_action_applied_at_utc,extension_status';vals=(state['session_id'],int(state['step']),float(state['sim_time_s']),float(state['wind_speed_mps']),float(state['wind_available_kw']),float(state['wind_operating_limit_kw']),float(state['load_power_kw']),float(state['wind_actual_kw']),float(state['diesel_actual_kw']),float(state['wind_target_kw']),float(state['diesel_target_kw']),float(state['pitch_actual_deg']),int(bool(state['wind_running'])),int(bool(state['fault'])),int(bool(state['diesel_running'])),float(state['power_imbalance_kw']),state['sampled_at_utc'],state['received_at_utc'],float(state['received_age_s']),ctrl,pt,state.get('last_wind_action_seq'),state.get('last_wind_action_step'),state.get('wind_action_applied_at_utc'),ea);q=','.join('?' for _ in vals)
  with self.connection() as c:
   prev=c.execute('SELECT session_id,step FROM current_state WHERE id=1').fetchone()
   if prev is not None and prev['session_id']==state['session_id'] and int(state['step'])<int(prev['step']):raise ValueError('state step moved backwards within current session')
   c.execute(f'INSERT INTO current_state(id,{cols}) VALUES(1,{q}) ON CONFLICT(id) DO UPDATE SET '+','.join(f'{x}=excluded.{x}' for x in cols.split(',')),vals);exists=c.execute('SELECT 1 FROM state_history WHERE session_id=? AND step=? AND sampled_at_utc=?',(state['session_id'],state['step'],state['sampled_at_utc'])).fetchone()
   if exists is None:c.execute(f'INSERT INTO state_history({cols}) VALUES({q})',vals)
 def get_current_state(self):
  with self.connection() as c:return c.execute('SELECT * FROM current_state WHERE id=1').fetchone()
 @staticmethod
 def _utc(value):return datetime.fromisoformat(str(value).replace('Z','+00:00'))
 @classmethod
 def _grid_state_from_row(cls,row):
  r=str(row['received_at_utc']);age=float(row['received_age_s'])+max(0,(datetime.now(timezone.utc)-cls._utc(r)).total_seconds());return GridState(session_id=str(row['session_id']),step=int(row['step']),sim_time_s=float(row['sim_time_s']),wind_speed_mps=float(row['wind_speed_mps']),wind_available_kw=float(row['wind_available_kw']),wind_operating_limit_kw=float(row['wind_operating_limit_kw']),load_power_kw=float(row['load_power_kw']),wind_actual_kw=float(row['wind_actual_kw']),diesel_actual_kw=float(row['diesel_actual_kw']),wind_running=bool(row['wind_running']),fault=bool(row['fault']),received_age_s=age,sampled_at_utc=str(row['sampled_at_utc']),received_at_utc=r,wind_target_kw=float(row['wind_target_kw']),diesel_target_kw=float(row['diesel_target_kw']),pitch_actual_deg=float(row['pitch_actual_deg']),diesel_running=bool(row['diesel_running']),power_imbalance_kw=float(row['power_imbalance_kw']),controller_wind_enable=None if row['controller_wind_enable'] is None else bool(row['controller_wind_enable']),pitch_target_deg=None if row['pitch_target_deg'] is None else float(row['pitch_target_deg']),last_wind_action_seq=None if row['last_wind_action_seq'] is None else int(row['last_wind_action_seq']),last_wind_action_step=None if row['last_wind_action_step'] is None else int(row['last_wind_action_step']),wind_action_applied_at_utc=None if row['wind_action_applied_at_utc'] is None else str(row['wind_action_applied_at_utc']),extension_status=str(row['extension_status'] or 'legacy_or_incomplete'))
 def get_current_grid_state(self):
  r=self.get_current_state();return None if r is None else self._grid_state_from_row(r)
 def get_grid_state(self,session_id,step):
  with self.connection() as c:r=c.execute('SELECT * FROM state_history WHERE session_id=? AND step=? ORDER BY id DESC LIMIT 1',(session_id,step)).fetchone()
  return None if r is None else self._grid_state_from_row(r)
 def record_command(self,command):
  with self.connection() as c:return int(c.execute('INSERT INTO dispatch_commands(session_id,step,sim_time_s,source,seq,wind_target_kw,diesel_target_kw,wind_enable,diesel_enable,status,reason,ack_accepted,ack_reason,ack_received_at_utc,created_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(command['session_id'],command['step'],command['sim_time_s'],command.get('source','B'),command['seq'],command['wind_target_kw'],command['diesel_target_kw'],int(command['wind_enable']),int(command['diesel_enable']),command.get('status','generated'),command.get('reason',''),command.get('ack_accepted'),command.get('ack_reason'),command.get('ack_received_at_utc'),command.get('created_at_utc',utc_now()))).lastrowid)
 def queue_decision(self,state,result,*,executable):
  with self.connection() as c:
   status='pending' if executable else 'open_loop';cur=c.execute('INSERT INTO dispatch_outbox(session_id,state_step,sim_time_s,wind_target_kw,diesel_target_kw,wind_enable,diesel_enable,target_unserved_kw,target_surplus_kw,reason,executable,status,created_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id,state_step,executable) DO NOTHING',(state.session_id,state.step,state.sim_time_s,result.wind_target_kw,result.diesel_target_kw,int(result.wind_enable),int(result.diesel_enable),result.target_unserved_kw,result.target_surplus_kw,result.reason,int(executable),status,utc_now()));r=c.execute('SELECT id FROM dispatch_outbox WHERE session_id=? AND state_step=? AND executable=?',(state.session_id,state.step,int(executable))).fetchone();return (int(r['id']),cur.rowcount==1)
 def claim_next_outbox(self):
  with self.connection() as c:
   c.execute('BEGIN IMMEDIATE');r=c.execute("SELECT * FROM dispatch_outbox WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
   if r is None:return None
   if c.execute("UPDATE dispatch_outbox SET status='sending',claimed_at_utc=? WHERE id=? AND status='pending'",(utc_now(),r['id'])).rowcount!=1:return None
   return c.execute('SELECT * FROM dispatch_outbox WHERE id=?',(r['id'],)).fetchone()
 def finish_outbox(self,outbox_id,*,status,protocol_seq=None,command_id=None,ack_accepted=None,ack_reason=None,detail=None):
  if status not in {'accepted','rejected','delivery_unknown','cancelled','local_error'}:raise ValueError(f'invalid terminal outbox status: {status}')
  with self.connection() as c:
   if c.execute('UPDATE dispatch_outbox SET status=?,protocol_seq=?,command_id=?,ack_accepted=?,ack_reason=?,detail=?,completed_at_utc=? WHERE id=? AND status="sending"',(status,protocol_seq,command_id,None if ack_accepted is None else int(ack_accepted),ack_reason,detail,utc_now(),outbox_id)).rowcount!=1:raise RuntimeError(f'outbox row {outbox_id} is not in sending state')
 def recover_abandoned_outbox(self):
  with self.connection() as c:return int(c.execute("UPDATE dispatch_outbox SET status='delivery_unknown',detail='I/O process restarted while command was in sending state',completed_at_utc=? WHERE status='sending'",(utc_now(),)).rowcount)
 def get_outbox(self,outbox_id):
  with self.connection() as c:return c.execute('SELECT * FROM dispatch_outbox WHERE id=?',(outbox_id,)).fetchone()
 def heartbeat(self,process_name,*,pid,state,detail=''):
  with self.connection() as c:c.execute('INSERT INTO process_status(process_name,pid,state,detail,heartbeat_at_utc) VALUES(?,?,?,?,?) ON CONFLICT(process_name) DO UPDATE SET pid=excluded.pid,state=excluded.state,detail=excluded.detail,heartbeat_at_utc=excluded.heartbeat_at_utc',(process_name,pid,state,detail,utc_now()))
 def get_process_status(self):
  with self.connection() as c:return list(c.execute('SELECT * FROM process_status ORDER BY process_name'))
 def record_evaluation(self,*,command_id,target_unserved_kw,target_surplus_kw,actual_unserved_kw=None,actual_surplus_kw=None):
  with self.connection() as c:return int(c.execute('INSERT INTO dispatch_evaluation(command_id,target_unserved_kw,target_surplus_kw,actual_unserved_kw,actual_surplus_kw,evaluated_at_utc) VALUES(?,?,?,?,?,?)',(command_id,target_unserved_kw,target_surplus_kw,actual_unserved_kw,actual_surplus_kw,utc_now())).lastrowid)
 def record_wind_execution_evaluation(self,row):
  fields=('session_id','dispatch_command_id','outbox_id','c_wind_action_seq','dispatch_step','feedback_step','dispatch_created_at_utc','feedback_sampled_at_utc','wind_action_applied_at_utc','response_latency_s','b_wind_enable','b_wind_target_kw','c_controller_wind_enable','c_pitch_target_deg','c_wind_available_kw','c_wind_operating_limit_kw','a_wind_running','a_wind_actual_kw','a_pitch_actual_deg','a_fault','start_stop_score','power_tracking_score','pitch_response_score','capability_safety_score','total_score','verdict','reason','evaluated_at_utc');vals=tuple(row.get(k) for k in fields);ph=','.join('?' for _ in vals);updates=','.join(f'{f}=excluded.{f}' for f in fields if f!='dispatch_command_id')
  with self.connection() as c:
   cur=c.execute(f'INSERT INTO wind_execution_evaluation({",".join(fields)}) VALUES({ph}) ON CONFLICT(dispatch_command_id) DO UPDATE SET {updates}',vals);return int(cur.lastrowid or c.execute('SELECT id FROM wind_execution_evaluation WHERE dispatch_command_id=?',(row['dispatch_command_id'],)).fetchone()[0])
 def get_wind_execution_history(self,*,session_id=None,start_utc=None,end_utc=None,limit=200):
  clauses=[];args=[]
  if session_id:clauses.append('session_id=?');args.append(session_id)
  if start_utc:clauses.append('feedback_sampled_at_utc>=?');args.append(start_utc)
  if end_utc:clauses.append('feedback_sampled_at_utc<=?');args.append(end_utc)
  where=' WHERE '+ ' AND '.join(clauses) if clauses else ''
  with self.connection() as c:return list(c.execute('SELECT * FROM wind_execution_evaluation'+where+' ORDER BY feedback_step DESC,id DESC LIMIT ?',args+[int(limit)]))
 def record_log(self,level,event_type,message,*,session_id=None,step=None):
  with self.connection() as c:c.execute('INSERT INTO event_log(level,event_type,message,session_id,step,created_at_utc) VALUES(?,?,?,?,?,?)',(level,event_type,message,session_id,step,utc_now()))
