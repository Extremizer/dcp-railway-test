from decimal import Decimal, ROUND_HALF_UP
import sqlite3

CENT=Decimal('0.01')
def money(v): return Decimal(str(v or 0)).quantize(CENT,rounding=ROUND_HALF_UP)

class FinanceEngine:
    def __init__(self,db_path): self.db_path=db_path
    def _db(self):
        c=sqlite3.connect(self.db_path); c.row_factory=sqlite3.Row; return c
    def balance(self,buyer_id,buyer_type='dealer',currency='USD'):
        with self._db() as c:
            rows=c.execute('SELECT amount FROM finance_ledger WHERE buyer_type=? AND buyer_id=? AND wallet_currency=? ORDER BY id',(buyer_type,buyer_id,currency)).fetchall()
        return money(sum((Decimal(str(r['amount'])) for r in rows),Decimal('0')))
    def credit_limit(self,buyer_id,buyer_type='dealer',currency='USD'):
        """Deprecated compatibility shim: credit never contributes to capacity."""
        return Decimal('0.00')
    def available_to_order(self,buyer_id,buyer_type='dealer',currency='USD'):
        if buyer_type=='dealer':
            return self.order_capacity(buyer_id,buyer_type,currency)['available_to_order']
        return self.balance(buyer_id,buyer_type,currency)

    def order_capacity(self,buyer_id,buyer_type='dealer',currency='USD',conn=None):
        if buyer_type!='dealer' or currency!='USD': raise ValueError('order capacity supports dealer/USD only')
        own=conn is None
        c=self._db() if own else conn
        try:
            dealer=c.execute('SELECT active FROM dealers WHERE id=?',(buyer_id,)).fetchone()
            if not dealer or not dealer['active']: raise ValueError('unknown or inactive dealer')
            rows=c.execute('SELECT amount FROM finance_ledger WHERE buyer_type=? AND buyer_id=? AND wallet_currency=? ORDER BY id',(buyer_type,buyer_id,currency)).fetchall()
            balance=money(sum((Decimal(str(r['amount'])) for r in rows),Decimal('0')))
            credit=Decimal('0.00')
            available=balance
            return {'balance':balance,'credit_limit':credit,'available_to_order':available}
        finally:
            if own: c.close()

    def require_order_capacity(self,*,buyer_id,amount,buyer_type='dealer',currency='USD',conn=None):
        required=money(amount)
        if not required.is_finite() or required<=0: raise ValueError('order amount must be finite and positive')
        cap=self.order_capacity(buyer_id,buyer_type,currency,conn=conn)
        available=cap['available_to_order']
        if required>available:
            raise ValueError(f'INSUFFICIENT_AVAILABLE:{available}:{required}')
        return dict(cap,required=required,remaining=money(available-required))

    def history(self,buyer_id,buyer_type='dealer',currency='USD',from_at=None,to_at=None,limit=100):
        if buyer_type not in {'client','dealer'}: raise ValueError('invalid buyer_type')
        if currency not in {'RUB','USD'}: raise ValueError('invalid wallet currency')
        if not buyer_id or not str(buyer_id).strip(): raise ValueError('buyer_id required')
        if isinstance(limit,bool) or not isinstance(limit,int) or limit<1 or limit>500: raise ValueError('limit must be 1..500')
        def valid_ts(value,name):
            if value is None: return None
            value=str(value).strip()
            if not value: raise ValueError(name+' must not be empty')
            from datetime import datetime
            try: datetime.fromisoformat(value.replace('Z','+00:00'))
            except ValueError as e: raise ValueError(name+' must be ISO-8601') from e
            return value
        from_at=valid_ts(from_at,'from_at'); to_at=valid_ts(to_at,'to_at')
        if from_at and to_at:
            from datetime import datetime
            f=datetime.fromisoformat(from_at.replace('Z','+00:00')); t=datetime.fromisoformat(to_at.replace('Z','+00:00'))
            if f.tzinfo is None: f=f.replace(tzinfo=__import__('datetime').timezone.utc)
            if t.tzinfo is None: t=t.replace(tzinfo=__import__('datetime').timezone.utc)
            if f>t: raise ValueError('from_at must be <= to_at')
        sql='SELECT id,buyer_type,buyer_id,wallet_currency,event_type,amount,description,reference_type,reference_id,order_id,item_slice_id,arrival_id,actor,reason,idempotency_key,created_at FROM finance_ledger WHERE buyer_type=? AND buyer_id=? AND wallet_currency=?'
        args=[buyer_type,buyer_id,currency]
        if from_at is not None: sql+=' AND created_at>=?'; args.append(from_at)
        if to_at is not None: sql+=' AND created_at<=?'; args.append(to_at)
        sql+=' ORDER BY created_at DESC,id DESC LIMIT ?'; args.append(limit)
        with self._db() as c: rows=c.execute(sql,args).fetchall()
        return [dict(r,amount=money(r['amount'])) for r in rows]

    def payment_methods(self,buyer_id,buyer_type='dealer'):
        if buyer_type not in {'client','dealer'}: raise ValueError('invalid buyer_type')
        if not buyer_id or not str(buyer_id).strip(): raise ValueError('buyer_id required')
        with self._db() as c:
            rows=c.execute("SELECT m.code,m.name,m.payment_currency,m.fx_code,m.description,m.sort_order,a.enabled,a.access_mode,a.fx_code_primary,a.fx_code_secondary,o.override_mode FROM payment_methods m JOIN payment_method_audience a ON a.payment_method_code=m.code AND a.buyer_type=? LEFT JOIN buyer_payment_method_overrides o ON o.buyer_type=? AND o.buyer_id=? AND o.payment_method_code=m.code WHERE m.active=1 ORDER BY m.sort_order,m.code",(buyer_type,buyer_type,buyer_id)).fetchall()
        out=[]
        for r in rows:
            base=bool(r['enabled']) and (r['access_mode']=='all')
            if r['access_mode']=='selected_only': base=False
            mode=r['override_mode']
            enabled=True if mode=='allow' else False if mode=='deny' else base
            if enabled:
                out.append(dict(r))
        return out

    def event(self,event_id,buyer_id,buyer_type='dealer',currency='USD'):
        if buyer_type not in {'client','dealer'}: raise ValueError('invalid buyer_type')
        if currency not in {'RUB','USD'}: raise ValueError('invalid wallet currency')
        if not buyer_id or not str(buyer_id).strip(): raise ValueError('buyer_id required')
        try: event_id=int(event_id)
        except (TypeError,ValueError) as e: raise ValueError('invalid event_id') from e
        if event_id<1: raise ValueError('invalid event_id')
        with self._db() as c:
            r=c.execute('SELECT id,buyer_type,buyer_id,wallet_currency,event_type,amount,description,reference_type,reference_id,order_id,item_slice_id,arrival_id,actor,reason,idempotency_key,created_at FROM finance_ledger WHERE id=? AND buyer_type=? AND buyer_id=? AND wallet_currency=?',(event_id,buyer_type,buyer_id,currency)).fetchone()
            if not r: return None
            d=dict(r); d['amount']=money(r['amount'])
            if r['item_slice_id'] is not None:
                sl=c.execute('SELECT oem FROM item_slices WHERE id=?',(r['item_slice_id'],)).fetchone()
                d['oem']=sl['oem'] if sl else None
            else: d['oem']=None
        return d

    def post_event(self, *, buyer_type, buyer_id, currency, event_type, amount, description, reference_type, reference_id, actor, idempotency_key, reason=None, order_id=None, item_slice_id=None, arrival_id=None, conn=None):
        allowed={'PAYMENT','ORDER_CHARGE','REFUND','AUTO_REFUND','ADJUSTMENT_PLUS','ADJUSTMENT_MINUS','DELIVERY_CHARGE'}
        if buyer_type not in {'client','dealer'}: raise ValueError('invalid buyer_type')
        if currency not in {'RUB','USD'}: raise ValueError('invalid wallet currency')
        if buyer_type=='dealer' and currency!='USD': raise ValueError('dealer wallet currency must be USD')
        if event_type not in allowed: raise ValueError('invalid event_type')
        for value,name in [(buyer_id,'buyer_id'),(description,'description'),(reference_type,'reference_type'),(reference_id,'reference_id'),(actor,'actor'),(idempotency_key,'idempotency_key')]:
            if not value or not str(value).strip(): raise ValueError(name+' required')
        value=money(amount)
        if buyer_type=='dealer' and not value.is_finite(): raise ValueError('dealer amount must be finite')
        if value==Decimal('0.00'): raise ValueError('amount must be non-zero')
        if event_type in {'PAYMENT','REFUND','AUTO_REFUND','ADJUSTMENT_PLUS'} and value<=0: raise ValueError('event requires positive amount')
        if event_type in {'ORDER_CHARGE','ADJUSTMENT_MINUS','DELIVERY_CHARGE'} and value>=0: raise ValueError('event requires negative amount')
        if event_type in {'ADJUSTMENT_PLUS','ADJUSTMENT_MINUS'} and (not reason or not str(reason).strip()): raise ValueError('manual adjustment requires reason')
        own_conn = conn is None
        c = self._db() if own_conn else conn
        try:
            if buyer_type=='dealer':
                # Serialize capacity check and debit in the caller's transaction.
                # Never commit/rollback a transaction supplied by ORDER CORE.
                if not c.in_transaction: c.execute('BEGIN IMMEDIATE')
                if not c.execute('SELECT 1 FROM dealers WHERE id=? AND active=1',(buyer_id,)).fetchone(): raise ValueError('unknown or inactive dealer')
                if c.execute('SELECT 1 FROM finance_ledger WHERE idempotency_key=?',(idempotency_key,)).fetchone(): raise ValueError('duplicate idempotency_key')
                if value<0:
                    self.require_order_capacity(buyer_id=buyer_id,amount=-value,currency=currency,conn=c)
            sql="INSERT INTO finance_ledger (buyer_type,buyer_id,wallet_currency,event_type,amount,description,reference_type,reference_id,order_id,item_slice_id,arrival_id,actor,reason,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
            try:
                cur=c.execute(sql,(buyer_type,buyer_id,currency,event_type,str(value),description,reference_type,reference_id,order_id,item_slice_id,arrival_id,actor,reason,idempotency_key))
                if own_conn: c.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError as e:
                if own_conn: c.rollback()
                if 'finance_ledger.idempotency_key' in str(e): raise ValueError('duplicate idempotency_key') from e
                raise
        finally:
            if own_conn: c.close()


    def payment(self, *, buyer_type, buyer_id, currency, amount, payment_id, actor, reason, payment_method, source_amount, source_currency, fx_code=None, fx_rate=None, description='Пополнение баланса'):
        value=money(amount)
        if value<=0: raise ValueError('payment amount must be positive')
        for v,n in [(reason,'reason'),(payment_method,'payment_method'),(source_currency,'source_currency')]:
            if not v or not str(v).strip(): raise ValueError(n+' required')
        src=money(source_amount)
        if src<=0: raise ValueError('source_amount must be positive')
        if source_currency not in {'USD','RUB','USDT'}: raise ValueError('invalid source_currency')
        if source_currency=='USD':
            if value!=src: raise ValueError('USD payment must be 1:1')
            fx_code='USD_USD'; fx_rate='1'
        elif fx_code is None or fx_rate is None:
            raise ValueError('FX snapshot required')
        with self._db() as c:
            event_id=self.post_event(buyer_type=buyer_type,buyer_id=buyer_id,currency=currency,event_type='PAYMENT',amount=value,description=description,reference_type='payment',reference_id=payment_id,actor=actor,reason=reason,idempotency_key=f'PAYMENT:payment:{payment_id}',conn=c)
            c.execute('UPDATE finance_ledger SET payment_method=?,source_amount=?,source_currency=?,fx_code=?,fx_rate=? WHERE id=?',(payment_method,str(src),source_currency,str(fx_code),str(fx_rate),event_id))
            c.commit()
            return event_id

    def refund(self, *, buyer_type, buyer_id, currency, amount, refund_id, source_event_id, actor, reason):
        value=money(amount)
        if value<=0: raise ValueError('refund amount must be positive')
        if not reason or not str(reason).strip(): raise ValueError('refund reason required')
        c=self._db()
        try:
            c.execute('BEGIN IMMEDIATE')
            src=c.execute('SELECT id,buyer_type,buyer_id,wallet_currency,event_type,amount,order_id FROM finance_ledger WHERE id=?',(source_event_id,)).fetchone()
            if not src: raise ValueError('source event not found')
            if (src['buyer_type'],src['buyer_id'],src['wallet_currency'])!=(buyer_type,buyer_id,currency): raise ValueError('refund source owner/currency mismatch')
            if money(src['amount'])>=0: raise ValueError('source event is not refundable')
            refundable=abs(money(src['amount']))
            manual=money(c.execute("SELECT COALESCE(SUM(amount),0) FROM finance_ledger WHERE event_type='REFUND' AND reference_type='ledger_event' AND reference_id=?",(str(source_event_id),)).fetchone()[0])
            auto=Decimal('0.00')
            if src['event_type']=='ORDER_CHARGE' and src['order_id']:
                auto=money(c.execute("SELECT COALESCE(SUM(amount),0) FROM finance_ledger WHERE event_type='AUTO_REFUND' AND order_id=?",(src['order_id'],)).fetchone()[0])
            remaining=money(refundable-manual-auto)
            if remaining<0: raise ValueError('refund ledger exceeds refundable amount')
            if value>remaining: raise ValueError('refund exceeds refundable amount')
            event_id=self.post_event(buyer_type=buyer_type,buyer_id=buyer_id,currency=currency,event_type='REFUND',amount=value,description=f'Возврат: {reason}',reference_type='ledger_event',reference_id=str(source_event_id),actor=actor,reason=reason,idempotency_key=f'REFUND:refund:{refund_id}',conn=c)
            c.commit(); return event_id
        except:
            c.rollback(); raise
        finally:
            c.close()

    def auto_refund(self, *, buyer_type, buyer_id, currency, slice_id, reason_code, actor, conn=None):
        if reason_code not in {'unavailable','refusal'}: raise ValueError('invalid auto refund reason')
        own=conn is None
        c=self._db() if own else conn
        try:
            if own: c.execute('BEGIN IMMEDIATE')
            sl=c.execute('SELECT s.order_id,s.oem,s.line_total_usd,o.dealer_id FROM item_slices s JOIN dealer_orders o ON o.id=s.order_id WHERE s.id=?',(slice_id,)).fetchone()
            if not sl: raise ValueError('slice not found')
            if buyer_type=='dealer' and sl['dealer_id']!=buyer_id: raise ValueError('slice owner mismatch')
            amount=money(sl['line_total_usd'])
            if amount<=0: raise ValueError('slice has no refundable amount')
            prior=money(c.execute("SELECT COALESCE(SUM(amount),0) FROM finance_ledger WHERE event_type='AUTO_REFUND' AND item_slice_id=?",(slice_id,)).fetchone()[0])
            if prior>0: raise ValueError('slice already auto-refunded')
            charge=c.execute("SELECT id,buyer_type,buyer_id,wallet_currency,amount FROM finance_ledger WHERE event_type='ORDER_CHARGE' AND order_id=? ORDER BY id DESC LIMIT 1",(sl['order_id'],)).fetchone()
            if not charge: raise ValueError('order charge not found')
            if (charge['buyer_type'],charge['buyer_id'],charge['wallet_currency'])!=(buyer_type,buyer_id,currency): raise ValueError('order charge owner/currency mismatch')
            refundable=abs(money(charge['amount']))
            manual=money(c.execute("SELECT COALESCE(SUM(amount),0) FROM finance_ledger WHERE event_type='REFUND' AND reference_type='ledger_event' AND reference_id=?",(str(charge['id']),)).fetchone()[0])
            auto=money(c.execute("SELECT COALESCE(SUM(amount),0) FROM finance_ledger WHERE event_type='AUTO_REFUND' AND order_id=?",(sl['order_id'],)).fetchone()[0])
            remaining=money(refundable-manual-auto)
            if remaining<0: raise ValueError('refund ledger exceeds refundable amount')
            if amount>remaining: raise ValueError('auto refund exceeds order refundable amount')
            label='поставка невозможна' if reason_code=='unavailable' else 'принят отказ'
            event_id=self.post_event(buyer_type=buyer_type,buyer_id=buyer_id,currency=currency,event_type='AUTO_REFUND',amount=amount,description=f'Для позиции {sl["oem"]} из заказа {sl["order_id"]}: {label}',reference_type='item_slice',reference_id=str(slice_id),order_id=sl['order_id'],item_slice_id=slice_id,actor=actor,reason=reason_code,idempotency_key=f'AUTO_REFUND:slice:{slice_id}:{reason_code}',conn=c)
            if own: c.commit()
            return event_id
        except:
            if own: c.rollback()
            raise
        finally:
            if own: c.close()

    def adjustment_minus(self, *, buyer_type, buyer_id, currency, amount, adjustment_id, actor, reason):
        value=money(amount)
        if value<=0: raise ValueError('adjustment_minus amount must be positive')
        if not actor or not str(actor).strip(): raise ValueError('actor required')
        if not reason or not str(reason).strip(): raise ValueError('adjustment reason required')
        if not adjustment_id or not str(adjustment_id).strip(): raise ValueError('adjustment_id required')
        return self.adjustment(buyer_type=buyer_type,buyer_id=buyer_id,currency=currency,amount=-value,adjustment_id=adjustment_id,actor=actor,reason=reason)

    def adjustment(self, *, buyer_type, buyer_id, currency, amount, adjustment_id, actor, reason):
        value=money(amount)
        if value==0: raise ValueError('adjustment amount must be non-zero')
        if not reason or not str(reason).strip(): raise ValueError('adjustment reason required')
        event='ADJUSTMENT_PLUS' if value>0 else 'ADJUSTMENT_MINUS'
        return self.post_event(buyer_type=buyer_type,buyer_id=buyer_id,currency=currency,event_type=event,amount=value,description=f'Ручная корректировка: {reason}',reference_type='adjustment',reference_id=adjustment_id,actor=actor,reason=reason,idempotency_key=f'ADJUSTMENT:adjustment:{adjustment_id}')

    def delivery_charge(self, *, buyer_type, buyer_id, currency, amount, arrival_id, actor, description):
        value=money(amount)
        if value<=0: raise ValueError('delivery amount must be positive')
        with self._db() as c:
            ar=c.execute('SELECT dealer_id FROM dealer_arrivals WHERE id=?',(arrival_id,)).fetchone()
            if not ar: raise ValueError('arrival not found')
            if buyer_type=='dealer' and ar['dealer_id']!=buyer_id: raise ValueError('arrival owner mismatch')
        return self.post_event(buyer_type=buyer_type,buyer_id=buyer_id,currency=currency,event_type='DELIVERY_CHARGE',amount=-value,description=description,reference_type='dealer_arrival',reference_id=str(arrival_id),arrival_id=arrival_id,actor=actor,idempotency_key=f'DELIVERY_CHARGE:arrival:{arrival_id}')

