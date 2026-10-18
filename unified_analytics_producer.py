"""Opt-in nonblocking producer facade for the canonical signed analytics receiver.

Writes delivery state into the EXISTING producer runtime SQLite; never a CRM DB.
"""
import asyncio,hashlib,json,os,sqlite3,time,urllib.error,urllib.request
from urllib.parse import urlsplit
import ipaddress
import unified_analytics_outbox as outbox
from unified_analytics_transport import PATH,PATH_CONVERSION,signed_headers

def enabled():
    return os.getenv("EXTREMIZER_UNIFIED_ANALYTICS_ENABLE","")=="1"

def initialize(db):
    if enabled():outbox.initialize(db)

def _event(source_bot,source_kind,update,oem,manufacturer=None,source_chat_id=None,source_chat_title=None,ordinal=0):
    user=getattr(update,"effective_user",None)
    chat=getattr(update,"effective_chat",None)
    message=getattr(update,"effective_message",None)
    uid=getattr(user,"id",None)
    cid=getattr(chat,"id",None)
    mid=getattr(message,"message_id",None)
    if not isinstance(mid,int) or not isinstance(cid,int):
        return None
    key=f"src:{source_bot}:"+hashlib.sha256(f"{cid}:{mid}:{ordinal}:{oem}".encode()).hexdigest()[:32]
    return {"source_bot":source_bot,"source_kind":source_kind,
      "delivery_key":key,"oem":oem,"manufacturer":manufacturer,
      "telegram_user_id":uid,"username":getattr(user,"username",None),
      "first_name":getattr(user,"first_name",None),"last_name":getattr(user,"last_name",None),
      "source_chat_id":source_chat_id if source_chat_id is not None else cid,
      "source_chat_title":source_chat_title if source_chat_title is not None else getattr(chat,"title",None),
      "telegram_message_id":mid}

def begin_request_from_update(db,*,source_bot,source_kind,update,oem,manufacturer=None,
                              source_chat_id=None,source_chat_title=None,ordinal=0):
    payload=_event(source_bot,source_kind,update,oem,manufacturer,source_chat_id,source_chat_title,ordinal)
    if payload is None:return None
    key=payload["delivery_key"]
    try:
        outbox.enqueue(db,key+":begin",payload)
        return key
    except (OSError,ValueError,sqlite3.Error):return None

def _transmit(payload):
    source=payload["source_bot"]
    secret=os.getenv("EXTREMIZER_ANALYTICS_"+source.upper()+"_SECRET","")
    base=os.getenv("EXTREMIZER_UNIFIED_ANALYTICS_BASE_URL","").rstrip("/")
    target=urlsplit(base)
    private=(target.scheme=="http" and target.hostname is not None
             and target.hostname.lower().endswith(".railway.internal")
             and target.hostname.lower()!=".railway.internal")
    public=(target.scheme=="https" and target.hostname is not None)
    if (not (private or public) or target.username or target.password
        or target.query or target.fragment or target.path not in ("","/")
        or len(secret)<32):return False
    try:
        if ipaddress.ip_address(target.hostname).is_private:return False
    except ValueError:
        pass
    body=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")
    try:
        path=PATH_CONVERSION if payload.get('action') else PATH
        request=urllib.request.Request(base+path,data=body,method="POST",
                                      headers=signed_headers(secret,source,body,path=path))
        # Signed payload must never follow an HTTP(S) redirect to another host.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        with urllib.request.build_opener(NoRedirect).open(request,timeout=4) as response:
            if response.status!=200:return False
            data=json.loads(response.read(1024))
            accepted='CONVERSION_ACCEPTED' if path==PATH_CONVERSION else 'ANALYTICS_ACCEPTED'
            return data.get("status")==accepted
    except (OSError,ValueError,urllib.error.HTTPError,TimeoutError):
        return False

def flush_pending(db):
    try:return outbox.flush(db,_transmit)
    except (OSError,ValueError,sqlite3.Error):return {"delivered":0,"deferred":0,"terminal":0}

def complete_request(db,request_id,*,manufacturer=None,result_status=None,price_status=None,
                     display_price_amount=None,display_price_currency=None,
                     stock_rf_status=None,stock_rf=None):
    if not request_id:return False
    try:
        with outbox.database(db) as c:
            row=c.execute("SELECT payload_json FROM analytics_delivery_outbox WHERE delivery_key=?",
                          (request_id+":begin",)).fetchone()
        if not row:return False
        payload=json.loads(row[0])
        if manufacturer is not None:
            payload["manufacturer"]=manufacturer
        payload.update(result_status=result_status,price_status=price_status,
          display_price_amount=display_price_amount,
          display_price_currency=display_price_currency if display_price_amount is not None else None,
          stock_rf_status=stock_rf_status,stock_rf=stock_rf)
        # Receiver sees same logical delivery_key. Outbox has an independent stage key.
        outbox.enqueue(db,request_id+":complete",payload)
        try:
            loop=asyncio.get_running_loop()
            loop.create_task(asyncio.to_thread(flush_pending,db))
        except RuntimeError:
            pass
        return True
    except (OSError,ValueError,sqlite3.Error):return False

