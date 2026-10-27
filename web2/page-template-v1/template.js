/* WEB2 Page Template v1 — isolated, declarative, no production routes. */
export const TEMPLATE_VERSION = "1.0.0";
export const DIRECTIONS = Object.freeze([
  {id:"powersports", title:"POWERSPORTS", subtitle:"MACHINE"},
  {id:"workout", title:"WORKOUT & GYM", subtitle:"BODY"},
  {id:"food", title:"FOOD & HEALTH", subtitle:"RESOURCE"},
  {id:"outdoor", title:"OUTDOOR & TRAVEL", subtitle:"FREEDOM"},
  {id:"people", title:"GET TOGETHER", subtitle:"PEOPLE"}
].map(Object.freeze));

export const LOCKED_ASSETS = Object.freeze({
  wordmark: Object.freeze({route:"/web2-header-asset/wordmark", sha256:"46c3c4295313c0737094cc1b45a48fd64ea18cf3f971d48110640f64cd3a662f"}),
  search: Object.freeze({route:"/web2-header-asset/search", sha256:"26d942c4f05047ae12aab0de024dbc9723a8179789385c16649d13df2b89d2d8"}),
  cart: Object.freeze({route:"/web2-header-asset/cart", sha256:"920a3463a55a0b0dd3ea21155b3f3ba185ed60df066cec40bb3591e15af2a28b"}),
  private: Object.freeze({route:"/web2-header-asset/private", sha256:"c40e3950728ae670d1ca7c1a02d507a1a5d5eaa7f4b7aad09c4396b4b928cb3c"})
});
export const HERO_VARIANTS = Object.freeze(["full-image","split","photo-above","photo-search"]);
export const EMPTY_EDITORIAL = Object.freeze({items:[]});

function el(tag,className,text) {
  const node=document.createElement(tag);
  if(className) node.className=className;
  if(text!==undefined) node.textContent=text;
  return node;
}
export function createHero(config={}) {
  const variant=HERO_VARIANTS.includes(config.variant)?config.variant:"split";
  const section=el("section","pt-hero pt-hero--"+variant);
  const copy=el("div","pt-hero-copy");
  copy.append(el("p","pt-eyebrow",config.eyebrow||"EXTREMIZER"),el("h1","pt-title",config.title||""));
  if(config.subtitle) copy.append(el("p","pt-subtitle",config.subtitle));
  section.append(copy);
  if(config.imageApproved && config.imageUrl) {
    const img=el("img","pt-hero-photo");
    img.src=config.imageUrl; img.alt=config.imageAlt||""; img.loading="lazy";
    section.append(img);
  } else section.append(el("div","pt-placeholder","Фотография не утверждена"));
  return section;
}
export function createOemSearch() {
  const section=el("section","pt-search");
  section.append(el("h2","","OEM SEARCH"));
  const message=el("p","","Поиск и оформление доступны в существующем WEB2.");
  const link=el("a","pt-button","Перейти к OEM-поиску");
  link.href="/web2#oemInput";
  section.append(message,link);
  return section;
}
export function createCategoryGrid(cards=[]) {
  const section=el("section","pt-section");
  section.append(el("h2","","НАПРАВЛЕНИЯ"));
  const grid=el("div","pt-grid");
  for(const item of cards) {
    const article=el("article","pt-card");
    article.append(el("h3","",item.title||""),el("p","",item.subtitle||""));
    grid.append(article);
  }
  section.append(grid);
  return section;
}
export function createEditorialFeature({items=[]}={}) {
  if(!items.length) return null;
  const section=el("section","pt-section");
  section.append(el("h2","","EDITORIAL"));
  for(const item of items) section.append(el("p","",item.title||""));
  return section;
}
export function createGlobalFooter() {
  const footer=el("footer","pt-footer");
  footer.append(el("span","","EXTREMIZER"));
  const home=el("a","","На главную");
  home.href="/web2"; footer.append(home);
  return footer;
}
export function renderPage(root,config={}) {
  if(!root) throw Error("Template root required");
  root.replaceChildren();
  const page=el("div","pt-page");
  // Global Header M0 stays owned by /web2; no copying, reimplementing or replacing it.
  const header=el("div","pt-header-reference");
  const link=el("a","","Глобальная шапка M0 / действующий WEB2");
  link.href="/web2";header.append(link);page.append(header);
  page.append(createHero(config.hero),createOemSearch(),createCategoryGrid(config.categories||[]));
  const feature=createEditorialFeature(config.editorial||EMPTY_EDITORIAL);
  if(feature) page.append(feature);
  page.append(createGlobalFooter());root.append(page);
}
