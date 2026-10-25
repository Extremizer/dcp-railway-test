import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from fastapi.testclient import TestClient
import dcp_cloud_browser_host as host
import dcp_phone_vps_app as native
import dcp_phone_vps_runtime as runtime
from dcp_phone_recovery_core import RecoveryGrants

class PathTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.page=self.root/'dcp_phone_staging_mock.html'
        self.page.write_bytes(Path(host.__file__).with_name(self.page.name).read_bytes())
        self.env={'DCP_STAGING_MOCK_HTML':str(self.page),'DCP_CLOUD_STATE_DIR':str(self.root)}
    def tearDown(self):self.tmp.cleanup()
    def test_docker_default_unchanged(self):self.assertEqual(host.mock_page_uri({}),'file:///app/dcp_phone_staging_mock.html')
    def test_native_copy_must_match_source(self):self.assertEqual(host.mock_page_uri(self.env),self.page.as_uri())
    def test_modified_mock_is_rejected(self):
        self.page.write_text('changed')
        with self.assertRaises(host.HostConfigurationError):host.mock_page_uri(self.env)
    def test_outside_state_is_rejected(self):
        self.env['DCP_CLOUD_STATE_DIR']=str(self.root/'other')
        with self.assertRaises(host.HostConfigurationError):host.mock_page_uri(self.env)
    def test_symlink_is_rejected(self):
        actual=self.page.rename(self.root/'source.html');self.page.symlink_to(actual)
        with self.assertRaises(host.HostConfigurationError):host.mock_page_uri(self.env)
    def test_native_http_bind_is_loopback(self):
        env={**self.env,'DCP_CLOUD_BROWSER_HOST_ENABLED':'1','DCP_CLOUD_PRODUCER_ENABLED':'0','DCP_PHONE_RECOVERY_ENABLED':'1',
             'DCP_RECOVERY_TELEGRAM_BOT_TOKEN':'test:fake','DCP_RECOVERY_OWNER_ID':'123','DISPLAY':':99',
             'DCP_CHROMIUM_EXECUTABLE':str(self.page),'DCP_RECOVERY_BIND_HOST':'127.0.0.1'}
        state=self.root/'state';state.mkdir();env['DCP_CLOUD_STATE_DIR']=str(state)
        self.assertEqual(host.configuration(env,platform='linux')['bind'],'127.0.0.1')
        env['DCP_RECOVERY_BIND_HOST']='attacker.example'
        with self.assertRaises(host.HostConfigurationError):host.configuration(env,platform='linux')

class RuntimeTests(unittest.TestCase):
    def test_browser_wrapper_does_not_inherit_secrets_or_disable_sandbox(self):
        wrapper=Path(__file__).with_name("deploy").joinpath("phone-first/chromium-snap.sh").read_text()
        self.assertIn("exec env -i",wrapper)
        self.assertNotIn("--no-sandbox",wrapper)
        self.assertNotIn("DCP_RECOVERY_TELEGRAM_BOT_TOKEN",wrapper)
    def test_root_is_rejected(self):
        with patch.object(os,'geteuid',return_value=0):
            with self.assertRaisesRegex(host.HostConfigurationError,'nonroot'):runtime.validate({})
    def test_live_or_producer_flags_are_rejected(self):
        base={'DCP_PHONE_STAGING_MOCK_ONLY':'1','DCP_CLOUD_PRODUCER_ENABLED':'0','DCP_LIVE_NAVIGATION_GO':'0','DCP_RECOVERY_BIND_HOST':'127.0.0.1'}
        for key,value in [('DCP_LIVE_NAVIGATION_GO','1'),('DCP_CLOUD_PRODUCER_ENABLED','1'),('DCP_PHONE_STAGING_MOCK_ONLY','0'),('DCP_RECOVERY_BIND_HOST','0.0.0.0'),('DCP_STAGING_TEST_NOTICE_ID','old-notice')]:
            with self.subTest(key=key),patch.object(os,'geteuid',return_value=1001):
                with self.assertRaisesRegex(host.HostConfigurationError,'offline'):runtime.validate({**base,key:value})
    def test_source_identity_is_required(self):
        env={'DCP_PHONE_STAGING_MOCK_ONLY':'1','DCP_CLOUD_PRODUCER_ENABLED':'0','DCP_LIVE_NAVIGATION_GO':'0','DCP_RECOVERY_BIND_HOST':'127.0.0.1'}
        with patch.object(os,'geteuid',return_value=1001):
            with self.assertRaisesRegex(host.HostConfigurationError,'source'):runtime.validate(env)

class InviteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.env={'DCP_VPS_CONTROL_TOKEN':'a'*40,'DCP_PHONE_STAGING_MOCK_ONLY':'1','DCP_CLOUD_PRODUCER_ENABLED':'0','DCP_LIVE_NAVIGATION_GO':'0','DCP_CLOUD_STATE_DIR':str(self.root)}
        self.client=TestClient(native.app,client=('127.0.0.1',12345))
        self.sender=Mock(return_value=True)
        self.coordinator=Mock();self.coordinator.notify=self.sender
        self.browser=Mock();self.browser.status.return_value='MOCK_ONLY'
        self.patch=patch.multiple(native.app.state,coordinator=self.coordinator,browser=self.browser,grants=RecoveryGrants('test:fake',123))
        self.patch.start();self.environ=patch.dict(os.environ,self.env);self.environ.start()
    def tearDown(self):self.environ.stop();self.patch.stop();self.tmp.cleanup()
    def post(self,headers=None):return self.client.post('/_ops/invite',json={'request_id':'new-vps-test-id'},headers=headers or {})
    def test_missing_control_token_cannot_send(self):
        self.assertEqual(self.post().status_code,404);self.sender.assert_not_called()
    def test_wrong_control_token_cannot_send(self):
        self.assertEqual(self.post({'X-Extremizer-Control':'wrong'}).status_code,404);self.sender.assert_not_called()
    def test_nonloopback_cannot_send_even_with_token(self):
        c=TestClient(native.app,client=('203.0.113.10',12345))
        self.assertEqual(c.post('/_ops/invite',json={'request_id':'new-vps-test-id'},headers={'X-Extremizer-Control':'a'*40}).status_code,404)
        self.sender.assert_not_called()
    def test_new_request_sent_once_and_never_replayed(self):
        h={'X-Extremizer-Control':'a'*40}
        self.assertEqual(self.post(h).json()['result'],'SENT')
        self.assertEqual(self.post(h).json()['result'],'ALREADY_ATTEMPTED')
        self.sender.assert_called_once_with('AUTH_REQUIRED')
    def test_uncertain_delivery_cannot_retry(self):
        self.sender.side_effect=RuntimeError('transport')
        h={'X-Extremizer-Control':'a'*40}
        self.assertTrue(self.post(h).json()['result'].startswith('DIAG_'))
        self.assertEqual(self.post(h).json()['result'],'ALREADY_ATTEMPTED')
        self.assertEqual(self.sender.call_count,1)
    def test_health_tracks_browser_availability(self):
        self.assertEqual(self.client.get("/health").status_code,200)
        self.browser.status.return_value="BROWSER_DOWN"
        r=self.client.get("/health")
        self.assertEqual(r.status_code,503);self.assertFalse(r.json()["ready"])
    def test_public_frame_is_unauthorized(self):self.assertEqual(self.client.get('/recovery/frame').status_code,401)
    def test_forged_telegram_identity_is_unauthorized(self):
        r=self.client.post('/recovery/exchange',json={'ticket':'a'*43,'init_data':'user=123&hash=forged'})
        self.assertEqual(r.status_code,401)
    def test_live_configuration_cannot_issue_invite(self):
        with patch.dict(os.environ,{'DCP_LIVE_NAVIGATION_GO':'1'}):
            self.assertEqual(self.post({'X-Extremizer-Control':'a'*40}).status_code,503)
        self.sender.assert_not_called()

if __name__=='__main__':unittest.main()
