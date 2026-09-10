"""SQLite repository for B's local EMS state, parameters and audit history."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import sqlite3
from typing import Iterator, Mapping, Optional

from .models import DispatchResult, GridState

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS physical_parameters (
 id INTEGER PRIMARY KEY CHECK(id=1), wind_rated_kw REAL NOT NULL DEFAULT 100.0, wind_cut_in_mps REAL NOT NULL DEFAULT 3.0,
 wind_rated_speed_mps REAL NOT NULL DEFAULT 12.0, wind_cut_out_mps REAL NOT NULL DEFAULT 25.0, pitch_min_deg REAL NOT NULL DEFAULT 0.0,
 pitch_max_deg REAL NOT NULL DEFAULT 90.0, wind_ramp_up_kw_s REAL NOT NULL DEFAULT 40.0, wind_ramp_down_kw_s REAL NOT NULL DEFAULT 60.0,
 diesel_min_kw REAL NOT NULL DEFAULT 20.0, diesel_max_kw REAL NOT NULL DEFAULT 120.0, diesel_ramp_up_kw_s REAL NOT NULL DEFAULT 30.0,
 diesel_ramp_down_kw_s REAL NOT NULL DEFAULT 40.0, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dispatch_parameters (
 id INTEGER PRIMARY KEY CHECK(id=1), wind_min_kw REAL NOT NULL CHECK(wind_min_kw>=0), wind_max_kw REAL NOT NULL CHECK(wind_max_kw>=wind_min_kw),
 diesel_max_kw REAL NOT NULL CHECK(diesel_max_kw>=0), reserve_kw REAL NOT NULL DEFAULT 10.0 CHECK(reserve_kw>0), updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ems_runtime_config (
 id INTEGER PRIMARY KEY CHECK(id=1), poll_period_s REAL NOT NULL DEFAULT 1.0 CHECK(poll_period_s>0), dispatch_period_s REAL NOT NULL DEFAULT 5.0 CHECK(dispatch_period_s>0),
 closed_loop INTEGER NOT NULL DEFAULT 1 CHECK(closed_loop IN(0,1)), command_timeout_s REAL DEFAULT 3.0 CHECK(command_timeout_s IS NULL OR command_timeout_s>0),
 max_state_age_s REAL NOT NULL DEFAULT 2.0 CHECK(max_state_age_s>0), updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS current_state (
 id INTEGER PRIMARY KEY CHECK(id=1), session_id TEXT NOT NULL, step INTEGER NOT NULL CHECK(step>=0), sim_time_s REAL NOT NULL CHECK(sim_time_s>=0),
 wind_speed_mps REAL NOT NULL CHECK(wind_speed_mps>=0), wind_available_kw REAL NOT NULL CHECK(wind_available_kw>=0),
 wind_operating_limit_kw REAL NOT NULL CHECK(wind_operating_limit_kw>=0 AND wind_operating_limit_kw<=wind_available_kw),
 load_power_kw REAL NOT NULL CHECK(load_power_kw>=0), wind_actual_kw REAL NOT NULL CHECK(wind_actual_kw>=0), diesel_actual_kw REAL NOT NULL CHECK(diesel_actual_kw>=0),
 wind_target_kw REAL NOT NULL CHECK(wind_target_kw>=0), diesel_target_kw REAL NOT NULL DEFAULT 0 CHECK(diesel_target_kw>=0),
 pitch_actual_deg REAL NOT NULL CHECK(pitch_actual_deg>=0 AND pitch_actual_deg<=90), wind_running INTEGER NOT NULL CHECK(wind_running IN(0,1)), fault INTEGER NOT NULL CHECK(fault IN(0,1)),
 diesel_running INTEGER NOT NULL DEFAULT 0 CHECK(diesel_running IN(0,1)), power_imbalance_kw REAL NOT NULL DEFAULT 0,
 sampled_at_utc TEXT NOT NULL, received_at_utc TEXT NOT NULL, received_age_s REAL NOT NULL CHECK(received_age_s>=0),
 controller_wind_enable INTEGER CHECK(controller_wind_enable IS NULL OR controller_wind_enable IN(0,1)),
 pitch_target_deg REAL CHECK(pitch_target_deg IS NULL OR(pitch_target_deg>=0 AND pitch_target_deg<=90)),
 last_wind_action_seq INTEGER CHECK(last_wind_action_seq IS NULL OR last_wind_action_seq>=0),
 last_wind_action_step INTEGER CHECK(last_wind_action_step IS NULL OR last_wind_action_step>=0),
 wind_action_applied_at_utc TEXT, extension_status TEXT NOT NULL DEFAULT 'legacy_or_incomplete'
);
CREATE TABLE IF NOT EXISTS state_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, step INTEGER NOT NULL CHECK(step>=0), sim_time_s REAL NOT NULL CHECK(sim_time_s>=0),
 wind_speed_mps REAL NOT NULL CHECK(wind_speed_mps>=0), wind_available_kw REAL NOT NULL CHECK(wind_available_kw>=0),
 wind_operating_limit_kw REAL NOT NULL CHECK(wind_operating_limit_kw>=0 AND wind_operating_limit_kw<=wind_available_kw),
 load_power_kw REAL NOT NULL CHECK(load_power_kw>=0), wind_actual_kw REAL NOT NULL CHECK(wind_actual_kw>=0), diesel_actual_kw REAL NOT NULL CHECK(diesel_actual_kw>=0),
 wind_target_kw REAL NOT NULL CHECK(wind_target_kw>=0), diesel_target_kw REAL NOT NULL DEFAULT 0 CHECK(diesel_target_kw>=0),
 pitch_actual_deg REAL NOT NULL CHECK(pitch_actual_deg>=0 AND pitch_actual_deg<=90), wind_running INTEGER NOT NULL CHECK(wind_running IN(0,1)), fault INTEGER NOT NULL CHECK(fault IN(0,1)),
 diesel_running INTEGER NOT NULL DEFAULT 0 CHECK(diesel_running IN(0,1)), power_imbalance_kw REAL NOT NULL DEFAULT 0,
 sampled_at_utc TEXT NOT NULL, received_at_utc TEXT NOT NULL, received_age_s REAL NOT NULL CHECK(received_age_s>=0),
 controller_wind_enable INTEGER CHECK(controller_wind_enable IS NULL OR controller_wind_enable IN(0,1)),
 pitch_target_deg REAL CHECK(pitch_target_deg IS NULL OR(pitch_target_deg>=0 AND pitch_target_deg<=90)),
 last_wind_action_seq INTEGER CHECK(last_wind_action_seq IS NULL OR last_wind_action_seq>=0),
 last_wind_action_step INTEGER CHECK(last_wind_action_step IS NULL OR last_wind_action_step>=0),
 wind_action_applied_at_utc TEXT, extension_status TEXT NOT NULL DEFAULT 'legacy_or_incomplete'
);
CREATE TABLE IF NOT EXISTS dispatch_commands (
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, step INTEGER NOT NULL CHECK(step>=0), sim_time_s REAL NOT NULL CHECK(sim_time_s>=0),
 source TEXT NOT NULL, seq INTEGER NOT NULL CHECK(seq>=0), wind_target_kw REAL NOT NULL CHECK(wind_target_kw>=0), diesel_target_kw REAL NOT NULL CHECK(diesel_target_kw>=0),
 wind_enable INTEGER NOT NULL CHECK(wind_enable IN(0,1)), diesel_enable INTEGER NOT NULL CHECK(diesel_enable IN(0,1)), status TEXT NOT NULL, reason TEXT NOT NULL,
 ack_accepted INTEGER CHECK(ack_accepted IN(0,1)), ack_reason TEXT, ack_received_at_utc TEXT, created_at_utc TEXT NOT NULL, UNIQUE(session_id,source,seq)
);
CREATE TABLE IF NOT EXISTS dispatch_evaluation (
 id INTEGER PRIMARY KEY AUTOINCREMENT, command_id INTEGER NOT NULL REFERENCES dispatch_commands(id), target_unserved_kw REAL NOT NULL CHECK(target_unserved_kw>=0), target_surplus_kw REAL NOT NULL CHECK(target_surplus_kw>=0),
 actual_unserved_kw REAL, actual_surplus_kw REAL, evaluated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wind_execution_evaluation (
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, dispatch_command_id INTEGER NOT NULL REFERENCES dispatch_commands(id), outbox_id INTEGER, c_wind_action_seq INTEGER,
 dispatch_step INTEGER NOT NULL CHECK(dispatch_step>=0), feedback_step INTEGER NOT NULL CHECK(feedback_step>dispatch_step), dispatch_created_at_utc TEXT NOT NULL, feedback_sampled_at_utc TEXT NOT NULL,
 wind_action_applied_at_utc TEXT, response_latency_s REAL CHECK(response_latency_s IS NULL OR response_latency_s>=0), b_wind_enable INTEGER NOT NULL CHECK(b_wind_enable IN(0,1)),
 b_wind_target_kw REAL NOT NULL CHECK(b_wind_target_kw>=0), c_controller_wind_enable INTEGER NOT NULL CHECK(c_controller_wind_enable IN(0,1)), c_pitch_target_deg REAL NOT NULL CHECK(c_pitch_target_deg>=0 AND c_pitch_target_deg<=90),
 c_wind_available_kw REAL NOT NULL CHECK(c_wind_available_kw>=0), c_wind_operating_limit_kw REAL NOT NULL CHECK(c_wind_operating_limit_kw>=0), a_wind_running INTEGER NOT NULL CHECK(a_wind_running IN(0,1)),
 a_wind_actual_kw REAL NOT NULL CHECK(a_wind_actual_kw>=0), a_pitch_actual_deg REAL NOT NULL CHECK(a_pitch_actual_deg>=0 AND a_pitch_actual_deg<=90), a_fault INTEGER NOT NULL CHECK(a_fault IN(0,1)),
 start_stop_score REAL CHECK(start_stop_score IS NULL OR(start_stop_score BETWEEN 0 AND 100)), power_tracking_score REAL CHECK(power_tracking_score IS NULL OR(power_tracking_score BETWEEN 0 AND 100)),
 pitch_response_score REAL CHECK(pitch_response_score IS NULL OR(pitch_response_score BETWEEN 0 AND 100)), capability_safety_score REAL CHECK(capability_safety_score IS NULL OR(capability_safety_score BETWEEN 0 AND 100)),
 total_score REAL CHECK(total_score IS NULL OR(total_score BETWEEN 0 AND 100)), verdict TEXT NOT NULL, reason TEXT NOT NULL, evaluated_at_utc TEXT NOT NULL, UNIQUE(dispatch_command_id)
);
CREATE TABLE IF NOT EXISTS event_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT NOT NULL, event_type TEXT NOT NULL, message TEXT NOT NULL, session_id TEXT, step INTEGER, created_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dispatch_outbox (
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, state_step INTEGER NOT NULL CHECK(state_step>=0), sim_time_s REAL NOT NULL CHECK(sim_time_s>=0),
 wind_target_kw REAL NOT NULL CHECK(wind_target_kw>=0), diesel_target_kw REAL NOT NULL CHECK(diesel_target_kw>=0), wind_enable INTEGER NOT NULL CHECK(wind_enable IN(0,1)), diesel_enable INTEGER NOT NULL CHECK(diesel_enable IN(0,1)),
 target_unserved_kw REAL NOT NULL CHECK(target_unserved_kw>=0), target_surplus_kw REAL NOT NULL CHECK(target_surplus_kw>=0), reason TEXT NOT NULL, executable INTEGER NOT NULL CHECK(executable IN(0,1)),
 status TEXT NOT NULL CHECK(status IN('open_loop','pending','sending','accepted','rejected','delivery_unknown','cancelled','local_error')), protocol_seq INTEGER CHECK(protocol_seq IS NULL OR protocol_seq>=0),
 command_id INTEGER REFERENCES dispatch_commands(id), ack_accepted INTEGER CHECK(ack_accepted IN(0,1)), ack_reason TEXT, detail TEXT, created_at_utc TEXT NOT NULL, claimed_at_utc TEXT, completed_at_utc TEXT,
 UNIQUE(session_id,state_step,executable)
);
CREATE INDEX IF NOT EXISTS idx_dispatch_outbox_status_id ON dispatch_outbox(status,id);
CREATE INDEX IF NOT EXISTS idx_wind_exec_session_step ON wind_execution_evaluation(session_id,feedback_step);
CREATE TABLE IF NOT EXISTS process_status (process_name TEXT PRIMARY KEY, pid INTEGER, state TEXT NOT NULL, detail TEXT NOT NULL, heartbeat_at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS communication_config (id INTEGER PRIMARY KEY CHECK(id=1), host TEXT NOT NULL, port INTEGER NOT NULL CHECK(port BETWEEN 1 AND 65535), enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)), updated_at_utc TEXT NOT NULL);
"""

