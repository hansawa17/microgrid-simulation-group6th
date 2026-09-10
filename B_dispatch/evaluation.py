"""Read-only EMS and wind-execution evaluation."""
from __future__ import annotations

from dataclasses import dataclass

from .wind_execution import WEIGHTS as WIND_EXECUTION_WEIGHTS, evaluate_pair, find_feedback, insufficient

EVALUATION_WEIGHTS={"balance":0.30,"wind":0.25,"diesel":0.15,"constraint":0.15,"tracking":0.15}
SCORING_RULES={
 "balance":{"title":"供需平衡","weight":30,"metric":"平均绝对功率不平衡","rules":["≤ 1 kW：100 分","≤ 3 kW：90 分","≤ 5 kW：80 分","≤ 10 kW：60 分","> 10 kW：40 分"]},
 "wind":{"title":"风能利用","weight":25,"metric":"实际风电 / 可利用风电","rules":["≥ 90%：100 分","≥ 80%：90 分","≥ 70%：80 分","≥ 60%：70 分","< 60%：按利用率计分，最低 40 分"]},
 "diesel":{"title":"柴油经济性","weight":15,"metric":"柴油实际供电 / 负荷","rules":["≤ 20%：100 分","≤ 30%：95 分","≤ 40%：85 分","≤ 50%：75 分","> 50%：60 分"]},
 "constraint":{"title":"运行约束","weight":15,"metric":"风电运行边界违反次数","rules":["0 次：100 分","每增加 1 次：扣 10 分","最低 0 分","检查 actual ≤ operating limit ≤ available"]},
 "tracking":{"title":"调度跟踪","weight":15,"metric":"已确认调度的目标与实际平均偏差","rules":["≤ 1 kW：100 分","≤ 3 kW：95 分","≤ 5 kW：85 分","≤ 10 kW：70 分","> 10 kW：50 分"]},
}

@dataclass(frozen=True)
class EvaluationResult:
 period_label:str; sample_count:int; balance_score:float; wind_score:float; diesel_score:float; constraint_score:float; tracking_score:float
 ems_score:float; system_score:float; overall_score:float; avg_balance_error_kw:float; wind_utilization_pct:float; diesel_share_pct:float
 constraint_violations:int; tracking_error_kw:float; dispatch_count:int; unserved_kw:float; surplus_kw:float
 wind_execution_count:int; wind_execution_score:float|None; wind_execution_verdict:str; wind_execution_reason:str
 @property
 def grade(self)->str:
  return "优秀" if self.overall_score>=90 else "良好" if self.overall_score>=80 else "合格" if self.overall_score>=70 else "需改进"

def _score_balance(e:float)->float:return 100.0 if e<=1 else 90.0 if e<=3 else 80.0 if e<=5 else 60.0 if e<=10 else 40.0
def _score_wind(u:float)->float:return 100.0 if u>=90 else 90.0 if u>=80 else 80.0 if u>=70 else 70.0 if u>=60 else max(40.0,u)
def _score_diesel(s:float)->float:return 100.0 if s<=20 else 95.0 if s<=30 else 85.0 if s<=40 else 75.0 if s<=50 else 60.0
def _score_tracking(e:float)->float:return 100.0 if e<=1 else 95.0 if e<=3 else 85.0 if e<=5 else 70.0 if e<=10 else 50.0

def _window_sql(period:str):
 m={"1m":1,"5m":5}.get(period)
 return m

