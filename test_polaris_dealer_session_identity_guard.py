import unittest
from polaris_dealer_session_identity_guard import verify_dealer_session

URL='https://www.dealercostparts.com/cart'
TAB={'type':'page','url':URL}

def check(status='READY',name='SYNTHETIC',expected='SYNTHETIC',logout=True):
    return verify_dealer_session(expected=expected,preflight=lambda:status,tabs=lambda:[TAB],evaluate=lambda tab,script:{'before':URL,'after':URL,'names':[name] if name else [],'logout':logout})

class GuardTests(unittest.TestCase):
    def test_accept(self): self.assertTrue(check()['dealer_context_verified'])
    def test_auth(self): self.assertFalse(check(status='AUTH_REQUIRED')['dealer_context_verified'])
    def test_challenge(self): self.assertFalse(check(status='CLOUDFLARE')['dealer_context_verified'])
    def test_empty_config(self): self.assertFalse(check(expected='')['dealer_context_verified'])
    def test_missing_marker(self): self.assertFalse(check(name='')['dealer_context_verified'])
    def test_mismatch(self): self.assertFalse(check(name='OTHER')['dealer_context_verified'])
    def test_logout_not_sufficient(self): self.assertFalse(check(name='OTHER',logout=True)['dealer_context_verified'])
    def test_case_normalized(self): self.assertTrue(check(name='synthetic')['dealer_context_verified'])
    def test_spaces_normalized(self): self.assertTrue(check(name=' SYNTHETIC ')['dealer_context_verified'])
    def test_no_actual_disclosure(self): self.assertNotIn('OTHER',str(check(name='OTHER')))
    def test_no_expected_disclosure(self): self.assertNotIn('SYNTHETIC',str(check(name='OTHER')))
    def test_missing_env_config(self):
        from unittest.mock import patch
        with patch.dict('os.environ',{},clear=True):
            result=verify_dealer_session(preflight=lambda:'READY')
        self.assertEqual(result['status'],'DEALER_IDENTITY_CONFIG_MISSING')
    def test_conflicting_markers(self):
        result=verify_dealer_session(expected='SYNTHETIC',preflight=lambda:'READY',
            tabs=lambda:[TAB],evaluate=lambda tab,script:{'before':URL,'after':URL,'names':['SYNTHETIC','OTHER'],'logout':True})
        self.assertFalse(result['dealer_context_verified'])
    def test_actual_not_logged(self):
        import io
        from contextlib import redirect_stdout,redirect_stderr
        stream=io.StringIO()
        with redirect_stdout(stream),redirect_stderr(stream):
            check(name='OTHER')
        self.assertNotIn('OTHER',stream.getvalue())
    def test_expected_not_logged(self):
        import io
        from contextlib import redirect_stdout,redirect_stderr
        stream=io.StringIO()
        with redirect_stdout(stream),redirect_stderr(stream):
            check(expected='SYNTHETIC')
        self.assertNotIn('SYNTHETIC',stream.getvalue())
    def test_logoff_required(self): self.assertFalse(check(logout=False)['dealer_context_verified'])

if __name__=='__main__': unittest.main()
