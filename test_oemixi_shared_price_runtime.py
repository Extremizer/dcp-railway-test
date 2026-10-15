"""Actual app binding/handler; no polling, real Telegram, browser or production DB."""
import ast
import asyncio
import hashlib
import socket
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import oemixibot_app as app
from dcp_price_gateway import SharedDealerPriceAdapter

KEY=app._SHARED_PRICE_ADAPTER_KEY
ROOT=Path(__file__).resolve().parent


class RuntimeGatewayTests(unittest.TestCase):
    def setUp(self):
        self.network=patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden'))
        self.network.start();self.addCleanup(self.network.stop)
        self.store=Mock();self.store.cart_view.return_value={'items':[],'total_usd':0}
        p=patch.object(app,'STORE',self.store);p.start();self.addCleanup(p.stop)

    def test_build_default_remains_legacy_no_automatic_activation(self):
        runtime=app.build_application(token='123456:isolated-test-token')
        self.assertNotIn(KEY,runtime.bot_data)

    def test_build_explicit_gateway_binds_existing_adapter_only(self):
        fn=Mock()
        runtime=app.build_application(token='123456:isolated-test-token',
            shared_price_gateway=fn,dealer_max_age_hours=1)
        self.assertIsInstance(runtime.bot_data[KEY],SharedDealerPriceAdapter)
        self.assertIs(runtime.bot_data[KEY].gateway,fn)
        self.assertEqual(runtime.bot_data[KEY].max_age_hours,1)
        fn.assert_not_called()

    def test_missing_invalid_dealer_policy_fails_before_store_init(self):
        for policy in (None,0,-1,True,float('inf'),float('nan')):
            with self.assertRaises(ValueError):
                app.build_application(token='123456:isolated-test-token',
                    shared_price_gateway=Mock(),dealer_max_age_hours=policy)
        self.store.init.assert_not_called()
        with self.assertRaises(ValueError):
            app.build_application(token='123456:isolated-test-token',dealer_max_age_hours=1)
        self.store.init.assert_not_called()

    def test_application_bindings_are_independent_and_no_hot_rebind(self):
        a,b=SimpleNamespace(bot_data={}),SimpleNamespace(bot_data={})
        ga,gb=Mock(),Mock()
        app.bind_shared_price_gateway(a,ga,dealer_max_age_hours=1)
        app.bind_shared_price_gateway(b,gb,dealer_max_age_hours=2)
        self.assertIs(a.bot_data[KEY].gateway,ga)
        self.assertIs(b.bot_data[KEY].gateway,gb)
        with self.assertRaises(ValueError):app.bind_shared_price_gateway(a,gb,dealer_max_age_hours=2)
        self.assertIs(a.bot_data[KEY].gateway,ga)

    def test_running_application_cannot_enable_gateway_mid_request(self):
        application=SimpleNamespace(bot_data={},running=True)
        with self.assertRaises(ValueError):
            app.bind_shared_price_gateway(application,Mock(),dealer_max_age_hours=1)
        self.assertEqual(application.bot_data,{})

    def test_missing_or_corrupt_bound_adapter_never_reverts_to_direct_path(self):
        for value in (None,object(),Mock()):
            context=SimpleNamespace(application=SimpleNamespace(bot_data={KEY:value}))
            with patch.object(app,'resolve_offer',side_effect=AssertionError('legacy forbidden')):
                result=asyncio.run(app._resolve_runtime_offer('2670-265',context))
            self.assertEqual(result['status'],'TECHNICAL_ERROR')
            self.assertIsNone(result['dl_usd'])

    def test_unconfigured_application_preserves_legacy_call(self):
        context=SimpleNamespace(application=SimpleNamespace(bot_data={}))
        with patch.object(app,'resolve_offer',return_value={'status':'NOT_FOUND'}) as legacy:
            self.assertEqual(asyncio.run(app._resolve_runtime_offer('2670-265',context))['status'],'NOT_FOUND')
        legacy.assert_called_once_with('2670-265')

    def test_identity_retry_handler_keeps_bound_adapter(self):
        application=SimpleNamespace(bot_data={})
        bound=app.bind_shared_price_gateway(application,None,dealer_max_age_hours=1)
        context=SimpleNamespace(application=application,user_data={})
        reply=AsyncMock()
        update=SimpleNamespace(effective_user=None,
            effective_message=SimpleNamespace(text='2670-265',reply_text=reply))
        offers=Mock(side_effect=[{'status':'IDENTITY_REQUIRED','oem':'2670-265'},
                                {'status':'TECHNICAL_ERROR','oem':'2670-265','dl_usd':None}])
        with patch.object(app,'_dealer',return_value={'id':'synthetic','price_coefficient':1}), \
             patch.object(app,'resolve_offer',offers), \
             patch.object(app,'lookup_shared_identity',return_value={'manufacturer':'Arctic Cat'}), \
             patch.object(app.oem_identity_service,'remember',return_value=True):
            asyncio.run(app.oem_message(update,context))
        self.assertEqual(offers.call_count,2)
        for call in offers.call_args_list:self.assertIs(call.kwargs['price_gateway'],bound)
        self.store.cart_add.assert_not_called()

    def test_all_sixteen_oem_handler_call_sites_use_runtime_boundary(self):
        tree=ast.parse((ROOT/'oemixibot_app.py').read_text())
        handler=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='oem_message')
        calls=[n for n in ast.walk(handler) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
        self.assertEqual(sum(c.func.id=='_resolve_runtime_offer' for c in calls),16)
        self.assertEqual(sum(c.func.id=='resolve_offer' for c in calls),0)

    def test_scope_bytes_outside_binding_and_call_sites_preserved(self):
        source=(ROOT/'oemixibot_app.py').read_text()
        source=source.replace('from dcp_price_gateway import SharedDealerPriceAdapter\n','')
        start=source.index('\n_LEGACY_PRICE_PATH = object()')
        end=source.index('\ndef _dealer(update):',start)
        source=source[:start]+source[end:]
        source=source.replace('offer=await _resolve_runtime_offer(oem, context)','offer=resolve_offer(oem)')
        start=source.index('def build_application(')
        end=source.index('    STORE.init(); STORE.ensure_cart_schema()',start)
        source=source[:start]+'def build_application(token=None):\n'+source[end:]
        source=source.replace('    if adapter is not None:\n        app.bot_data[_SHARED_PRICE_ADAPTER_KEY] = adapter\n','')
        data=source.encode()
        blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        self.assertEqual(blob,'d8a6180ecb1759d6d225b4c4564e7c2750031cbe')  # PR38 + approved admin OEM-search scope


if __name__=='__main__': unittest.main()
