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
        if buyer_type!='dealer' or currency!='USD': return Decimal('0.00')
        with self._db() as c:
            r=c.execute('SELECT credit_limit_usd FROM dealers WHERE id=?',(buyer_id,)).fetchone()
        if not r: raise KeyError(buyer_id)
        return money(r['credit_limit_usd'])
    def available_to_order(self,buyer_id,buyer_type='dealer',currency='USD'):
        return money(self.balance(buyer_id,buyer_type,currency)+self.credit_limit(buyer_id,buyer_type,currency))

    def dealer_debt_status(self,dealer_id,currency='USD'):
        if currency!='USD': raise ValueError('dealer debt status supports USD only')
        if not dealer_id or not str(dealer_id).strip(): raise ValueError('dealer_id required')
        with self._db() as c:
            dealer=c.execute('SELECT id,name,active,credit_limit_usd FROM dealers WHERE id=?',(dealer_id,)).fetchone()
            if not dealer: raise ValueError('unknown dealer')
            rows=c.execute('SELECT amount FROM finance_ledger WHERE buyer_type=? AND buyer_id=? AND wallet_currency=? ORDER BY id',('dealer',dealer_id,currency)).fetchall()
        balance=money(sum((Decimal(str(r['amount'])) for r in rows),Decimal('0')))
        credit=money(dealer['credit_limit_usd'])
        used=money(min(max(-balance,Decimal('0.00')),credit))
        remaining=money(max(credit-used,Decimal('0.00')))
        available=money(balance+credit)
        over=money(max(-available,Decimal('0.00')))
        return {'dealer_id':dealer['id'],'dealer_name':dealer['name'],'active':bool(dealer['active']),'balance':balance,'credit_limit':credit,'credit_used':used,'credit_remaining':remaining,'available_to_order':available,'over_limit':over}

    def ensure_aging_schema(self):
        with self._db() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS finance_receivable_terms(
                source_event_id INTEGER PRIMARY KEY,
                due_date TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(source_event_id) REFERENCES finance_ledger(id)
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS finance_due_date_audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_event_id INTEGER NOT NULL,
                old_due_date TEXT,
                new_due_date TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY(source_event_id) REFERENCES finance_ledger(id)
            )""")

    def set_receivable_due_date(self, *, source_event_id, due_date, actor, reason, idempotency_key):
        from datetime import date
        self.ensure_aging_schema()
        try: source_event_id=int(source_event_id)
        except (TypeError,ValueError) as e: raise ValueError('invalid source_event_id') from e
        if source_event_id<1: raise ValueError('invalid source_event_id')
        due_date=str(due_date or '').strip(); actor=str(actor or '').strip(); reason=str(reason or '').strip(); key=str(idempotency_key or '').strip()
        try: parsed=date.fromisoformat(due_date)
        except ValueError as e: raise ValueError('invalid due_date') from e
        if parsed.isoformat()!=due_date: raise ValueError('invalid due_date')
        if not actor: raise ValueError('actor required')
        if not reason: raise ValueError('reason required')
        if not key: raise ValueError('idempotency_key required')
        c=self._db()
        try:
            c.execute('BEGIN IMMEDIATE')
            src=c.execute("SELECT id,buyer_type,buyer_id,wallet_currency,event_type,amount FROM finance_ledger WHERE id=?",(source_event_id,)).fetchone()
            if not src: raise ValueError('source event not found')
            if src['buyer_type']!='dealer' or src['wallet_currency']!='USD' or src['event_type'] not in {'ORDER_CHARGE','DELIVERY_CHARGE','ADJUSTMENT_MINUS'} or money(src['amount'])>=0:
                raise ValueError('source event is not an aging receivable')
            current=c.execute('SELECT due_date FROM finance_receivable_terms WHERE source_event_id=?',(source_event_id,)).fetchone()
            old=current['due_date'] if current else None
            if old==due_date: raise ValueError('due date unchanged')
            try:
                cur=c.execute("INSERT INTO finance_due_date_audit(source_event_id,old_due_date,new_due_date,actor,reason,idempotency_key,created_at) VALUES(?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",(source_event_id,old,due_date,actor,reason,key))
            except sqlite3.IntegrityError as e:
                if 'UNIQUE' in str(e).upper(): raise ValueError('duplicate idempotency_key') from e
                raise
            c.execute("INSERT INTO finance_receivable_terms(source_event_id,due_date,actor,reason,updated_at) VALUES(?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now')) ON CONFLICT(source_event_id) DO UPDATE SET due_date=excluded.due_date,actor=excluded.actor,reason=excluded.reason,updated_at=excluded.updated_at",(source_event_id,due_date,actor,reason))
            c.commit(); return cur.lastrowid
        except:
            c.rollback(); raise
        finally:
            c.close()

    def receivable_aging(self,dealer_id,currency='USD',as_of=None):
        from datetime import date,datetime,timezone
        if currency!='USD': raise ValueError('receivable aging supports USD only')
        if not dealer_id or not str(dealer_id).strip(): raise ValueError('dealer_id required')
        self.ensure_aging_schema()
        if as_of is None: today=datetime.now(timezone.utc).date()
        else:
            try: today=date.fromisoformat(str(as_of))
            except ValueError as e: raise ValueError('invalid as_of') from e
        with self._db() as c:
            if not c.execute('SELECT 1 FROM dealers WHERE id=?',(dealer_id,)).fetchone(): raise ValueError('unknown dealer')
            debits=c.execute("SELECT id,event_type,amount,order_id,reference_type,reference_id,created_at FROM finance_ledger WHERE buyer_type='dealer' AND buyer_id=? AND wallet_currency=? AND event_type IN ('ORDER_CHARGE','DELIVERY_CHARGE','ADJUSTMENT_MINUS') ORDER BY id",(dealer_id,currency)).fetchall()
            credits=c.execute("SELECT id,event_type,amount,order_id,reference_type,reference_id FROM finance_ledger WHERE buyer_type='dealer' AND buyer_id=? AND wallet_currency=? AND event_type IN ('PAYMENT','REFUND','AUTO_REFUND','ADJUSTMENT_PLUS') ORDER BY id",(dealer_id,currency)).fetchall()
            terms={r['source_event_id']:r['due_date'] for r in c.execute('SELECT source_event_id,due_date FROM finance_receivable_terms').fetchall()}
            all_amounts=c.execute("SELECT amount FROM finance_ledger WHERE buyer_type='dealer' AND buyer_id=? AND wallet_currency=? ORDER BY id",(dealer_id,currency)).fetchall()
        manual={}; auto={}; global_credit=Decimal('0.00')
        for r in credits:
            v=money(r['amount'])
            if r['event_type']=='REFUND' and r['reference_type']=='ledger_event' and r['reference_id']:
                try: sid=int(r['reference_id'])
                except (TypeError,ValueError): sid=None
                if sid is not None: manual[sid]=money(manual.get(sid,Decimal('0.00'))+v)
            elif r['event_type']=='AUTO_REFUND' and r['order_id']:
                auto[r['order_id']]=money(auto.get(r['order_id'],Decimal('0.00'))+v)
            else:
                global_credit=money(global_credit+v)
        prepared=[]
        for r in debits:
            original=abs(money(r['amount']))
            specific=money(manual.get(r['id'],Decimal('0.00')) + (auto.get(r['order_id'],Decimal('0.00')) if r['event_type']=='ORDER_CHARGE' and r['order_id'] else Decimal('0.00')))
            adjusted=money(max(original-specific,Decimal('0.00')))
            prepared.append((r,original,specific,adjusted))
        pool=global_credit; out=[]
        for r,original,specific,adjusted in prepared:
            applied=money(min(adjusted,pool)); pool=money(max(pool-applied,Decimal('0.00'))); outstanding=money(adjusted-applied)
            due=terms.get(r['id']); days=0
            if outstanding<=0: code,label='paid','Погашено'
            elif not due: code,label='no_due','Срок не установлен'
            else:
                due_dt=date.fromisoformat(due); days=max((today-due_dt).days,0)
                if today<=due_dt: code,label='current','Не просрочено'
                elif days<=7: code,label='overdue_1_7','Просрочено 1–7 дней'
                elif days<=30: code,label='overdue_8_30','Просрочено 8–30 дней'
                else: code,label='overdue_30_plus','Просрочено 30+ дней'
            out.append({'source_event_id':r['id'],'event_type':r['event_type'],'order_id':r['order_id'],'reference_type':r['reference_type'],'reference_id':r['reference_id'],'created_at':r['created_at'],'original_amount':original,'specific_credits':specific,'global_credit_applied':applied,'outstanding':outstanding,'due_date':due,'aging_code':code,'aging_label':label,'days_overdue':days})
        ledger_balance=money(sum((Decimal(str(r['amount'])) for r in all_amounts),Decimal('0')))
        modeled=money(sum((r['outstanding'] for r in out),Decimal('0')))
        expected=money(max(-ledger_balance,Decimal('0.00')))
        if modeled!=expected: raise ValueError(f'aging ledger mismatch:{modeled}:{expected}')
        return out

    def aging_summary(self,dealer_id,currency='USD',as_of=None):
        rows=self.receivable_aging(dealer_id,currency,as_of)
        codes=['no_due','current','overdue_1_7','overdue_8_30','overdue_30_plus','paid']
        result={c:{'count':0,'amount':Decimal('0.00')} for c in codes}
        for r in rows:
            result[r['aging_code']]['count']+=1
            result[r['aging_code']]['amount']=money(result[r['aging_code']]['amount']+r['outstanding'])
        result['open_total']=money(sum((r['outstanding'] for r in rows),Decimal('0')))
        return result


    def ensure_aging_alert_schema(self):
        with self._db() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS finance_aging_alert_state(
                source_event_id INTEGER NOT NULL,
                admin_id INTEGER NOT NULL,
                alert_key TEXT,
                last_notified_at TEXT,
                last_checked_at TEXT NOT NULL,
                PRIMARY KEY(source_event_id,admin_id),
                FOREIGN KEY(source_event_id) REFERENCES finance_ledger(id)
            )""")

    def aging_alert_candidates(self, admin_id, no_due_days=7, currency='USD', as_of=None):
        from datetime import date,datetime,timezone
        try: admin_id=int(admin_id)
        except (TypeError,ValueError) as e: raise ValueError('invalid admin_id') from e
        try: no_due_days=int(no_due_days)
        except (TypeError,ValueError) as e: raise ValueError('invalid no_due_days') from e
        if no_due_days<1 or no_due_days>3650: raise ValueError('invalid no_due_days')
        if currency!='USD': raise ValueError('aging alerts support USD only')
        if as_of is None: today=datetime.now(timezone.utc).date()
        else:
            try: today=date.fromisoformat(str(as_of))
            except ValueError as e: raise ValueError('invalid as_of') from e
        self.ensure_aging_alert_schema()
        with self._db() as c:
            dealers=[r['id'] for r in c.execute('SELECT id FROM dealers ORDER BY id').fetchall()]
            states={r['source_event_id']:r['alert_key'] for r in c.execute('SELECT source_event_id,alert_key FROM finance_aging_alert_state WHERE admin_id=?',(admin_id,)).fetchall()}
        open_ids=set(); candidates=[]; resets=[]
        for dealer_id in dealers:
            for r in self.receivable_aging(dealer_id,currency,as_of=today.isoformat()):
                if r['outstanding']<=0: continue
                eid=int(r['source_event_id']); open_ids.add(eid); key=None; age_days=0
                if r['aging_code'] in {'overdue_1_7','overdue_8_30','overdue_30_plus'}:
                    key=r['aging_code']
                elif r['aging_code']=='no_due':
                    try:
                        created=date.fromisoformat(str(r['created_at'])[:10])
                        age_days=max((today-created).days,0)
                    except (TypeError,ValueError):
                        age_days=0
                    if age_days>=no_due_days: key='no_due_old'
                previous=states.get(eid)
                if key is None:
                    if previous is not None: resets.append(eid)
                    continue
                if previous!=key:
                    x=dict(r); x.update({'dealer_id':dealer_id,'admin_id':admin_id,'alert_key':key,'age_days':age_days,'no_due_days':no_due_days})
                    candidates.append(x)
        stale=[eid for eid,key in states.items() if key is not None and eid not in open_ids]
        if resets or stale:
            with self._db() as c:
                for eid in sorted(set(resets+stale)):
                    c.execute("UPDATE finance_aging_alert_state SET alert_key=NULL,last_checked_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE source_event_id=? AND admin_id=?",(eid,admin_id))
        priority={'overdue_30_plus':0,'overdue_8_30':1,'overdue_1_7':2,'no_due_old':3}
        return sorted(candidates,key=lambda r:(priority[r['alert_key']],-float(r['outstanding']),r['source_event_id']))

    def acknowledge_aging_alert(self, *, source_event_id, admin_id, alert_key):
        allowed={'overdue_1_7','overdue_8_30','overdue_30_plus','no_due_old'}
        try: source_event_id=int(source_event_id); admin_id=int(admin_id)
        except (TypeError,ValueError) as e: raise ValueError('invalid alert identity') from e
        if alert_key not in allowed: raise ValueError('invalid alert_key')
        self.ensure_aging_alert_schema()
        with self._db() as c:
            if not c.execute('SELECT 1 FROM finance_ledger WHERE id=?',(source_event_id,)).fetchone(): raise ValueError('source event not found')
            c.execute("""INSERT INTO finance_aging_alert_state(source_event_id,admin_id,alert_key,last_notified_at,last_checked_at)
                         VALUES(?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                         ON CONFLICT(source_event_id,admin_id) DO UPDATE SET alert_key=excluded.alert_key,last_notified_at=excluded.last_notified_at,last_checked_at=excluded.last_checked_at""",
                      (source_event_id,admin_id,alert_key))
        return True

    def order_capacity(self,buyer_id,buyer_type='dealer',currency='USD',conn=None):
        if buyer_type!='dealer' or currency!='USD': raise ValueError('order capacity supports dealer/USD only')
        own=conn is None
        c=self._db() if own else conn
        try:
            dealer=c.execute('SELECT credit_limit_usd,active FROM dealers WHERE id=?',(buyer_id,)).fetchone()
            if not dealer or not dealer['active']: raise ValueError('unknown or inactive dealer')
            rows=c.execute('SELECT amount FROM finance_ledger WHERE buyer_type=? AND buyer_id=? AND wallet_currency=? ORDER BY id',(buyer_type,buyer_id,currency)).fetchall()
            balance=money(sum((Decimal(str(r['amount'])) for r in rows),Decimal('0')))
            credit=money(dealer['credit_limit_usd'])
            available=money(balance+credit)
            return {'balance':balance,'credit_limit':credit,'available_to_order':available}
        finally:
            if own: c.close()

    def require_order_capacity(self,*,buyer_id,amount,buyer_type='dealer',currency='USD',conn=None):
        required=money(amount)
        if required<=0: raise ValueError('order amount must be positive')
        cap=self.order_capacity(buyer_id,buyer_type,currency,conn=conn)
        available=cap['available_to_order']
        if required>available:
            raise ValueError(f'INSUFFICIENT_AVAILABLE:{available}:{required}')
        return dict(cap,required=required,remaining=money(available-required))

    def set_credit_limit(self, *, dealer_id, new_limit, actor, reason, idempotency_key):
        nl=money(new_limit)
        if nl<0: raise ValueError('credit limit must be >= 0')
        actor=str(actor or '').strip(); reason=str(reason or '').strip(); key=str(idempotency_key or '').strip()
        if not actor: raise ValueError('actor required')
        if not reason: raise ValueError('reason required')
        if not key: raise ValueError('idempotency_key required')
        c=self._db()
        try:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute('SELECT credit_limit_usd FROM dealers WHERE id=? AND active=1',(dealer_id,)).fetchone()
            if not r: raise ValueError('unknown or inactive dealer')
            old=money(r['credit_limit_usd'])
            if old==nl: raise ValueError('credit limit unchanged')
            c.execute('UPDATE dealers SET credit_limit_usd=? WHERE id=?',(str(nl),dealer_id))
            try:
                cur=c.execute("INSERT INTO finance_credit_limit_audit(dealer_id,currency,old_limit,new_limit,actor,reason,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",(dealer_id,'USD',str(old),str(nl),actor,reason,key))
            except sqlite3.IntegrityError as e:
                if 'UNIQUE' in str(e).upper(): raise ValueError('duplicate idempotency_key') from e
                raise
            c.commit(); return cur.lastrowid
        except:
            c.rollback(); raise
        finally:
            c.close()
    def credit_limit_history(self,dealer_id,limit=100):
        if not dealer_id or not str(dealer_id).strip(): raise ValueError('dealer_id required')
        if isinstance(limit,bool) or not isinstance(limit,int) or limit<1 or limit>500: raise ValueError('limit must be 1..500')
        with self._db() as c:
            rows=c.execute('SELECT id,dealer_id,currency,old_limit,new_limit,actor,reason,idempotency_key,created_at FROM finance_credit_limit_audit WHERE dealer_id=? ORDER BY created_at DESC,id DESC LIMIT ?',(dealer_id,limit)).fetchall()
        return [dict(r,old_limit=money(r['old_limit']),new_limit=money(r['new_limit'])) for r in rows]

    def credit_limit_event(self,event_id,dealer_id):
        if not dealer_id or not str(dealer_id).strip(): raise ValueError('dealer_id required')
        try: event_id=int(event_id)
        except (TypeError,ValueError) as e: raise ValueError('invalid event_id') from e
        if event_id<1: raise ValueError('invalid event_id')
        with self._db() as c:
            r=c.execute('SELECT id,dealer_id,currency,old_limit,new_limit,actor,reason,idempotency_key,created_at FROM finance_credit_limit_audit WHERE id=? AND dealer_id=?',(event_id,dealer_id)).fetchone()
        if not r: return None
        return dict(r,old_limit=money(r['old_limit']),new_limit=money(r['new_limit']))

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
        if event_type not in allowed: raise ValueError('invalid event_type')
        for value,name in [(buyer_id,'buyer_id'),(description,'description'),(reference_type,'reference_type'),(reference_id,'reference_id'),(actor,'actor'),(idempotency_key,'idempotency_key')]:
            if not value or not str(value).strip(): raise ValueError(name+' required')
        value=money(amount)
        if value==Decimal('0.00'): raise ValueError('amount must be non-zero')
        if event_type in {'PAYMENT','REFUND','AUTO_REFUND','ADJUSTMENT_PLUS'} and value<=0: raise ValueError('event requires positive amount')
        if event_type in {'ORDER_CHARGE','ADJUSTMENT_MINUS','DELIVERY_CHARGE'} and value>=0: raise ValueError('event requires negative amount')
        if event_type in {'ADJUSTMENT_PLUS','ADJUSTMENT_MINUS'} and (not reason or not str(reason).strip()): raise ValueError('manual adjustment requires reason')
        own_conn = conn is None
        c = self._db() if own_conn else conn
        try:
            if buyer_type=='dealer' and not c.execute('SELECT 1 FROM dealers WHERE id=? AND active=1',(buyer_id,)).fetchone(): raise ValueError('unknown or inactive dealer')
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

