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
async function dryrun(){
 try{
  $("dryrunStatus").textContent="queued";
  const queued=await call("dry-run",{method:"POST",body:{manufacturer:"SAFE-MFR",oem:"SAFE-OEM-000"}});
  $("dryrunStatus").textContent=queued.status+" "+queued.request_id;
  for(let i=0;i<20;i++){
   await new Promise(r=>setTimeout(r,500));
   const result=await call("dry-run/"+encodeURIComponent(queued.request_id));
   if(result.status==="DRY_RUN_OK"){
    $("dryrunStatus").textContent="DRY_RUN_OK";
    return;
   }
  }
  $("dryrunStatus").textContent="TIMEOUT";
 }catch(e){$("dryrunStatus").textContent=e.message}
}
async function start(){
 try{
  const tg=window.Telegram?.WebApp;
  const launch=new URL(location.href);
  const ticketRef=launch.searchParams.get("ref")||"";
  // Telegram owns the fragment. Only a non-secret selector is accepted.
  history.replaceState(null,"",location.pathname);
  if(!tg?.initData||!/^[0-9a-f]{64}$/.test(ticketRef))
    throw new Error("Откройте новое приглашение внутри Telegram");
  tg.ready();tg.expand();
  const response=await fetch("/recovery/exchange",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({ticket_ref:ticketRef,init_data:tg.initData}),cache:"no-store"});
  if(!response.ok)throw new Error("Авторизация владельца не подтверждена");
  bearer=(await response.json()).bearer;
  $("app").style.display="block";
  message("Доступ разрешён. Восстановите SIGN OUT в браузере DCP.");
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
$("dryrun").onclick=()=>dryrun();
start();
