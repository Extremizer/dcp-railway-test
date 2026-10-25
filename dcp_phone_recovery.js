"use strict";
let bearer=null,viewport={width:0,height:0};
const $=x=>document.getElementById(x);
const message=x=>$("status").textContent=x;
async function call(path,{method="GET",body=null,frame=false}={}){
 const headers={Authorization:"Bearer "+bearer};
 if(body!==null)headers["Content-Type"]="application/json";
 const r=await fetch("/recovery/"+path,{method,headers,body:body===null?null:JSON.stringify(body),cache:"no-store",credentials:"same-origin"});
 if(!r.ok)throw new Error("Доступ или браузер недоступен ("+r.status+")");
 return frame?r:await r.json();
}
async function snapshot(){
 const r=await call("frame",{frame:true});
 viewport.width=Number(r.headers.get("X-Viewport-Width"));viewport.height=Number(r.headers.get("X-Viewport-Height"));
 const blob=await r.blob(),url=URL.createObjectURL(blob);
 const old=$("frame").src;$("frame").src=url;
 if(old.startsWith("blob:")) URL.revokeObjectURL(old);
}
async function action(name,payload){await call(name,{method:"POST",body:payload});await snapshot()}
async function start(){
 try{
  const tg=window.Telegram?.WebApp;
  const hash=new URL(location.href).hash.slice(1);
  // Ticket lives in fragment, never request URL or Referer.
  history.replaceState(null,"",location.pathname);
  if(!tg?.initData||!hash)throw new Error("Откройте одноразовую ссылку внутри Telegram");
  tg.ready();tg.expand();
  const response=await fetch("/recovery/exchange",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({ticket:hash,init_data:tg.initData}),cache:"no-store"});
  if(!response.ok)throw new Error("Авторизация владельца не подтверждена");
  bearer=(await response.json()).bearer;
  $("app").style.display="block";
  const state=await call("status");
  message(state.dcp_status==="MOCK_ONLY"?"Доступ разрешён. Тестовый Chromium — DCP отключён.":"Доступ разрешён. Восстановите SIGN OUT в браузере DCP.");
  await snapshot();
 }catch(e){message(e.message)}
}
$("refresh").onclick=()=>snapshot().catch(e=>message(e.message));
$("state").onclick=async()=>{try{message("DCP: "+(await call("status")).dcp_status)}catch(e){message(e.message)}};
$("close").onclick=async()=>{try{await call("close",{method:"POST"});bearer=null;$("app").style.display="none";message("Доступ закрыт")}catch(e){message(e.message)}};
$("viewport").onclick=async e=>{try{
 const r=$("frame").getBoundingClientRect();if(!r.width||!r.height||!viewport.width||!viewport.height)return;
 await action("tap",{x:(e.clientX-r.left)*viewport.width/r.width,y:(e.clientY-r.top)*viewport.height/r.height});
 }catch(ex){message(ex.message)}};
$("type").onclick=async()=>{const v=$("typing").value;$("typing").value="";try{await action("text",{text:v})}catch(e){message(e.message)}};
$("up").onclick=()=>action("scroll",{dy:-450}).catch(e=>message(e.message));
$("down").onclick=()=>action("scroll",{dy:450}).catch(e=>message(e.message));
start();