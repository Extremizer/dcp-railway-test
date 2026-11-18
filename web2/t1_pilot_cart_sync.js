/* EXTREMIZER / T1: one OWNER linked-test WEB2 cart. Opt-in by server cookie.
 * No effect on unlinked WEB2. Existing fallback/handoff stays unchanged.
 * RAM shared state can reset on Railway restart. No real checkout.
 */
(() => {
  "use strict";
  const scopes = ["usa", "rf:1", "rf:2", "rf:3"];
  const scopeOf = (x) => x.offer_source === "usa" ? "usa" :
    "rf:" + String(x.warehouse_id);
  const allowed = (x) => scopes.includes(scopeOf(x));
  const compare = (items) => JSON.stringify(items
    .map(x => [x.key, x.qty]).sort((a,b) => a[0].localeCompare(b[0])));
  const title = s => s === "usa" ? "США" : "Склад " + s.slice(3);
  const rev = Object.create(null);
  const shadow = Object.create(null);
  let csrf = "";
  let enabled = false;
  let busy = false;
  let queued = false;
  let statusNode = null;
  let showError = false;
  const originalSave = saveCart;
  const originalRender = renderCart;

  function setStatus(message, failed=false) {
    if (!statusNode) {
      statusNode = document.createElement("p");
      statusNode.className = "t1-pilot-sync-status";
      statusNode.style.cssText = "font-size:12px;line-height:1.5;color:#A5A5A5;margin:8px 0";
      const root = document.getElementById("cartFooter");
      (root || document.getElementById("cartPanel")).append(statusNode);
    }
    statusNode.textContent = message;
    statusNode.style.color = failed ? "#D8DAD7" : "#A5A5A5";
    showError = failed;
  }

  function stop(message) {
    enabled = false;
    setStatus(message + " Старые позиции в браузере сохранены.",true);
  }

  async function fetchState() {
    const r = await fetch("/api/t1/pilot/carts", {
      method:"GET",credentials:"same-origin",cache:"no-store",
      headers:{"Accept":"application/json"}
    });
    if (r.status === 401 || r.status === 403 || r.status === 404) return null;
    if (!r.ok) throw Error("state_http_" + r.status);
    return r.json();
  }

  function group(items, scope) {
    return items.filter(x => allowed(x) && scopeOf(x) === scope);
  }

  function flatten(server) {
    return scopes.flatMap(s => server[s].items);
  }

  function applyServer(server) {
    const pre=JSON.stringify(cart);
    if (!localStorage.getItem("extremizer_web2_t1_local_backup")) {
      localStorage.setItem("extremizer_web2_t1_local_backup",pre);
    }
    cart = normalizeCart(flatten(server));
    originalSave();
    renderCart();
    scopes.forEach(s => {
      rev[s]=server[s].revision;
      shadow[s]=compare(group(cart,s));
    });
  }

  function addScopePresentation() {
    // Do not change the working WEB2 for unlinked visitors.
    cart.sort((a,b) => scopes.indexOf(scopeOf(a))-scopes.indexOf(scopeOf(b)));
    originalRender();
    const root=document.getElementById("cartItems");
    const children=Array.from(root.querySelectorAll(":scope > .cart-item"));
    let previous=null;
    for (let i=0;i<cart.length;i++) {
      const s=scopeOf(cart[i]);
      if(s!==previous) {
        const header=document.createElement("div");
        header.textContent=title(s);
        header.style.cssText="margin:18px 0 9px;font-weight:700;font-size:14px;color:#BFF803";
        root.insertBefore(header,children[i]);
        previous=s;
      }
    }
    const summary=scopes.filter(s=>group(cart,s).length);
    // Independent per-scope subtotals, never a combined RF/USA amount.
    for (const s of summary) {
      const subtotal=group(cart,s).reduce(
        (sum,item)=>sum + Number(item.unit_rub||0)*item.qty,0);
      const line=document.createElement("div");
      line.style.cssText="margin:4px 0 12px;text-align:right;font-size:13px;color:#D8DAD7";
      line.textContent="\u0418\u0442\u043e\u0433\u043e "+title(s)+": "+rub(subtotal);
      const indices=cart.map((item,i)=>scopeOf(item)===s?i:-1).filter(i=>i>=0);
      const last=children[indices[indices.length-1]];
      if(last && last.parentNode===root)last.after(line);
    }
    document.getElementById("cartTotal").textContent="По направлениям";
    document.getElementById("cartButtonTotal").textContent=
      String(summary.length)+" "+(summary.length===1?"корзина":"корзины");
  }

  async function writeScope(s,items) {
    const response=await fetch("/api/t1/pilot/carts/"+s,{
      method:"POST",credentials:"same-origin",
      headers:{"Content-Type":"application/json","X-Ext-T1-CSRF":csrf},
      body:JSON.stringify({expected_revision:rev[s],items})
    });
    if(response.status===409) throw Error("revision_conflict");
    if(!response.ok) throw Error("write_http_"+response.status);
    const data=await response.json();
    rev[s]=data.revision;
    shadow[s]=compare(group(items,s));
  }

  async function publish() {
    if(!enabled) return;
    if(busy){queued=true;return;}
    busy=true;
    try {
      for (const s of scopes) {
        const items=group(cart,s);
        const digest=compare(items);
        if(digest===shadow[s]) continue;
        await writeScope(s,items);
      }
      setStatus("TG + WEB: общие тестовые корзины. Оформление отключено.");
    } catch(error) {
      stop("Конфликт версии или соединения TG + WEB.");
    } finally {
      busy=false;
      if(queued){queued=false;void publish();}
    }
  }

  async function poll() {
    if(!enabled || busy) return;
    busy=true;
    try {
      const response=await fetchState();
      if(!response || response.checkout_allowed!==false) {
        stop("Сессия TG + WEB завершилась.");
        return;
      }
      const incoming=response.scopes;
      if(scopes.some(s => !incoming[s] ||
          !Number.isInteger(incoming[s].revision) ||
          !Array.isArray(incoming[s].items))) throw Error("invalid_state");
      for (const s of scopes) {
        const remote=incoming[s];
        if(remote.revision===rev[s]) continue;
        if(compare(group(cart,s))!==shadow[s]) {
          stop("Одновременное изменение корзины. Перезагрузи страницу.");
          return;
        }
      }
      const changed=scopes.filter(s=>incoming[s].revision!==rev[s]);
      if(changed.length) applyServer(incoming);
    } catch(error) {
      stop("Синхронизация недоступна.");
    } finally {busy=false;}
  }

  async function boot() {
    try {
      const response=await fetchState();
      if(!response || response.checkout_allowed!==false) return;
      const incoming=response.scopes;
      if(!csrf && typeof response.csrf!=="string") return;
      csrf=response.csrf;
      if(scopes.some(s=>!incoming[s] ||
          !Number.isInteger(incoming[s].revision) ||
          !Array.isArray(incoming[s].items))) return;
      const local=normalizeCart(cart);
      const remote=normalizeCart(flatten(incoming));
      const localHas=local.length>0;
      const remoteHas=remote.length>0;
      if(localHas && remoteHas &&
          compare(local)!==compare(remote)) {
        setStatus("TG + WEB: обнаружены разные старые корзины. Удали тестовые локальные позиции или открой чистую вкладку.",true);
        return;
      }
      // Bound local history before any synchronized overwrite.
      if(!localStorage.getItem("extremizer_web2_t1_local_backup")) {
        localStorage.setItem("extremizer_web2_t1_local_backup",JSON.stringify(local));
      }
      scopes.forEach(s => {
        rev[s]=incoming[s].revision;
        shadow[s]=compare(group(remote,s));
      });
      enabled=true;
      renderCart=addScopePresentation;
      if(!localHas && remoteHas) applyServer(incoming);
      else if(localHas && !remoteHas) await publish();
      else renderCart();
      setStatus("TG + WEB: общие тестовые корзины. Заказы отключены.");
      setInterval(()=>{void poll()},3500);
    } catch(error) {
      setStatus("WEB2 работает без общей корзины: тестовая связь недоступна.",true);
    }
  }

  saveCart=function() {
    originalSave();
    if(enabled) {
      addScopePresentation();
      void publish();
    }
  };

  void boot();
})();