def evaluate_repository(repo,period:str="5m")->EvaluationResult:
 m=_window_sql(period)
 with repo.connection() as conn:
  if period=="session":
   states=conn.execute("SELECT * FROM state_history WHERE session_id=(SELECT session_id FROM current_state WHERE id=1) ORDER BY id").fetchall(); commands=conn.execute("SELECT * FROM dispatch_commands WHERE session_id=(SELECT session_id FROM current_state WHERE id=1) ORDER BY id").fetchall(); label="本次 Session"
  elif m is None:
   states=conn.execute("SELECT * FROM state_history ORDER BY id").fetchall(); commands=conn.execute("SELECT * FROM dispatch_commands ORDER BY id").fetchall(); label="全部历史"
  else:
   window=f"-{m} minutes"; states=conn.execute("SELECT * FROM state_history WHERE julianday(received_at_utc)>=julianday('now',?) ORDER BY id",(window,)).fetchall(); commands=conn.execute("SELECT * FROM dispatch_commands WHERE julianday(created_at_utc)>=julianday('now',?) ORDER BY id",(window,)).fetchall(); label=f"最近 {m} 分钟"
  tracking_rows=conn.execute("SELECT c.wind_target_kw,c.diesel_target_kw,s.wind_actual_kw,s.diesel_actual_kw FROM dispatch_commands c JOIN state_history s ON s.session_id=c.session_id AND s.step=c.step WHERE c.ack_accepted=1").fetchall()
  if states:
   avg_balance=sum(abs(float(r['load_power_kw'])-float(r['wind_actual_kw'])-float(r['diesel_actual_kw'])) for r in states)/len(states); avail=sum(float(r['wind_available_kw']) for r in states); wind_util=(sum(float(r['wind_actual_kw']) for r in states)/avail*100 if avail>1e-9 else 0.0); load=sum(float(r['load_power_kw']) for r in states); diesel_share=(sum(float(r['diesel_actual_kw']) for r in states)/load*100 if load>1e-9 else 0.0)
   unserved=sum(max(float(r['load_power_kw'])-float(r['wind_actual_kw'])-float(r['diesel_actual_kw']),0.0) for r in states)/len(states); surplus=sum(max(float(r['wind_actual_kw'])+float(r['diesel_actual_kw'])-float(r['load_power_kw']),0.0) for r in states)/len(states)
   violations=sum(1 for r in states if float(r['wind_operating_limit_kw'])>float(r['wind_available_kw'])+1e-6 or float(r['wind_actual_kw'])>float(r['wind_operating_limit_kw'])+1e-6)
  else: avg_balance=wind_util=diesel_share=unserved=surplus=0.0; violations=0
  tracking_error=(sum((abs(float(r['wind_target_kw'])-float(r['wind_actual_kw']))+abs(float(r['diesel_target_kw'])-float(r['diesel_actual_kw'])))/2 for r in tracking_rows)/len(tracking_rows)) if tracking_rows else 0.0
  age=float(repo.get_runtime_config()['max_state_age_s'])
  exec_rows=[]
  exec_cmds=[r for r in commands if r['status']=='accepted' and (r['ack_accepted'] in (1,True))]
  for cmd in exec_cmds:
   fb,reason=find_feedback(conn,cmd,max_age_s=age)
   result=insufficient(cmd,reason) if fb is None else evaluate_pair(cmd,fb)
   if fb is not None: exec_rows.append(result)

 b=_score_balance(avg_balance); w=_score_wind(wind_util); d=_score_diesel(diesel_share); c=100.0 if violations==0 else max(0.0,100.0-10.0*violations); t=_score_tracking(tracking_error)
 overall=0.30*b+0.25*w+0.15*d+0.15*c+0.15*t
 ems=0.35*b+0.25*w+0.15*d+0.10*c+0.15*t
 if exec_rows:
  exec_score=sum(float(x.total_score) for x in exec_rows if x.total_score is not None)/len(exec_rows); exec_verdict="pass" if exec_score>=70 else "needs_improvement"; exec_reason=f"{len(exec_rows)} 条完整 B→C→A 反馈，按小组评价规则汇总"
 else: exec_score=None; exec_verdict="insufficient_data"; exec_reason="无完整 B dispatch→C action→A 后续 state 反馈"
 # Keep the legacy card field, but make it the independent wind-execution result when available.
 system=exec_score if exec_score is not None else 0.0
 return EvaluationResult(label,len(states),b,w,d,c,t,ems,system,overall,avg_balance,wind_util,diesel_share,violations,tracking_error,len(commands),unserved,surplus,len(exec_rows),exec_score,exec_verdict,exec_reason)

WIND_EXECUTION_WEIGHTS=WIND_EXECUTION_WEIGHTS
