"""Disposable regression for Admin Acting-As CONTRACT v1.4."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import tempfile
import unittest

import acting_as_v14 as aa


class ActingAsV14Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=str(Path(self.tmp.name)/"audit.db")
        self.future=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
        aa.start_session(
            self.db,session_id="s1",actual_admin_id="admin-1",
            effective_subject_type="dealer",effective_subject_id="dealer-A",
            effective_role="dealer",data_scope="dealer:dealer-A",
            reason="support request",expires_at=self.future
        )

    def test_actual_and_effective_subject_are_both_preserved(self):
        aa.authorize(
            self.db,session_id="s1",action="ORDER_CREATE",
            resource_scope="dealer:dealer-A",
            admin_permissions={"ACT_AS","ORDER_CREATE"},
            effective_permissions={"ORDER_CREATE"},
            business_guard_passed=True
        )
        event=aa.record_event(
            self.db,session_id="s1",action="ORDER_CREATE",
            resource_type="order",resource_id="E01A-1",
            result="success",details={"amount":"100.00"}
        )
        self.assertGreater(event,0)
        row=aa.audit_events(self.db,"s1")[0]
        self.assertEqual(row["actual_actor_id"],"admin-1")
        self.assertEqual(row["effective_subject_id"],"dealer-A")
        self.assertEqual(row["reason"],"support request")

    def test_acting_as_never_bypasses_business_guard(self):
        with self.assertRaisesRegex(PermissionError,"business guard"):
            aa.authorize(
                self.db,session_id="s1",action="PAYMENT_CONFIRM",
                resource_scope="dealer:dealer-A",
                admin_permissions={"ACT_AS","ACT_AS_CRITICAL","PAYMENT_CONFIRM"},
                effective_permissions={"PAYMENT_CONFIRM"},
                business_guard_passed=False,critical=True
            )

    def test_critical_finance_action_needs_extra_admin_permission(self):
        with self.assertRaisesRegex(PermissionError,"ACT_AS_CRITICAL"):
            aa.authorize(
                self.db,session_id="s1",action="PAYMENT_CONFIRM",
                resource_scope="dealer:dealer-A",
                admin_permissions={"ACT_AS","PAYMENT_CONFIRM"},
                effective_permissions={"PAYMENT_CONFIRM"},
                business_guard_passed=True,critical=True
            )

    def test_effective_subject_permissions_still_apply(self):
        with self.assertRaisesRegex(PermissionError,"effective subject"):
            aa.authorize(
                self.db,session_id="s1",action="REFUND",
                resource_scope="dealer:dealer-A",
                admin_permissions={"ACT_AS","ACT_AS_CRITICAL","REFUND"},
                effective_permissions=set(),
                business_guard_passed=True,critical=True
            )

    def test_scope_isolation(self):
        with self.assertRaisesRegex(PermissionError,"outside"):
            aa.authorize(
                self.db,session_id="s1",action="ORDER_VIEW",
                resource_scope="dealer:dealer-B",
                admin_permissions={"ACT_AS","ORDER_VIEW"},
                effective_permissions={"ORDER_VIEW"},
                business_guard_passed=True
            )

    def test_exit_ends_effective_session(self):
        aa.end_session(self.db,session_id="s1",actual_admin_id="admin-1")
        with self.assertRaisesRegex(ValueError,"not active"):
            aa.get_active_session(self.db,"s1")

    def test_other_admin_cannot_end_session(self):
        with self.assertRaisesRegex(ValueError,"actual admin"):
            aa.end_session(self.db,session_id="s1",actual_admin_id="admin-2")

    def test_nested_impersonation_is_prohibited(self):
        with self.assertRaisesRegex(ValueError,"nested impersonation"):
            aa.start_session(
                self.db,session_id="nested",actual_admin_id="admin-1",
                effective_subject_type="client",effective_subject_id="c1",
                effective_role="client",data_scope="client:c1",
                reason="support",expires_at=self.future,
                parent_impersonation_session_id="s1"
            )

    def test_reason_is_mandatory(self):
        with self.assertRaisesRegex(ValueError,"reason required"):
            aa.start_session(
                self.db,session_id="bad",actual_admin_id="admin-1",
                effective_subject_type="client",effective_subject_id="c1",
                effective_role="client",data_scope="client:c1",
                reason="",expires_at=self.future
            )


if __name__=="__main__":
    unittest.main()
