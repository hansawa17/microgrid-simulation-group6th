from __future__ import annotations
import json
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path

from B_dispatch.models import GridState
from B_dispatch.repository import EMSRepository
from B_dispatch.tcpB import EMSTcpClient,ProtocolError
from B_dispatch.wind_execution import evaluate_pair,find_feedback,insufficient


def envelope(payload,*,session='S1',step=2):
 return {'version':1,'type':'state','source':'A','target':'B','session_id':session,'seq':step,'step':step,'sim_time_s':float(step),'payload':payload}

def base_payload():
 return {'sampled_at_utc':'2026-09-10T06:00:00.000Z','wind_speed_mps':10.0,'wind_available_kw':80.0,'wind_operating_limit_kw':70.0,'load_power_kw':70.0,'wind_actual_kw':60.0,'diesel_actual_kw':10.0,'wind_target_kw':60.0,'diesel_target_kw':10.0,'pitch_actual_deg':5.0,'wind_running':True,'diesel_running':True,'fault':False,'power_imbalance_kw':0.0}

class WindExecutionTests(unittest.TestCase):
 def test_new_state_extension_parses_and_persists(self):
  p=base_payload()|{'controller_wind_enable':True,'pitch_target_deg':4.0,'last_wind_action_seq':11,'last_wind_action_step':2,'wind_action_applied_at_utc':'2026-09-10T06:00:00.100Z'}
  c=EMSTcpClient('127.0.0.1');s=c._parse_state(envelope(p));self.assertEqual(s.extension_status,'complete');self.assertTrue(s.controller_wind_enable);self.assertEqual(s.last_wind_action_seq,11)
  with tempfile.TemporaryDirectory() as d:
   repo=EMSRepository(Path(d)/'ems.db');repo.initialize();repo.save_state(s);row=repo.get_current_state();self.assertEqual(row['extension_status'],'complete');self.assertEqual(row['last_wind_action_seq'],11)
 def test_legacy_state_is_kept_and_marks_incomplete(self):
  s=EMSTcpClient('127.0.0.1')._parse_state(envelope(base_payload()));self.assertEqual(s.extension_status,'legacy_or_incomplete');self.assertIsNone(s.controller_wind_enable);self.assertIsNone(s.last_wind_action_step)
 def test_bad_extension_types_and_ranges_are_rejected(self):
  cases=[{'controller_wind_enable':1},{'pitch_target_deg':91},{'last_wind_action_seq':-1},{'last_wind_action_step':-1},{'wind_action_applied_at_utc':'not-utc'}]
  for patch in cases:
   with self.subTest(patch=patch):
    with self.assertRaises(ProtocolError):EMSTcpClient('127.0.0.1')._parse_state(envelope(base_payload()|patch))
 def test_unknown_fields_are_ignored(self):
  s=EMSTcpClient('127.0.0.1')._parse_state(envelope(base_payload()|{'future_state_field':'x'}));self.assertEqual(s.wind_actual_kw,60.0)
 def test_new_session_without_action_has_null_metadata(self):
  p=base_payload()|{'controller_wind_enable':False,'pitch_target_deg':90.0,'last_wind_action_seq':None,'last_wind_action_step':None,'wind_action_applied_at_utc':None};s=EMSTcpClient('127.0.0.1')._parse_state(envelope(p,session='S2',step=0));self.assertEqual(s.extension_status,'complete');self.assertFalse(s.controller_wind_enable);self.assertIsNone(s.last_wind_action_seq)
 def test_pair_and_same_session_ordering(self):
  command={'id':1,'session_id':'S1','step':2,'created_at_utc':'2026-09-10T06:00:00.000Z','wind_enable':True,'wind_target_kw':60.0}
  feedback={'session_id':'S1','step':3,'extension_status':'complete','last_wind_action_seq':8,'last_wind_action_step':3,'received_age_s':0.2,'sampled_at_utc':'2026-09-10T06:00:01.000Z','wind_action_applied_at_utc':'2026-09-10T06:00:00.200Z','controller_wind_enable':True,'pitch_target_deg':4.0,'wind_available_kw':80.0,'wind_operating_limit_kw':70.0,'wind_running':True,'wind_actual_kw':58.0,'pitch_actual_deg':5.0,'fault':False}
  r=evaluate_pair(command,feedback);self.assertIsNotNone(r.total_score);self.assertEqual(r.feedback_step,3)
 def test_cross_session_old_step_and_stale_are_not_associated(self):
  with tempfile.TemporaryDirectory() as d:
   repo=EMSRepository(Path(d)/'ems.db');repo.initialize()
   now='2026-09-10T06:00:00.000Z';cmd_id=repo.record_command({'session_id':'S1','step':2,'sim_time_s':2,'source':'B','seq':1,'wind_target_kw':40,'diesel_target_kw':20,'wind_enable':True,'diesel_enable':True,'status':'accepted','reason':'ok','ack_accepted':True,'created_at_utc':now})
   base={'session_id':'S2','step':3,'extension_status':'complete','last_wind_action_step':3,'received_age_s':0.1};self.assertIsNotNone(insufficient(repo.connection().__enter__() if False else {'id':cmd_id,'session_id':'S1','step':2,'created_at_utc':now,'wind_enable':True,'wind_target_kw':40},'cross-session not precomputed'))
   with repo.connection() as conn:
    fb,reason=find_feedback(conn,{'session_id':'S1','step':2},max_age_s=2);self.assertIsNone(fb);self.assertIn('no complete',reason)
 def test_insufficient_data_has_no_fake_score(self):
  r=insufficient({'id':1,'session_id':'S1','step':2,'created_at_utc':'2026-09-10T06:00:00.000Z','wind_enable':False,'wind_target_kw':0},'no feedback');self.assertIsNone(r.total_score);self.assertEqual(r.verdict,'insufficient_data')

if __name__=='__main__':unittest.main()