UNIFIED_PHYSICAL={"wind_rated_kw":100.0,"wind_cut_in_mps":3.0,"wind_rated_speed_mps":12.0,"wind_cut_out_mps":25.0,"pitch_min_deg":0.0,"pitch_max_deg":90.0,"wind_ramp_up_kw_s":40.0,"wind_ramp_down_kw_s":60.0,"diesel_min_kw":20.0,"diesel_max_kw":120.0,"diesel_ramp_up_kw_s":30.0,"diesel_ramp_down_kw_s":40.0}


def utc_now()->str:return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")


class EMSRepository:
    def __init__(self,db_path:str|Path):self.db_path=Path(db_path)
    @contextmanager
    def connection(self)->Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True,exist_ok=True); conn=sqlite3.connect(self.db_path,timeout=5.0); conn.row_factory=sqlite3.Row; conn.execute("PRAGMA foreign_keys=ON"); conn.execute("PRAGMA busy_timeout=5000")
        try:yield conn;conn.commit()
        except Exception:conn.rollback();raise
        finally:conn.close()
    @staticmethod
    def _columns(conn:sqlite3.Connection,table:str)->set[str]:return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
    def _add_column(self,conn:sqlite3.Connection,table:str,name:str,sql:str)->None:
        if name not in self._columns(conn,table):conn.execute(f"ALTER TABLE {table} ADD COLUMN {sql}")
    def initialize(self)->None:
        with self.connection() as conn:
            conn.executescript(SCHEMA); self._migrate_legacy(conn); now=utc_now(); cols=','.join(UNIFIED_PHYSICAL); qs=','.join('?' for _ in UNIFIED_PHYSICAL)
            conn.execute(f"INSERT INTO physical_parameters(id,{cols},updated_at) VALUES(1,{qs},?) ON CONFLICT(id) DO NOTHING",tuple(UNIFIED_PHYSICAL.values())+(now,))
            conn.execute("INSERT INTO dispatch_parameters(id,wind_min_kw,wind_max_kw,diesel_max_kw,reserve_kw,updated_at) VALUES(1,0,100,120,10,?) ON CONFLICT(id) DO NOTHING",(now,))
            conn.execute("INSERT INTO ems_runtime_config(id,poll_period_s,dispatch_period_s,closed_loop,command_timeout_s,max_state_age_s,updated_at) VALUES(1,1,5,1,3,2,?) ON CONFLICT(id) DO NOTHING",(now,))
            conn.execute("INSERT INTO communication_config(id,host,port,enabled,updated_at_utc) VALUES(1,'127.0.0.1',5000,1,?) ON CONFLICT(id) DO NOTHING",(now,))
            conn.execute("INSERT INTO schema_meta(key,value) VALUES('schema_version','6') ON CONFLICT(key) DO UPDATE SET value='6'")
    def _migrate_legacy(self,conn:sqlite3.Connection)->None:
        for table in ('current_state','state_history'):
            self._add_column(conn,table,'wind_available_kw','wind_available_kw REAL NOT NULL DEFAULT 0'); self._add_column(conn,table,'wind_operating_limit_kw','wind_operating_limit_kw REAL NOT NULL DEFAULT 0'); self._add_column(conn,table,'diesel_target_kw','diesel_target_kw REAL NOT NULL DEFAULT 0'); self._add_column(conn,table,'diesel_running','diesel_running INTEGER NOT NULL DEFAULT 0'); self._add_column(conn,table,'power_imbalance_kw','power_imbalance_kw REAL NOT NULL DEFAULT 0'); self._add_column(conn,table,'received_age_s','received_age_s REAL NOT NULL DEFAULT 0')
            self._add_column(conn,table,'controller_wind_enable','controller_wind_enable INTEGER'); self._add_column(conn,table,'pitch_target_deg','pitch_target_deg REAL'); self._add_column(conn,table,'last_wind_action_seq','last_wind_action_seq INTEGER'); self._add_column(conn,table,'last_wind_action_step','last_wind_action_step INTEGER'); self._add_column(conn,table,'wind_action_applied_at_utc','wind_action_applied_at_utc TEXT'); self._add_column(conn,table,'extension_status',"extension_status TEXT NOT NULL DEFAULT 'legacy_or_incomplete'")
            conn.execute(f"UPDATE {table} SET extension_status=CASE WHEN controller_wind_enable IS NOT NULL AND pitch_target_deg IS NOT NULL THEN 'complete' ELSE 'legacy_or_incomplete' END")
        for name,sql in (("ack_accepted","ack_accepted INTEGER"),("ack_reason","ack_reason TEXT"),("ack_received_at_utc","ack_received_at_utc TEXT")):self._add_column(conn,'dispatch_commands',name,sql)
    def set_parameters(self,*,wind_min_kw:float,wind_max_kw:float,diesel_max_kw:float,reserve_kw:float=10.0)->None:
        vals=tuple(float(v) for v in (wind_min_kw,wind_max_kw,diesel_max_kw,reserve_kw))
        if any(not math.isfinite(v) or v<0 for v in vals):raise ValueError('dispatch parameters must be finite non-negative numbers')
        if vals[1]<vals[0]:raise ValueError('wind_max_kw must be >= wind_min_kw')
        if vals[2]<vals[3]:raise ValueError('diesel_max_kw must be >= reserve_kw')
        if vals[3]<=0:raise ValueError('reserve_kw must be > 0')
        with self.connection() as conn:
            cur=conn.execute('UPDATE dispatch_parameters SET wind_min_kw=?,wind_max_kw=?,diesel_max_kw=?,reserve_kw=?,updated_at=? WHERE id=1',vals+(utc_now(),))
            if cur.rowcount!=1:raise RuntimeError('dispatch parameters are not initialized')
    def get_parameters(self)->sqlite3.Row:
        with self.connection() as conn:row=conn.execute('SELECT * FROM dispatch_parameters WHERE id=1').fetchone()
        if row is None:raise RuntimeError('dispatch parameters are not initialized')
        return row
    def get_physical_parameters(self)->sqlite3.Row:
        with self.connection() as conn:row=conn.execute('SELECT * FROM physical_parameters WHERE id=1').fetchone()
        if row is None:raise RuntimeError('physical parameters are not initialized')
        return row
    def set_runtime_config(self,*,poll_period_s:float=1.0,dispatch_period_s:float=5.0,closed_loop:bool=True,command_timeout_s:Optional[float]=3.0,max_state_age_s:float=2.0)->None:
        poll,dispatch,age=float(poll_period_s),float(dispatch_period_s),float(max_state_age_s)
        if not all(math.isfinite(x) and x>0 for x in (poll,dispatch,age)):raise ValueError('runtime periods and max_state_age_s must be positive finite numbers')
        timeout=None if command_timeout_s is None else float(command_timeout_s)
        if timeout is not None and (not math.isfinite(timeout) or timeout<=0):raise ValueError('command_timeout_s must be > 0 or None')
        with self.connection() as conn:
            cur=conn.execute('UPDATE ems_runtime_config SET poll_period_s=?,dispatch_period_s=?,closed_loop=?,command_timeout_s=?,max_state_age_s=?,updated_at=? WHERE id=1',(poll,dispatch,int(bool(closed_loop)),timeout,age,utc_now()))
            if cur.rowcount!=1:raise RuntimeError('runtime config is not initialized')
    def get_runtime_config(self)->sqlite3.Row:
        with self.connection() as conn:row=conn.execute('SELECT * FROM ems_runtime_config WHERE id=1').fetchone()
        if row is None:raise RuntimeError('runtime config is not initialized')
        return row
    def set_communication_config(self,*,host:str,port:int,enabled:bool=True)->None:
        host=str(host).strip()
        if not host or any(c.isspace() for c in host):raise ValueError('host must be a non-empty hostname or IP address')
        if isinstance(port,bool) or not isinstance(port,int) or not 1<=port<=65535:raise ValueError('port must be an integer in [1,65535]')
        with self.connection() as conn:conn.execute('UPDATE communication_config SET host=?,port=?,enabled=?,updated_at_utc=? WHERE id=1',(host,port,int(bool(enabled)),utc_now()))
    def get_communication_config(self)->sqlite3.Row:
        with self.connection() as conn:row=conn.execute('SELECT * FROM communication_config WHERE id=1').fetchone()
        if row is None:raise RuntimeError('communication config is not initialized')
        return row
    def save_state(self,state:Mapping[str,object])->None:
        if is_dataclass(state):state=asdict(state)
        required=('session_id','step','sim_time_s','wind_speed_mps','wind_available_kw','wind_operating_limit_kw','load_power_kw','wind_actual_kw','diesel_actual_kw','wind_target_kw','diesel_target_kw','wind_running','diesel_running','fault','power_imbalance_kw','sampled_at_utc','received_at_utc','received_age_s')
        missing=[k for k in required if k not in state]
        if missing:raise ValueError(f'missing state fields: {", ".join(missing)}')
        if state.get('pitch_actual_deg') is None:raise ValueError('pitch_actual_deg is required')
        controller=None if state.get('controller_wind_enable') is None else int(bool(state['controller_wind_enable'])); pitch_target=state.get('pitch_target_deg'); action_seq=state.get('last_wind_action_seq'); action_step=state.get('last_wind_action_step'); action_time=state.get('wind_action_applied_at_utc')
        ext_status='complete' if controller is not None and pitch_target is not None else 'legacy_or_incomplete'
        if str(state.get('extension_status') or '')=='legacy_or_incomplete' and ext_status=='complete':ext_status='complete'
        vals=(state['session_id'],int(state['step']),float(state['sim_time_s']),float(state['wind_speed_mps']),float(state['wind_available_kw']),float(state['wind_operating_limit_kw']),float(state['load_power_kw']),float(state['wind_actual_kw']),float(state['diesel_actual_kw']),float(state['wind_target_kw']),float(state['diesel_target_kw']),float(state['pitch_actual_deg']),int(bool(state['wind_running'])),int(bool(state['fault'])),int(bool(state['diesel_running'])),float(state['power_imbalance_kw']),state['sampled_at_utc'],state['received_at_utc'],float(state['received_age_s']),controller,pitch_target,action_seq,action_step,action_time,ext_status)
        cols='session_id,step,sim_time_s,wind_speed_mps,wind_available_kw,wind_operating_limit_kw,load_power_kw,wind_actual_kw,diesel_actual_kw,wind_target_kw,diesel_target_kw,pitch_actual_deg,wind_running,fault,diesel_running,power_imbalance_kw,sampled_at_utc,received_at_utc,received_age_s,controller_wind_enable,pitch_target_deg,last_wind_action_seq,last_wind_action_step,wind_action_applied_at_utc,extension_status'
        with self.connection() as conn:
            prev=conn.execute('SELECT session_id,step FROM current_state WHERE id=1').fetchone()
            if prev is not None and prev['session_id']==state['session_id'] and int(state['step'])<int(prev['step']):raise ValueError('state step moved backwards within the current session')
            qs=','.join('?' for _ in range(25)); conn.execute(f'INSERT INTO current_state(id,{cols}) VALUES(1,{qs}) ON CONFLICT(id) DO UPDATE SET '+','.join(f'{c}=excluded.{c}' for c in cols.split(',')),vals)
            exists=conn.execute('SELECT 1 FROM state_history WHERE session_id=? AND step=? AND sampled_at_utc=? LIMIT 1',(state['session_id'],state['step'],state['sampled_at_utc'])).fetchone()
            if exists is None:conn.execute(f'INSERT INTO state_history({cols}) VALUES({','.join('?' for _ in range(25))})',vals)
    def get_current_state(self)->Optional[sqlite3.Row]:
        with self.connection() as conn:return conn.execute('SELECT * FROM current_state WHERE id=1').fetchone()
    @staticmethod
    def _utc_datetime(value:str)->datetime:return datetime.fromisoformat(str(value).replace('Z','+00:00'))
    @classmethod
    def _grid_state_from_row(cls,row:Mapping[str,object])->GridState:
        received_at=str(row['received_at_utc']); elapsed=max(0.0,(datetime.now(timezone.utc)-cls._utc_datetime(received_at)).total_seconds())
        return GridState(session_id=str(row['session_id']),step=int(row['step']),sim_time_s=float(row['sim_time_s']),wind_speed_mps=float(row['wind_speed_mps']),wind_available_kw=float(row['wind_available_kw']),wind_operating_limit_kw=float(row['wind_operating_limit_kw']),load_power_kw=float(row['load_power_kw']),wind_actual_kw=float(row['wind_actual_kw']),diesel_actual_kw=float(row['diesel_actual_kw']),wind_running=bool(row['wind_running']),fault=bool(row['fault']),received_age_s=float(row['received_age_s'])+elapsed,sampled_at_utc=str(row['sampled_at_utc']),received_at_utc=received_at,wind_target_kw=float(row['wind_target_kw']),diesel_target_kw=float(row['diesel_target_kw']),pitch_actual_deg=float(row['pitch_actual_deg']),diesel_running=bool(row['diesel_running']),power_imbalance_kw=float(row['power_imbalance_kw']),controller_wind_enable=None if row['controller_wind_enable'] is None else bool(row['controller_wind_enable']),pitch_target_deg=None if row['pitch_target_deg'] is None else float(row['pitch_target_deg']),last_wind_action_seq=None if row['last_wind_action_seq'] is None else int(row['last_wind_action_seq']),last_wind_action_step=None if row['last_wind_action_step'] is None else int(row['last_wind_action_step']),wind_action_applied_at_utc=None if row['wind_action_applied_at_utc'] is None else str(row['wind_action_applied_at_utc']),extension_status=str(row['extension_status'] or 'legacy_or_incomplete'))
    def get_current_grid_state(self)->Optional[GridState]:
        row=self.get_current_state();return None if row is None else self._grid_state_from_row(row)
    def get_grid_state(self,session_id:str,step:int)->Optional[GridState]:
        with self.connection() as conn:row=conn.execute('SELECT * FROM state_history WHERE session_id=? AND step=? ORDER BY id DESC LIMIT 1',(session_id,step)).fetchone()
        return None if row is None else self._grid_state_from_row(row)
    def record_command(self,command:Mapping[str,object])->int:
        with self.connection() as conn:
            cur=conn.execute('INSERT INTO dispatch_commands(session_id,step,sim_time_s,source,seq,wind_target_kw,diesel_target_kw,wind_enable,diesel_enable,status,reason,ack_accepted,ack_reason,ack_received_at_utc,created_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(command['session_id'],command['step'],command['sim_time_s'],command.get('source','B'),command['seq'],command['wind_target_kw'],command['diesel_target_kw'],int(command['wind_enable']),int(command['diesel_enable']),command.get('status','generated'),command.get('reason',''),command.get('ack_accepted'),command.get('ack_reason'),command.get('ack_received_at_utc'),command.get('created_at_utc',utc_now())));return int(cur.lastrowid)
    def queue_decision(self,state:GridState,result:DispatchResult,*,executable:bool)->tuple[int,bool]:
        status='pending' if executable else 'open_loop'
        with self.connection() as conn:
            cur=conn.execute('INSERT INTO dispatch_outbox(session_id,state_step,sim_time_s,wind_target_kw,diesel_target_kw,wind_enable,diesel_enable,target_unserved_kw,target_surplus_kw,reason,executable,status,created_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id,state_step,executable) DO NOTHING',(state.session_id,state.step,state.sim_time_s,result.wind_target_kw,result.diesel_target_kw,int(result.wind_enable),int(result.diesel_enable),result.target_unserved_kw,result.target_surplus_kw,result.reason,int(executable),status,utc_now()))
            row=conn.execute('SELECT id FROM dispatch_outbox WHERE session_id=? AND state_step=? AND executable=?',(state.session_id,state.step,int(executable))).fetchone()
        return (int(row['id']),cur.rowcount==1) if row else (_ for _ in ()).throw(RuntimeError('failed to persist EMS decision'))
    def claim_next_outbox(self)->Optional[sqlite3.Row]:
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');row=conn.execute("SELECT * FROM dispatch_outbox WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
            if row is None:return None
            cur=conn.execute("UPDATE dispatch_outbox SET status='sending',claimed_at_utc=? WHERE id=? AND status='pending'",(utc_now(),row['id']))
            return None if cur.rowcount!=1 else conn.execute('SELECT * FROM dispatch_outbox WHERE id=?',(row['id'],)).fetchone()
    def finish_outbox(self,outbox_id:int,*,status:str,protocol_seq:Optional[int]=None,command_id:Optional[int]=None,ack_accepted:Optional[bool]=None,ack_reason:Optional[str]=None,detail:Optional[str]=None)->None:
        if status not in {'accepted','rejected','delivery_unknown','cancelled','local_error'}:raise ValueError(f'invalid terminal outbox status: {status}')
        with self.connection() as conn:
            cur=conn.execute('UPDATE dispatch_outbox SET status=?,protocol_seq=?,command_id=?,ack_accepted=?,ack_reason=?,detail=?,completed_at_utc=? WHERE id=? AND status="sending"',(status,protocol_seq,command_id,None if ack_accepted is None else int(ack_accepted),ack_reason,detail,utc_now(),outbox_id))
            if cur.rowcount!=1:raise RuntimeError(f'outbox row {outbox_id} is not in sending state')
    def recover_abandoned_outbox(self)->int:
        with self.connection() as conn:cur=conn.execute("UPDATE dispatch_outbox SET status='delivery_unknown',detail='I/O process restarted while command was in sending state',completed_at_utc=? WHERE status='sending'",(utc_now(),));return int(cur.rowcount)
    def get_outbox(self,outbox_id:int)->Optional[sqlite3.Row]:
        with self.connection() as conn:return conn.execute('SELECT * FROM dispatch_outbox WHERE id=?',(outbox_id,)).fetchone()
    def heartbeat(self,process_name:str,*,pid:Optional[int],state:str,detail:str='')->None:
        with self.connection() as conn:conn.execute('INSERT INTO process_status(process_name,pid,state,detail,heartbeat_at_utc) VALUES(?,?,?,?,?) ON CONFLICT(process_name) DO UPDATE SET pid=excluded.pid,state=excluded.state,detail=excluded.detail,heartbeat_at_utc=excluded.heartbeat_at_utc',(process_name,pid,state,detail,utc_now()))
    def get_process_status(self)->list[sqlite3.Row]:
        with self.connection() as conn:return list(conn.execute('SELECT * FROM process_status ORDER BY process_name'))
    def record_evaluation(self,*,command_id:int,target_unserved_kw:float,target_surplus_kw:float,actual_unserved_kw:Optional[float]=None,actual_surplus_kw:Optional[float]=None)->int:
        with self.connection() as conn:cur=conn.execute('INSERT INTO dispatch_evaluation(command_id,target_unserved_kw,target_surplus_kw,actual_unserved_kw,actual_surplus_kw,evaluated_at_utc) VALUES(?,?,?,?,?,?)',(command_id,target_unserved_kw,target_surplus_kw,actual_unserved_kw,actual_surplus_kw,utc_now()));return int(cur.lastrowid)
    def record_wind_execution_evaluation(self,row:Mapping[str,object])->int:
        fields=('session_id','dispatch_command_id','outbox_id','c_wind_action_seq','dispatch_step','feedback_step','dispatch_created_at_utc','feedback_sampled_at_utc','wind_action_applied_at_utc','response_latency_s','b_wind_enable','b_wind_target_kw','c_controller_wind_enable','c_pitch_target_deg','c_wind_available_kw','c_wind_operating_limit_kw','a_wind_running','a_wind_actual_kw','a_pitch_actual_deg','a_fault','start_stop_score','power_tracking_score','pitch_response_score','capability_safety_score','total_score','verdict','reason','evaluated_at_utc'); vals=tuple(row.get(k) for k in fields); ph=','.join('?' for _ in fields)
        with self.connection() as conn:
            cur=conn.execute(f"INSERT INTO wind_execution_evaluation({','.join(fields)}) VALUES({ph}) ON CONFLICT(dispatch_command_id) DO UPDATE SET "+','.join(f'{f}=excluded.{f}' for f in fields if f!='dispatch_command_id'),vals); return int(cur.lastrowid or conn.execute('SELECT id FROM wind_execution_evaluation WHERE dispatch_command_id=?',(row['dispatch_command_id'],)).fetchone()[0])
    def get_wind_execution_history(self,*,session_id:Optional[str]=None,start_utc:Optional[str]=None,end_utc:Optional[str]=None,limit:int=200)->list[sqlite3.Row]:
        clauses=[];args=[]
        if session_id:clauses.append('session_id=?');args.append(session_id)
        if start_utc:clauses.append('feedback_sampled_at_utc>=?');args.append(start_utc)
        if end_utc:clauses.append('feedback_sampled_at_utc<=?');args.append(end_utc)
        where=' WHERE '+ ' AND '.join(clauses) if clauses else ''
        with self.connection() as conn:return list(conn.execute('SELECT * FROM wind_execution_evaluation'+where+' ORDER BY feedback_step DESC,id DESC LIMIT ?',args+[int(limit)]))
    def record_log(self,level:str,event_type:str,message:str,*,session_id:Optional[str]=None,step:Optional[int]=None)->None:
        with self.connection() as conn:conn.execute('INSERT INTO event_log(level,event_type,message,session_id,step,created_at_utc) VALUES(?,?,?,?,?,?)',(level,event_type,message,session_id,step,utc_now()))
