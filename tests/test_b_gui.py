"""Headless tests for B GUI direct TCP mode and connection controls.        
self.addCleanup(repo_patch.stop)        
repo_patch.start()        
self.window = gui_b.MainWindow()
    def tearDown(self):        
self.window.close()        
self.window.deleteLater()        
self.app.processEvents()
    def test_inline_public_port_is_persisted_and_connection_is_desired(self):        
with patch.object(self.window, "_begin_connection") as begin:            
self.window.host.setText("frp.example.com:38243")            
self.window.toggle_connection()
        self.assertEqual(            
self.window.repo.communication,            
{"host": "frp.example.com", "port": 38243, "enabled": 1},        
)        
self.assertTrue(self.window._connection_desired)        
self.assertEqual(self.window._connection_endpoint, ("frp.example.com", 38243))        
begin.assert_called_once()
    def test_disconnected_button_is_red_and_requests_are_disabled(self):        
self.window.set_connection_state(False)
        self.assertEqual(self.window.connect_btn.text(), "连接 A 服务器")        
self.assertEqual(self.window.connect_btn.property("kind"), "danger")        
self.assertEqual(self.window.a_status.text(), "A 未在线")        
self.assertFalse(self.window.request_btn.isEnabled())
    def test_connected_button_turns_green_and_requests_are_enabled(self):        
self.window.client = FakeConnectedClient()        
self.window._connection_desired = True        
self.window.set_connection_state(True)
        self.assertEqual(self.window.connect_btn.text(), "断开 A 服务器")        
self.assertEqual(self.window.connect_btn.property("kind"), "success")        
self.assertEqual(self.window.a_status.text(), "A 在线（GUI 直连）")        
self.assertTrue(self.window.request_btn.isEnabled())
    def test_direct_gui_connection_does_not_depend_on_b_io_heartbeat(self):        
self.window.repo.communication["enabled"] = 0        
self.window.client = FakeConnectedClient()        
self.window._connection_desired = True
        self.window.set_connection_state(True)
        self.assertTrue(self.window.client.connected)        
self.assertEqual(self.window.connect_btn.property("kind"), "success")        
self.assertEqual(self.window.a_status.text(), "A 在线（GUI 直连）")
    def test_scada_table_explicitly_labels_the_four_remote_categories(self):        
now = utc_now()        
self.window.state = GridState(            
session_id="gui-test", step=1, sim_time_s=1.0,            
wind_speed_mps=8.0, wind_available_kw=70.0,            
wind_operating_limit_kw=60.0, load_power_kw=80.0,            
wind_actual_kw=55.0, diesel_actual_kw=25.0,            
wind_running=True, diesel_running=True, fault=False,            
sampled_at_utc=now, received_at_utc=now,            
wind_target_kw=60.0, diesel_target_kw=20.0,            
pitch_actual_deg=0.0, power_imbalance_kw=0.0,        
)        
self.window.refresh_state_views()
        categories = {            
self.window.scada_table.item(row, 0).text()            
for row in range(self.window.scada_table.rowCount())        
}        
self.assertTrue({"YC 遥测", "YX 遥信", "YT 遥调", "YK 遥控"} <= categories)

if __name__ == "__main__":    
unittest.main()
