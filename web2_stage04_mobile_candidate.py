"""Isolated mobile candidate. Uses approved SHA-verified Stage02 source and patched icon endpoint.
No mutation of canonical ZIP and NO replacement of existing /web2/stage04.
"""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from web2_stage04_gate import _read_archive
from web2_stage04_mobile_qa import MOBILE_DIAGNOSTIC_HTML

def _make_candidate():
    html = _read_archive()["index.html"].decode("utf-8")
    html=html.replace('href="styles.css"', 'href="/web2/stage04/styles.css"')
    html=html.replace('src="assets/approved_powersports_1536x1024.png"',
                      'src="/web2/stage04/assets/approved_powersports_1536x1024.png"')
    hero = '''<section class="mobile-hero" aria-label="POWERSPORTS — гараж и техника">
      <h1>POWERSPORTS</h1><div class="mobile-machine">MACHINE</div></section>
    '''
    html=html.replace('<header aria-label="Глобальная навигация EXTREMIZER"',hero+'<header aria-label="Глобальная навигация EXTREMIZER"',1)
    practice = '''<section class="mobile-practice" aria-label="Практика владения — мобильный вариант">
      <h2>Техника должна ехать.</h2><p class="practice-label">Практика владения</p>
      <div class="practice-tile" role="note"><div class="practice-number">01</div>
      <div><div class="practice-title">ОБСЛУЖИВАНИЕ</div>
      <div class="practice-description">Раздел будущих материалов</div></div></div>
      <div class="practice-tile" role="note"><div class="practice-number">02</div>
      <div><div class="practice-title">ЭКСПЛУАТАЦИЯ</div>
      <div class="practice-description">Раздел будущих материалов</div></div></div>
      <div class="practice-tile" role="note"><div class="practice-number">03</div>
      <div><div class="practice-title">ПОДГОТОВКА</div>
      <div class="practice-description">Раздел будущих материалов</div></div></div>
      <p class="practice-note">Разделы пока информационные. Переходы и функции не утверждены.</p>
    </section>'''
    marker='</div><section aria-label="Карточка OEM — WEB2"'
    assert marker in html
    html=html.replace(marker,practice+marker,1)
    html=html.replace('</head>', '''<link rel="stylesheet" href="/web2-static/stage04-mobile-candidate.css"/>
      <meta name="robots" content="noindex,nofollow"></head>''',1)
    html=html.replace('<body>', '''<body><aside class="stage-warning"
      style="position:fixed;bottom:8px;left:8px;z-index:999;background:#BFF803;color:#080808;font:700 12px Arial;padding:7px 10px">
      MOBILE CANDIDATE / MOCK OEM / ЗАКАЗЫ ОТКЛЮЧЕНЫ</aside>''',1)
    html=html.replace('</body>', '''<script>document.addEventListener("DOMContentLoaded",function(){
        var b=document.getElementById("telegramButton");
        if(b){b.disabled=true;b.textContent="Оформление отключено — тестовый стенд";}
      });</script></body>''',1)
    return html

def attach_mobile_candidate(app: FastAPI):
    @app.get("/web2/stage04/mobile-candidate",include_in_schema=False)
    def candidate():
        return HTMLResponse(_make_candidate(),headers={"Cache-Control":"no-store",
            "X-Extremizer-Candidate":"MOBILE-CSS-ONLY", "X-Extremizer-Data":"SYNTHETIC-MOCK"})
    @app.get("/web2/stage04/mobile-candidate-qa",include_in_schema=False)
    def candidate_qa():
        html=MOBILE_DIAGNOSTIC_HTML.replace('src="/web2/stage04"',
            'src="/web2/stage04/mobile-candidate"').replace('контент каждого iframe',
            'контент каждого iframe')
        html=html.replace('Responsive QA (не утверждённый дизайн)','Mobile candidate QA — для согласования')
        return HTMLResponse(html,headers={"Cache-Control":"no-store"})