def send_dealer_completed(db,*,telegram_user_id,oem,telegram_message_id,source_chat_id=None,
                          username=None,first_name=None,last_name=None,manufacturer=None,
                          result_status=None,price_status=None,display_price_amount=None,
                          display_price_currency=None,source_kind='private'):
    if not isinstance(telegram_message_id,int) or not telegram_user_id or not oem:
        return False
    cid=source_chat_id if source_chat_id is not None else telegram_user_id
    delivery='src:oemixibot:'+hashlib.sha256(f'{cid}:{telegram_message_id}:0:{oem}'.encode()).hexdigest()[:32]
    payload={'source_bot':'oemixibot','source_kind':source_kind,'delivery_key':delivery,
      'telegram_user_id':int(telegram_user_id),'source_chat_id':cid,
      'telegram_message_id':telegram_message_id,'oem':oem,'manufacturer':manufacturer,
      'username':username,'first_name':first_name,'last_name':last_name,
      'result_status':result_status,'price_status':price_status,
      'display_price_amount':display_price_amount,
      'display_price_currency':display_price_currency if display_price_amount is not None else None,
      'stock_rf_status':'NOT_APPLICABLE','stock_rf':[]}
    try:
        outbox.enqueue(db,delivery+':complete',payload)
        return flush_pending(db)['delivered'] > 0
    except (OSError,ValueError,sqlite3.Error):return False

async def _replay_loop(db):
    while True:
        await asyncio.to_thread(flush_pending,db)
        await asyncio.sleep(30)

def begin_replay(application,db):
    if enabled():
        application.bot_data['unified_analytics_replay_task']=asyncio.create_task(_replay_loop(db))

async def stop_replay(application):
    task=application.bot_data.pop('unified_analytics_replay_task',None)
    if task is not None:
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass

def queue_conversion(db,*,source_bot,delivery_key,oem,telegram_user_id,
                     action,order_id=None):
    """Queue ONLY immutable request provenance, never search by latest OEM."""
    if source_bot not in ('pricing','oemixibot') or not delivery_key or not oem:
        return False
    if action not in ('CART','ORDER_PLACED'):
        return False
    if action=='ORDER_PLACED' and not order_id:return False
    payload={'source_bot':source_bot,'delivery_key':delivery_key,'oem':oem,
             'telegram_user_id':telegram_user_id,'action':action}
    if order_id is not None:payload['order_id']=str(order_id)
    stage='cart' if action=='CART' else 'order:'+str(order_id)
    try:
        outbox.enqueue(db,delivery_key+':'+stage,payload)
        try:
            loop=asyncio.get_running_loop()
            loop.create_task(asyncio.to_thread(flush_pending,db))
        except RuntimeError:
            pass
        return True
    except (OSError,sqlite3.Error,ValueError):return False

def dealer_delivery_key(chat_id,message_id,oem):
    if not isinstance(message_id,int) or not isinstance(chat_id,int) or not oem:
        return None
    return 'src:oemixibot:'+hashlib.sha256(f'{chat_id}:{message_id}:0:{oem}'.encode()).hexdigest()[:32]

