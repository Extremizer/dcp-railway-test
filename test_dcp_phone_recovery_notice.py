"""Offline tests for phone recovery invitation composition and delivery."""
import io,unittest
from unittest.mock import Mock
from dcp_phone_recovery_notice import recovery_url,message_payload,send_recovery_message,RecoveryNoticeError,RecoveryNoticeCoordinator

class NoticeTests(unittest.TestCase):
 def test_owner_webapp_link_and_no_token_in_message(self):
  url=recovery_url("https://example.com","AbcDEF0123_-")
  self.assertEqual(url,"https://example.com/recovery#AbcDEF0123_-")
  payload=message_payload(12345,url,"AUTH_REQUIRED")
  self.assertEqual(payload["chat_id"],12345)
  self.assertEqual(payload["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"],url)
  self.assertNotIn("AbcDEF0123_-",payload["text"])
 def test_status_fail_closed(self):
  with self.assertRaises(RecoveryNoticeError):message_payload(1,"https://example.com","READY")
  with self.assertRaises(RecoveryNoticeError):recovery_url("http://example.com","ticket")
  with self.assertRaises(RecoveryNoticeError):recovery_url("https://attacker@example.com","ticket")
 def test_delivery_mock_only(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):return False
   def read(self):return b'{"ok":true}'
  send=Mock(return_value=Response())
  p=message_payload(12345,"https://example.com/recovery#ticket","CLOUDFLARE")
  send_recovery_message(token="test:fake",payload=p,transport=send)
  self.assertEqual(send.call_count,1)
 def test_delivery_error_hides_secrets(self):
  def fail(*args,**kwargs):raise OSError("sensitive")
  with self.assertRaises(RecoveryNoticeError) as ctx:
   send_recovery_message(token="test:fake",payload={},transport=fail)
  self.assertEqual(str(ctx.exception),"telegram_transport_unknown")

class CoordinatorTests(unittest.TestCase):
 def test_blocked_status_once_per_cooldown(self):
  class Grants:
   def invite(self):return "SyntheticTicket"
  sent=[]
  c=RecoveryNoticeCoordinator(Grants(),12345,"https://example.com",sent.append,cooldown_seconds=60)
  self.assertFalse(c.notify("READY",now=1))
  self.assertTrue(c.notify("AUTH_REQUIRED",now=2))
  self.assertFalse(c.notify("CLOUDFLARE",now=3))
  self.assertTrue(c.notify("CLOUDFLARE",now=63))
  self.assertEqual(len(sent),2)

if __name__=="__main__":unittest.main()
