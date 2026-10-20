import tempfile,unittest
from unittest.mock import Mock
from dcp_phone_staging_notice_once import send_once,StagingNoticeBlocked

class OnceTest(unittest.TestCase):
 def test_only_once_with_persistent_marker(self):
  with tempfile.TemporaryDirectory() as p:
   c=Mock();c.notify.return_value=True
   self.assertEqual(send_once(coordinator=c,state_dir=p,request_id="test-request-001",browser_status="MOCK_ONLY"),"SENT")
   self.assertEqual(send_once(coordinator=c,state_dir=p,request_id="test-request-001",browser_status="MOCK_ONLY"),"ALREADY_ATTEMPTED")
   c.notify.assert_called_once_with("AUTH_REQUIRED")
 def test_fail_closed(self):
  with tempfile.TemporaryDirectory() as p:
   c=Mock()
   for status,request in [("READY","test-request-001"),("MOCK_ONLY","x")]:
    with self.assertRaises(StagingNoticeBlocked):
     send_once(coordinator=c,state_dir=p,request_id=request,browser_status=status)
   c.notify.assert_not_called()