def initialize_dealer_provenance(db):
    with outbox.database(db) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS analytics_dealer_card_tokens(
          token TEXT PRIMARY KEY, dealer_id TEXT NOT NULL, telegram_user_id INTEGER NOT NULL,
          oem TEXT NOT NULL, delivery_key TEXT NOT NULL, created_at REAL NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS analytics_dealer_cart_provenance(
          dealer_id TEXT NOT NULL, oem TEXT NOT NULL, delivery_key TEXT NOT NULL,
          PRIMARY KEY(dealer_id,oem,delivery_key))""")

def register_dealer_card(db,*,dealer_id,telegram_user_id,chat_id,message_id,oem):
    delivery=dealer_delivery_key(chat_id,message_id,oem)
    if delivery is None:return None
    token='ua'+hashlib.sha256(delivery.encode()).hexdigest()[:18]
    try:
        with outbox.database(db) as c:
            c.execute("""INSERT OR IGNORE INTO analytics_dealer_card_tokens
              (token,dealer_id,telegram_user_id,oem,delivery_key,created_at)
              VALUES(?,?,?,?,?,?)""",(token,str(dealer_id),int(telegram_user_id),oem,delivery,time.time()))
            row=c.execute("SELECT dealer_id,telegram_user_id,oem,delivery_key FROM analytics_dealer_card_tokens WHERE token=?",(token,)).fetchone()
            if row!=(str(dealer_id),int(telegram_user_id),oem,delivery):return None
        return token
    except (OSError,sqlite3.Error,ValueError):return None

def dealer_card_delivery(db,*,token,dealer_id,telegram_user_id,oem):
    try:
        with outbox.database(db) as c:
            row=c.execute("""SELECT delivery_key FROM analytics_dealer_card_tokens
                 WHERE token=? AND dealer_id=? AND telegram_user_id=? AND oem=?""",
                 (token,str(dealer_id),int(telegram_user_id),oem)).fetchone()
        return row[0] if row else None
    except (OSError,sqlite3.Error,ValueError):return None

def dealer_cart_record(db,*,dealer_id,oem,delivery_key,telegram_user_id):
    """Atomic provenance + durable CART event after successful cart mutation."""
    if not delivery_key:return False
    payload={'source_bot':'oemixibot','delivery_key':delivery_key,'oem':oem,
             'telegram_user_id':int(telegram_user_id),'action':'CART'}
    event_key=delivery_key+':cart'
    body=json.dumps(payload,sort_keys=True,separators=(',',':'))
    try:
        with outbox.database(db) as c:
            c.execute("INSERT OR IGNORE INTO analytics_dealer_cart_provenance VALUES(?,?,?)",
                      (str(dealer_id),oem,delivery_key))
            c.execute("""INSERT OR IGNORE INTO analytics_delivery_outbox
              (delivery_key,payload_json,created_at) VALUES(?,?,?)""",
              (event_key,body,time.time()))
        return True
    except (OSError,sqlite3.Error,ValueError):return False
def clear_dealer_cart(db,*,dealer_id,oem=None):
    try:
        with outbox.database(db) as c:
            if oem is None:c.execute("DELETE FROM analytics_dealer_cart_provenance WHERE dealer_id=?",(str(dealer_id),))
            else:c.execute("DELETE FROM analytics_dealer_cart_provenance WHERE dealer_id=? AND oem=?",(str(dealer_id),oem))
        return True
    except (OSError,sqlite3.Error):return False

def pop_dealer_order_links(db,*,dealer_id,cart_oems):
    """After checkout success only. Exact links for OEMs actually in order."""
    if not cart_oems:return []
    try:
        with outbox.database(db) as c:
            placeholders=','.join('?' for _ in cart_oems)
            links=c.execute(f"""SELECT oem,delivery_key FROM analytics_dealer_cart_provenance
                 WHERE dealer_id=? AND oem IN ({placeholders})""",(str(dealer_id),*cart_oems)).fetchall()
            c.execute("DELETE FROM analytics_dealer_cart_provenance WHERE dealer_id=?",(str(dealer_id),))
        return links
    except (OSError,sqlite3.Error):return []

def dealer_token_offer(db,*,token,dealer_id,telegram_user_id):
    try:
        with outbox.database(db) as c:
            row=c.execute("""SELECT oem,delivery_key FROM analytics_dealer_card_tokens
              WHERE token=? AND dealer_id=? AND telegram_user_id=?""",
              (token,str(dealer_id),int(telegram_user_id))).fetchone()
        return row if row else None
    except (OSError,sqlite3.Error,ValueError):return None

def stage_dealer_placed_order(db,*,dealer_id,telegram_user_id,order_id,cart_oems):
    """Commit exact order conversion events to existing producer outbox atomically.

    Called only after successful store checkout, never on unpaid/failed checkout.
    """
    if not order_id or not cart_oems:return False
    try:
        with outbox.database(db) as c:
            placeholders=','.join('?' for _ in cart_oems)
            links=c.execute(f"""SELECT oem,delivery_key FROM analytics_dealer_cart_provenance
               WHERE dealer_id=? AND oem IN ({placeholders})""",
               (str(dealer_id),*cart_oems)).fetchall()
            for oem,delivery in links:
                payload={'source_bot':'oemixibot','delivery_key':delivery,'oem':oem,
                  'telegram_user_id':int(telegram_user_id),'action':'ORDER_PLACED',
                  'order_id':str(order_id)}
                event_key=delivery+':order:'+str(order_id)
                body=json.dumps(payload,sort_keys=True,separators=(',',':'))
                c.execute("""INSERT OR IGNORE INTO analytics_delivery_outbox
                  (delivery_key,payload_json,created_at) VALUES(?,?,?)""",
                  (event_key,body,time.time()))
            c.execute('DELETE FROM analytics_dealer_cart_provenance WHERE dealer_id=?',(str(dealer_id),))
        return True
    except (OSError,sqlite3.Error,ValueError):return False
