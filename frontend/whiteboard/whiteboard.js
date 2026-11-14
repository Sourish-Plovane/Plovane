(() => {
  const API_ORIGIN = window.PLOVANE_API_ORIGIN || (window.location.pathname.startsWith('/whiteboard') ? window.location.origin : 'http://127.0.0.1:8700');
  const API = `${API_ORIGIN}/api/v1/whiteboard`;
  // The server writes the person's token into this page; every API call sends it.
  const TOKEN = document.querySelector('meta[name="plovane-token"]')?.content || window.PLOVANE_TOKEN || '';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  document.body.append($('#autocomplete'));
  const state = { data:null, commands:[], scale:.86, x:170, y:105, selected:null, tab:'files', online:false, lastPrompt:'', log:[], viewLevel:1, demo:false, drag:null, history:JSON.parse(localStorage.getItem('plovane-prompt-history')||'[]') };
  const layout = { sideWidth:240, bottomHeight:190, overlap:0, sideOpen:true, bottomOpen:true, ...JSON.parse(localStorage.getItem('plovane-whiteboard-layout')||'{}') };
  const preferences = { gridVisible:true, gridSpacing:20, gridContrast:1, zoomSensitivity:1, panMode:'drag', codeFontSize:12, codeTheme:'auto', ...JSON.parse(localStorage.getItem('plovane-whiteboard-preferences')||'{}') };
  let spacePressed=false;
  // Live activity feed, AI runs by id, blocks to highlight after the next redraw, providers with keys.
  const activity=[];const runs={};let flashIds=new Set(),refreshTimer=null,providerList=[];
  const kinds = {agent:'◉',service:'⬡',server:'⬡',external:'↗',database:'▤',queue:'⇢',endpoint:'↗',folder:'▰',group:'▦',file:'▧',function:'ƒ',class:'C',step:'·',code:'⌘',scan:'⌕',note:'✎',slot:'□'};
  const sample = {
    project:'plovane/payments-api', board:{layers:{0:{number:0,name:'Agents and access',zoom_min:0,zoom_max:10,shows:['agent']},1:{number:1,name:'Services and endpoints',zoom_min:10,zoom_max:20,shows:['service','database']},2:{number:2,name:'Folders',zoom_min:20,zoom_max:40,shows:['folder']},3:{number:3,name:'Files',zoom_min:40,zoom_max:70,shows:['file']},4:{number:4,name:'English steps',zoom_min:70,zoom_max:90,shows:['function','step']},5:{number:5,name:'Code',zoom_min:90,zoom_max:100,shows:['code']}},blocks:{},arrows:{},endpoints:{},templates:{},agents:{},alerts:[],view:{}},block_styles:{},absolute:{},arrows:[]
  };
  function demoBoard(){
    const B=(id,kind,name,layer,x,y,w,h,more={})=>sample.board.blocks[id]={id,kind,name,layer,x,y,w,h,...more};
    B('test-writer','agent','Test writer',0,300,90,145,68,{meta:{mode:'read-write',path:'tests/',regions:[{x:250,y:70,w:330,h:240}]}});B('reviewer','agent','Code reviewer',0,760,110,145,68,{meta:{mode:'read',path:'src/',regions:[{x:690,y:80,w:360,h:280}]}});
    B('payments','service','Payments API',1,390,310,260,166,{status:'verified',meta:{language:'python',description:'Flask service · 4 endpoints'}});B('web-app','service','Web application',1,70,340,230,135,{status:'verified',meta:{language:'typescript'}});B('postgres','database','PostgreSQL',1,780,350,180,105,{status:'verified'});
    B('api-folder','folder','payments',2,415,620,220,154,{path:'src/payments',parent:'payments'});B('client-folder','folder','client',2,70,640,220,140,{path:'src/client',parent:'web-app'});
    B('routes-file','file','routes.py',3,420,900,205,82,{path:'src/payments/routes.py',status:'verified',parent:'api-folder',meta:{code:'from flask import Blueprint\nfrom .billing import charge_card\n\nroutes = Blueprint("payments", __name__)\n\n@routes.post("/charge")\ndef charge():\n    return charge_card(request.json)'}});B('billing-file','file','billing.py',3,680,900,205,82,{path:'src/payments/billing.py',status:'generated',parent:'api-folder'});B('client-file','file','payments.ts',3,75,920,205,82,{path:'src/client/payments.ts',parent:'client-folder',meta:{code:'export async function charge(amount: number) {\n  const res = await fetch("/charge", { method: "POST", body: JSON.stringify({ amount }) });\n  return res.json();\n}'}});
    B('charge','function','charge_card()',4,405,1150,220,90,{symbol:'charge_card',status:'verified',parent:'billing-file',meta:{summary:'Checks the payment and charges the card'}});B('retry','function','retry_payment()',4,690,1150,220,90,{symbol:'retry_payment',status:'generated',parent:'billing-file',meta:{summary:'Retries a failed charge',code:'def retry_payment(payment, times=3):\n    for attempt in range(times):\n        if charge_card(payment):\n            return True\n    return False'}});
    B('charge-code','code','charge_card · Python',5,395,1400,340,185,{status:'verified',parent:'charge',meta:{code:'def charge_card(payment):\n    if not payment.valid:\n        raise ValueError("invalid payment")\n    return stripe.charge(payment)'}});B('scan-payments','scan','Security scan',1,355,280,330,250,{status:'verified',meta:{alerts:1}});
    sample.board.arrows={'a1':{id:'a1',source:'web-app',target:'payments',type:'api',endpoint:'POST /charge',status:'built'},'a2':{id:'a2',source:'payments',target:'postgres',type:'database',status:'built'},'a3':{id:'a3',source:'client-file',target:'routes-file',type:'api',endpoint:'POST /charge',status:'built'},'a4':{id:'a4',source:'routes-file',target:'billing-file',type:'import',status:'built'},'a5':{id:'a5',source:'charge',target:'retry',type:'calls',status:'planned'}};
    sample.absolute={}; Object.values(sample.board.blocks).forEach(b=>sample.absolute[b.id]={x:b.x,y:b.y,w:b.w,h:b.h});sample.board.alerts=[{severity:'medium',message:'Payment amount is not validated before charge',block_id:'billing-file'}]; sample.board.templates={rest_api:{name:'REST API service',builtin:true}};sample.board.agents={};state.demo=true;return sample;
  }
  async function request(path, options={}){
    let response;try{response=await fetch(`${API}/${path}`,{...options,headers:{'Content-Type':'application/json',...(TOKEN?{Authorization:`Bearer ${TOKEN}`}:{}),...(options.headers||{})}})}catch(_){throw new Error('Whiteboard backend unavailable. Run: plovane open')}
    const envelope=await response.json();if(!response.ok||!envelope.ok)throw new Error(envelope.error?.message||`Request failed (${response.status})`);return envelope.data;
  }
  function toast(text,error=false){const t=$('#toast');t.textContent=text;t.classList.toggle('error',error);t.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>t.classList.remove('show'),3200)}
  function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
  function styleFor(b){return state.data?.block_styles?.[b.id]||{}}
  function savePreferences(){localStorage.setItem('plovane-whiteboard-preferences',JSON.stringify(preferences))}
  function applyCodePreferences(){document.documentElement.style.setProperty('--code-font-size',`${preferences.codeFontSize}px`);document.body.dataset.codeTheme=preferences.codeTheme}
  function updateGrid(){const z=state.scale;const base=Math.max(8,Math.min(48,preferences.gridSpacing*z));const major=base*5;const v=$('#canvas-viewport'),dark=document.body.dataset.theme==='dark',fade=Math.max(.25,Math.min(1,preferences.gridContrast)),minorColor=dark?`rgba(120,128,145,${.34*fade})`:`rgba(140,148,162,${.28*fade})`,majorColor=dark?`rgba(95,103,120,${.34*fade})`:`rgba(160,168,182,${.28*fade})`;v.style.backgroundImage=preferences.gridVisible?`radial-gradient(circle, ${minorColor} ${Math.max(.55,z*.8)}px, transparent ${Math.max(.8,z*.95)}px),linear-gradient(to right, transparent ${major-1}px,${majorColor} ${major-1}px,${majorColor} ${major}px,transparent ${major}px),linear-gradient(to bottom, transparent ${major-1}px,${majorColor} ${major-1}px,${majorColor} ${major}px,transparent ${major}px)`:'none';v.style.backgroundSize=`${base}px ${base}px,${major}px ${major}px,${major}px ${major}px`;v.style.backgroundPosition=`${state.x}px ${state.y}px,${state.x}px ${state.y}px,${state.x}px ${state.y}px`;$('#zoom-label').textContent=`${Math.round(z*100)}%`}
  /* ---- Nested zoom: the project as boxes inside boxes ----
   * The canvas always looks at one block (the focus, or the whole project).
   * Its children are cards; each card already shows its own children as
   * small tiles, laid out with the same proportions they get once you are
   * inside it. Zooming into a card until it fills the screen makes it the
   * focus and remaps the camera, so the tiles simply become cards. A block
   * with no children shows its code. Zooming out past the frame goes up. */
  const FRAME_W=1600,ENTER_AT=.7,LEAVE_AT=.42,HIDDEN=new Set(['agent','scan']);
  const KIND_ORDER={service:0,server:0,endpoint:1,database:1,queue:1,external:1,folder:2,slot:2,file:3,class:4,function:4,group:4,step:4,code:5,note:6};
  const nest={stack:[],items:new Map(),code:{},target:null,anim:0,lastAnchor:null};
  /**
   * Input: b (object)
   * Output: object
   * Processing: A block's stored position (kept for the details dialog).
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function position(b){const a=state.data?.absolute?.[b.id];if(Array.isArray(a))return {x:a[0],y:a[1],w:b.w,h:b.h};return a||b}
  /**
   * Input: none
   * Output: object
   * Processing: The board's blocks by id.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function boardBlocks(){return (state.data?.board||state.data||{}).blocks||{}}
  /**
   * Input: b (object)
   * Output: number
   * Processing: The first line a block covers, for ordering.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function lineOf(b){return Number(b.meta?.lines?.[0]??0)}
  /**
   * Input: id (string | null)
   * Output: object[]
   * Processing: The blocks drawn inside a block (or at the top of the project), AI outline first.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function childrenOf(id){const all=boardBlocks();let list=Object.values(all).filter(b=>!HIDDEN.has(b.kind)&&(id==null?(!b.parent||!all[b.parent]):b.parent===id));
    if(list.some(b=>b.meta?.source==='ai'))list=list.filter(b=>b.meta?.source==='ai'||!['function','class','step'].includes(b.kind));
    return list.sort((a,b)=>(KIND_ORDER[a.kind]??7)-(KIND_ORDER[b.kind]??7)||lineOf(a)-lineOf(b)||String(a.name).localeCompare(String(b.name)))}
  /**
   * Input: b (object)
   * Output: object | null
   * Processing: Where a block's code is: file path and line range, or inline demo code.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function codeSource(b){if(!b)return null;if(b.meta?.code)return {inline:b.meta.code,from:1,to:Infinity};const path=b.meta?.file||(['file','code'].includes(b.kind)?b.path:null);if(!path)return null;const l=b.meta?.lines;return {path,from:l?Number(l[0]):1,to:l?Number(l[1]):Infinity}}
  /**
   * Input: id (string)
   * Output: boolean
   * Processing: Whether zooming into a block shows anything (blocks or code).
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function enterable(id){return childrenOf(id).length>0||!!codeSource(boardBlocks()[id])}
  /**
   * Input: r (object)
   * Output: object
   * Processing: The part of a box below its title, where its children or code go.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function innerOf(r){const head=Math.min(r.w*.12,r.h*.3);return {x:r.x,y:r.y+head,w:r.w,h:r.h-head,head}}
  /**
   * Input: n (number), r (object)
   * Output: object[]
   * Processing: n cells filling a box, in the column count that keeps them closest to 16:10.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function grid(n,r){if(!n)return [];const m=Math.min(r.w,r.h),pad=m*.05,gap=m*.035,W=r.w-2*pad,H=r.h-2*pad;let best=null;
    for(let cols=1;cols<=n;cols++){const rows=Math.ceil(n/cols),cw=(W-gap*(cols-1))/cols,ch=(H-gap*(rows-1))/rows;if(cw<=0||ch<=0)continue;const score=Math.abs(Math.log(cw/ch/1.6));if(!best||score<best.score)best={cols,cw,ch,score}}
    if(!best)return [];return Array.from({length:n},(_,i)=>({x:r.x+pad+(i%best.cols)*(best.cw+gap),y:r.y+pad+Math.floor(i/best.cols)*(best.ch+gap),w:best.cw,h:best.ch}))}
  /**
   * Input: inner (object)
   * Output: object
   * Processing: Font, line height and padding for code shown in a box, proportional to its width.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function codeMetrics(inner,n=0){const pad=inner.w*.03,fill=n?(inner.h-2*pad)/(n*1.55):0,font=Math.max(inner.w/95,Math.min(inner.w/42,fill||inner.w/95)),line=font*1.55;return {font,line,pad}}
  /**
   * Input: source (object)
   * Output: string[] | null
   * Processing: The lines a block covers, once its file has been fetched (fetching it the first time).
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function codeLines(source){if(!source)return null;if(source.inline!=null)return String(source.inline).split('\n');const entry=nest.code[source.path];
    if(!entry){nest.code[source.path]={loading:true};request('command',{method:'POST',body:JSON.stringify({command:`/open ${quoteArg(source.path)}`})}).then(d=>{nest.code[source.path]={text:d.result?.content??''};render()}).catch(e=>{nest.code[source.path]={error:e.message};render()});return null}
    if(entry.error)return ['// '+entry.error];if(entry.text==null)return null;const all=entry.text.split('\n');return all.slice(source.from-1,Math.min(all.length,source.to))}
  const KW=new Set('def class return if elif else for while try except finally with as import from raise yield lambda pass break continue and or not in is None True False async await function const let var new this export default switch case throw catch typeof interface type extends implements public private static void null undefined true false self'.split(' '));
  /**
   * Input: line (string)
   * Output: string
   * Processing: One line of code as escaped HTML with keywords, strings, numbers and comments coloured.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function highlight(line){let out='',last=0;for(const m of line.matchAll(/(#.*$|\/\/.*$)|("(?:[^"\\]|\\.)*"?|'(?:[^'\\]|\\.)*'?|`[^`]*`?)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_$][\w$]*)/g)){out+=esc(line.slice(last,m.index));const t=esc(m[0]);out+=m[1]?`<span class="c">${t}</span>`:m[2]?`<span class="s">${t}</span>`:m[3]?`<span class="n">${t}</span>`:KW.has(m[0])?`<span class="k">${t}</span>`:t;last=m.index+m[0].length}return out+esc(line.slice(last))}
  /**
   * Input: source (object), inner (object), all (boolean)
   * Output: object
   * Processing: The code panel for a box (every line, or only the ones that fit) and the height it needs.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function codeHTML(source,inner,all,sizing=inner){const lines=codeLines(source),{font,line,pad}=codeMetrics(sizing,lines?lines.length:0);
    if(!lines)return {html:`<div class="nest-code loading" style="left:${inner.x+pad}px;top:${inner.y+pad}px;width:${inner.w-2*pad}px;font-size:${font}px">Loading code…</div>`,height:0};
    const fit=all?Math.min(lines.length,4000):Math.max(1,Math.floor((inner.h-2*pad)/line)),shown=lines.slice(0,fit),first=source.from||1;
    const rows=shown.map((l,i)=>`<div><i>${first+i}</i>${highlight(l)||' '}</div>`).join('');
    const more=all&&lines.length>fit?`<div class="more">… ${lines.length-fit} more lines; use Edit code to see them all</div>`:'';
    return {html:`<div class="nest-code" style="--w:${inner.w};left:${inner.x+pad}px;top:${inner.y+pad}px;width:${inner.w-2*pad}px;${all?'':`height:${inner.h-2*pad}px;`}font-size:${font}px;line-height:${line}px">${rows}${more}</div>`,height:2*pad+(shown.length+(more?1:0))*line}}
  /**
   * Input: b (object)
   * Output: string
   * Processing: The one-line description shown under a block's name.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function summaryOf(b){if(['service','server'].includes(b.kind)){const eps=Object.values((state.data?.board||{}).endpoints||{}).filter(e=>e.service===b.id);if(eps.length)return eps.slice(0,4).map(e=>`${e.method} ${e.path}`).join(' · ')}
    const l=b.meta?.lines;return b.meta?.summary||b.meta?.description||(l?`lines ${l[0]}–${l[1]}${b.meta?.file?` of ${b.meta.file.split('/').pop()}`:''}`:'')||b.path||b.meta?.language||''}
  /**
   * Input: b (object)
   * Output: string
   * Processing: The status badge for a block: AI-made, generated code, or verified.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function badgeOf(b){if(b.meta?.source==='ai')return `<span class="nest-badge ai">✦ ${esc({anthropic:'claude',user:'you'}[b.meta.by]||b.meta.by||'AI')}</span>`;if(['generated','new','unverified'].includes(b.status))return '<span class="nest-badge gen">Generated</span>';if(b.status==='missing')return '<span class="nest-badge gen">Missing</span>';return ''}
  /**
   * Input: b (object), r (object)
   * Output: string
   * Processing: A small tile for a block seen inside its parent's card.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function tileHTML(b,r){const font=Math.min(r.w*.075,r.h*.24),n=childrenOf(b.id).length,hint=n?`${n} inside`:codeSource(b)?'{ } code':'';
    return `<div class="nest-tile ${esc(b.kind)}-tile" data-tile="${esc(b.id)}" style="--w:${r.w};left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px;font-size:${font}px"><b>${kinds[b.kind]||'□'} ${esc(b.name||b.id)}</b><small>${esc(hint)}</small></div>`}
  /**
   * Input: b (object), r (object)
   * Output: string
   * Processing: A card for a block inside the focus, with its own children (or code) previewed inside it.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function cardHTML(b,r){const inner=innerOf(r),font=inner.head*.34,kids=childrenOf(b.id);let inside='';
    if(kids.length)grid(kids.length,inner).forEach((cell,i)=>{inside+=tileHTML(kids[i],cell);nest.items.set(kids[i].id,{level:2,rect:cell,parent:b.id})});
    else if(codeSource(b))inside=codeHTML(codeSource(b),inner,false).html;
    else inside=`<div class="nest-empty" style="left:${inner.x}px;top:${inner.y+inner.h*.35}px;width:${inner.w}px;font-size:${font*.7}px">${esc(summaryOf(b)||b.kind)}</div>`;
    return `<article class="nest-card ${esc(b.kind)}-card ${state.selected===b.id?'selected':''}" data-block="${esc(b.id)}" style="--w:${r.w};left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px"><header style="height:${inner.head}px;font-size:${font}px"><span class="nest-icon">${kinds[b.kind]||'□'}</span><span class="nest-name">${esc(b.name||b.id)}</span>${badgeOf(b)}<small>${esc(b.kind)}${summaryOf(b)?' · '+esc(summaryOf(b)):''}</small></header></article>${inside}`}
  /**
   * Input: none
   * Output: object
   * Processing: The block being looked at (top of the zoom path), dropping any that no longer exist.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function focusEntry(){const all=boardBlocks(),v=$('#canvas-viewport');if(!nest.stack.length)nest.stack=[{id:null,w:FRAME_W,h:FRAME_W*Math.max(.45,Math.min(1,(v.clientHeight||600)/(v.clientWidth||1000)))}];
    const bad=nest.stack.findIndex((e,i)=>i>0&&!all[e.id]);if(bad>0)nest.stack=nest.stack.slice(0,bad);return nest.stack[nest.stack.length-1]}
  /**
   * Input: none
   * Output: void
   * Processing: Redraw the focus frame, its cards, their tiles and the arrows between cards.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function render(){if(!state.data)return;const top=focusEntry(),all=boardBlocks(),focus=top.id?all[top.id]:null,kids=childrenOf(top.id);nest.items=new Map();
    let frame={x:0,y:0,w:top.w,h:top.h},inner=innerOf(frame),html='',leafCode=!kids.length&&focus&&codeSource(focus);
    if(leafCode){if(top.h0==null)top.h0=top.h;const code=codeHTML(codeSource(focus),inner,true,innerOf({x:0,y:0,w:top.w,h:top.h0}));if(code.height){top.h=Math.max(top.h,inner.head+code.height+inner.w*.03);frame.h=top.h}html+=code.html}
    else if(kids.length)grid(kids.length,inner).forEach((cell,i)=>{nest.items.set(kids[i].id,{level:1,rect:cell});html+=cardHTML(kids[i],cell)});
    else html+=`<div class="nest-empty big" style="left:0;top:${frame.h*.42}px;width:${frame.w}px;font-size:${frame.w/55}px">${focus?'Nothing inside this block yet.':'No project files on the board yet.'}<br><button data-action="${focus?'generate-blocks':'import'}">${focus?'✦ Generate blocks with AI':'Import project files'}</button></div>`;
    top.leaf=!!leafCode;
    const title=focus?`${kinds[focus.kind]||'□'} ${esc(focus.name||focus.id)}`:`▰ ${esc(projectName())}`,sub=focus?summaryOf(focus):'Scroll over a block to zoom into it';
    $('#blocks').innerHTML=`<section class="nest-frame ${leafCode?'code-frame':''}" style="width:${frame.w}px;height:${frame.h}px"><header style="height:${inner.head}px;font-size:${inner.head*.3}px"><b>${title}</b>${focus?badgeOf(focus):''}<small>${esc(sub)}</small></header></section>${html}`;
    drawArrows(frame);bindCards();renderCrumbs();renderFocusBar();renderMinimap();renderSidebar();applyCamera();
    $('#change-count').textContent=state.log.length?`${state.log.length} recent ${state.log.length===1?'change':'changes'}`:'No changes yet'}
  /**
   * Input: frame (object)
   * Output: void
   * Processing: Arrows between the cards in view; an arrow to a block deeper inside goes to the card holding it.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function drawArrows(frame){const all=boardBlocks(),board=state.data.board||state.data,holder=id=>{let b=all[id],guard=0;while(b&&guard++<20){const it=nest.items.get(b.id);if(it&&it.level===1)return b.id;b=all[b.parent]}return null};
    const edge=(r,tx,ty)=>{const cx=r.x+r.w/2,cy=r.y+r.h/2,dx=tx-cx,dy=ty-cy,k=1/Math.max(Math.abs(dx)/(r.w/2)||0,Math.abs(dy)/(r.h/2)||0,1e-6);return [cx+dx*Math.min(k,1),cy+dy*Math.min(k,1)]};
    const seen=new Set(),arrows=state.data.arrows?.length?state.data.arrows:Object.values(board.arrows||{});
    const paths=arrows.map(a=>{const s=holder(a.source||a.source_node_id),t=holder(a.target||a.target_node_id);if(!s||!t||s===t||seen.has(s+'>'+t))return '';seen.add(s+'>'+t);const A=nest.items.get(s).rect,B=nest.items.get(t).rect,[x1,y1]=edge(A,B.x+B.w/2,B.y+B.h/2),[x2,y2]=edge(B,A.x+A.w/2,A.y+A.h/2),cx=(x1+x2)/2,cy=(y1+y2)/2,label=a.endpoint||a.label||(a.type==='api'?'API':a.type||'');
      return `<path class="${esc(a.type||'calls')} ${esc(a.status||'')}" d="M ${x1} ${y1} L ${x2} ${y2}" marker-end="url(#arrowhead)"/><text x="${cx}" y="${cy-frame.w/250}" text-anchor="middle" style="font-size:${frame.w/130}px">${esc(label)}</text>`}).join('');
    $('#connections').setAttribute('width',frame.w);$('#connections').setAttribute('height',frame.h);
    $('#connections').innerHTML=paths+`<defs><marker id="arrowhead" markerWidth="7" markerHeight="7" refX="5" refY="3" orient="auto"><path d="M0,0 L0,6 L6,3 z" fill="#aaa3ca"/></marker></defs>`}
  /**
   * Input: none
   * Output: void
   * Processing: Click selects a card, double-click zooms into it (or into the tile clicked).
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function bindCards(){$$('#blocks .nest-card').forEach(n=>{n.addEventListener('click',e=>{e.stopPropagation();state.selected=n.dataset.block;$$('#blocks .nest-card').forEach(x=>x.classList.toggle('selected',x===n));showSelection(n)});n.addEventListener('dblclick',e=>{e.stopPropagation();flyInto(n.dataset.block)})});
    $$('#blocks .nest-tile').forEach(n=>{n.addEventListener('click',e=>{e.stopPropagation();const parent=nest.items.get(n.dataset.tile)?.parent;state.selected=n.dataset.tile;const card=parent&&$(`#blocks .nest-card[data-block="${CSS.escape(parent)}"]`);if(card)showSelection(card,n.dataset.tile)});n.addEventListener('dblclick',e=>{e.stopPropagation();flyTo(n.dataset.tile)})})}
  /**
   * Input: none
   * Output: void
   * Processing: The zoom path as clickable breadcrumbs.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function renderCrumbs(){const all=boardBlocks();$('#breadcrumbs').innerHTML=nest.stack.map((e,i)=>{const name=e.id?(all[e.id]?.name||e.id):projectName();return `${i?'<span class="crumb-sep">/</span>':''}<button class="crumb ${i===nest.stack.length-1?'current':''}" data-action="crumb" data-crumb="${i}">${esc(name)}</button>`}).join('')}
  /**
   * Input: none
   * Output: void
   * Processing: The bar over the canvas naming the focus and offering what can be done with it.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function renderFocusBar(){const top=nest.stack[nest.stack.length-1],b=top?.id?boardBlocks()[top.id]:null,bar=$('#focus-bar');if(!bar)return;const src=b&&(b.path||b.meta?.file),code=b&&(codeSource(b)||b.kind==='file');
    bar.innerHTML=`${nest.stack.length>1?'<button data-action="zoom-up" title="Up one level (Esc)">↑ Up</button>':''}<span class="focus-name">${b?`${kinds[b.kind]||'□'} ${esc(b.name)}`:'▰ '+esc(projectName())}</span>${b&&src&&b.kind!=='folder'?'<button data-action="generate-blocks" title="Let Claude or DeepSeek group this code into blocks">✦ AI blocks</button>':''}${b&&b.kind==='folder'?'<button data-action="generate-blocks" title="Outline every file in this folder">✦ AI blocks for folder</button>':''}${b&&b.kind==='file'?'<button data-action="split-functions" title="One block per function, no AI needed">ƒ Functions</button>':''}${code?'<button data-action="edit-code" title="Open the code to read or edit">✎ Edit code</button>':''}${b?'<button data-action="focus-info">ⓘ</button>':''}`}
  /**
   * Input: none
   * Output: object
   * Processing: The canvas size in screen pixels.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function viewSize(){const v=$('#canvas-viewport');return {w:v.clientWidth||1000,h:v.clientHeight||600}}
  /**
   * Input: none
   * Output: void
   * Processing: Put the camera on screen: transform, fade level, grid and minimap window.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function applyCamera(){const world=$('#canvas-world');world.style.transform=`translate(${state.x}px,${state.y}px) scale(${state.scale})`;world.style.setProperty('--z',state.scale);updateGrid();updateMiniView()}
  /**
   * Input: none
   * Output: void
   * Processing: Kept for callers that move the camera directly (panning).
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function applyTransform(){nest.target=null;applyCamera()}
  /**
   * Input: s (number)
   * Output: number
   * Processing: Keep the zoom where the focus is still findable and code still readable.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function clampScale(s){const top=focusEntry(),v=viewSize(),min=nest.stack.length>1?1e-6:v.w*.25/top.w,max=top.leaf?40/(top.w/95):v.w*30/top.w;return Math.max(min,Math.min(max,s))}
  /**
   * Input: f (number), dx (number), dy (number)
   * Output: void
   * Processing: Change world units under the camera (and its target) without moving anything on screen.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function remap(f,ox,oy){for(const c of [state,nest.target]){if(!c)continue;c.x+=ox*c.scale;c.y+=oy*c.scale;c.scale/=f}}
  /**
   * Input: id (string), rect (object)
   * Output: void
   * Processing: Make a card the focus, keeping it exactly where it is on screen.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function enter(id,rect){const f=FRAME_W/rect.w;remap(f,rect.x,rect.y);nest.stack.push({id,w:FRAME_W,h:rect.h*f});state.selected=null;hidePopup();render();fadeIn()}
  /**
   * Input: none
   * Output: boolean
   * Processing: Go up to the parent, keeping the old focus where it is on screen.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function leave(){if(nest.stack.length<2)return false;const child=nest.stack.pop();render();const it=nest.items.get(child.id);if(!it){fit();return true}const r=it.rect,f=child.w/r.w;for(const c of [state,nest.target]){if(!c)continue;c.scale*=f;c.x-=r.x*c.scale;c.y-=r.y*c.scale}hidePopup();render();fadeIn();return true}
  /**
   * Input: none
   * Output: void
   * Processing: Blend new boxes in after the focus changes.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function fadeIn(){const b=$('#blocks');b.classList.remove('nest-fade');void b.offsetWidth;b.classList.add('nest-fade')}
  /**
   * Input: dir (number), ax (number), ay (number)
   * Output: void
   * Processing: After zooming, enter the card under the pointer once it fills the screen, or leave once the frame is small.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function checkDepth(dir,ax,ay){const v=viewSize(),top=focusEntry();
    if(dir<0&&nest.stack.length>1&&top.w*state.scale<LEAVE_AT*v.w&&(top.leaf||top.h*state.scale<LEAVE_AT*v.h)){leave();return}
    if(dir>0){const wx=(ax-state.x)/state.scale,wy=(ay-state.y)/state.scale;for(const [id,it] of nest.items){const r=it.rect;if(it.level!==1||wx<r.x||wy<r.y||wx>r.x+r.w||wy>r.y+r.h||!enterable(id))continue;if(r.w*state.scale>ENTER_AT*v.w||r.h*state.scale>ENTER_AT*v.h)enter(id,r);return}}}
  /**
   * Input: none
   * Output: boolean
   * Processing: Whether the system asks for less motion (then zoom jumps instead of easing).
   * Changed by: Claude Code
   * Date: 2026-11-13
   */
  function reducedMotion(){return !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches}
  /**
   * Input: next (number), cx (number | undefined), cy (number | undefined), smooth (boolean)
   * Output: void
   * Processing: Zoom around a screen point, eased over a few frames, entering or leaving blocks on the way.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function zoomAt(next,cx,cy,smooth=!reducedMotion()){const v=viewSize(),px=cx??v.w/2,py=cy??v.h/2,base=nest.target||{x:state.x,y:state.y,scale:state.scale},s=clampScale(next/state.scale*base.scale),wx=(px-base.x)/base.scale,wy=(py-base.y)/base.scale;
    nest.target={x:px-wx*s,y:py-wy*s,scale:s};nest.lastAnchor=[px,py];if(!smooth){Object.assign(state,nest.target);nest.target=null;applyCamera();checkDepth(Math.sign(s-base.scale),px,py);return}if(!nest.anim)nest.anim=requestAnimationFrame(stepCamera)}
  /**
   * Input: none
   * Output: void
   * Processing: One animation frame of the camera easing toward its target.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function stepCamera(){nest.anim=0;const t=nest.target;if(!t)return;const before=state.scale,k=.28;state.scale=Math.exp(Math.log(state.scale)+(Math.log(t.scale)-Math.log(state.scale))*k);state.x+=(t.x-state.x)*k;state.y+=(t.y-state.y)*k;
    const done=Math.abs(Math.log(t.scale/state.scale))<.002&&Math.abs(t.x-state.x)<.5&&Math.abs(t.y-state.y)<.5;if(done){Object.assign(state,t);nest.target=null;const id=nest.pendingEnter;nest.pendingEnter=null;const it=id&&nest.items.get(id);if(it&&it.level===1){enter(id,it.rect);return}}applyCamera();const [ax,ay]=nest.lastAnchor||[viewSize().w/2,viewSize().h/2];checkDepth(Math.sign(state.scale-before),ax,ay);if(nest.target)nest.anim=requestAnimationFrame(stepCamera)}
  /**
   * Input: animate (boolean)
   * Output: void
   * Processing: Fit the focus in the canvas (code frames show from the top).
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function fit(animate=false){if(!state.data)return;if(reducedMotion())animate=false;const top=focusEntry(),v=viewSize(),head=104,room={w:v.w-24,h:Math.max(120,v.h-head-16)},visibleH=top.leaf?Math.min(top.h,top.w*room.h/room.w):top.h,s=Math.min(room.w/top.w,room.h/visibleH),goal={scale:s,x:(v.w-top.w*s)/2,y:head+(top.leaf?0:(room.h-top.h*s)/2)};
    if(animate){nest.target=goal;nest.lastAnchor=null;if(!nest.anim)nest.anim=requestAnimationFrame(stepCamera)}else{nest.target=null;Object.assign(state,goal);applyCamera()}}
  /**
   * Input: id (string)
   * Output: void
   * Processing: Zoom smoothly into a card in view until it becomes the focus.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function flyInto(id){const it=nest.items.get(id);if(reducedMotion()&&it&&it.level===1&&enterable(id)){enter(id,it.rect);fit();return}if(!it||!enterable(id)){openBlock(id);return}if(it.level!==1){flyTo(id);return}const v=viewSize(),r=it.rect,head=104,rh=Math.max(120,v.h-head-16),s=clampScale(Math.min((v.w-24)/r.w,rh/r.h));nest.target={scale:s,x:v.w/2-(r.x+r.w/2)*s,y:head+rh/2-(r.y+r.h/2)*s};nest.lastAnchor=[v.w/2,head+rh/2];nest.pendingEnter=id;if(!nest.anim)nest.anim=requestAnimationFrame(stepCamera)}
  /**
   * Input: id (string)
   * Output: void
   * Processing: Jump straight to any block: rebuild the zoom path from the project down to it and fit it.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function flyTo(id){const all=boardBlocks();if(!all[id])return;const chain=[];let b=all[id],guard=0;while(b&&guard++<40){chain.unshift(b.id);b=all[b.parent]}
    nest.stack=nest.stack.slice(0,1);focusEntry();for(const next of chain){const top=nest.stack[nest.stack.length-1],kids=childrenOf(top.id),i=kids.findIndex(k=>k.id===next);if(i<0||!enterable(next))break;const r=grid(kids.length,innerOf({x:0,y:0,w:top.w,h:top.h}))[i];nest.stack.push({id:next,w:FRAME_W,h:r.h*FRAME_W/r.w})}
    if(nest.stack[nest.stack.length-1].id!==id)state.selected=id;render();fit();fadeIn()}
  /**
   * Input: none
   * Output: void
   * Processing: The overview map: the focus frame, its cards and the part of it on screen.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function renderMinimap(){const mini=$('.mini-inner');if(!mini)return;const top=focusEntry(),rect=mini.getBoundingClientRect(),k=Math.min((rect.width-8)/top.w,(rect.height-8)/top.h)||0;nest.mini={k,ox:(rect.width-top.w*k)/2,oy:(rect.height-top.h*k)/2};
    mini.innerHTML=[...nest.items].filter(([,it])=>it.level===1).map(([id,it])=>{const b=boardBlocks()[id]||{};return `<i class="mini-rect ${esc(b.kind||'')}" title="${esc(b.name||id)}" style="left:${nest.mini.ox+it.rect.x*k}px;top:${nest.mini.oy+it.rect.y*k}px;width:${Math.max(2,it.rect.w*k)}px;height:${Math.max(2,it.rect.h*k)}px"></i>`}).join('')+'<i class="mini-view"></i>';updateMiniView()}
  /**
   * Input: none
   * Output: void
   * Processing: Move the minimap's window to match the camera.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function updateMiniView(){const view=$('.mini-inner .mini-view'),m=nest.mini;if(!view||!m)return;const v=viewSize();view.style.cssText=`left:${m.ox+(-state.x/state.scale)*m.k}px;top:${m.oy+(-state.y/state.scale)*m.k}px;width:${v.w/state.scale*m.k}px;height:${v.h/state.scale*m.k}px`}
  /**
   * Input: none
   * Output: object | null
   * Processing: The block the focus bar's actions apply to: the selected card, else the focus.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function actionBlock(){const all=boardBlocks(),top=nest.stack[nest.stack.length-1];return (state.selected&&all[state.selected])||(top?.id?all[top.id]:null)}
  /**
   * Input: b (object)
   * Output: Promise<void>
   * Processing: Ask the chosen AI to outline a block's file (or folder) as nested blocks; explain the options when there is no key.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  async function generateBlocks(b){if(!b){toast('Zoom into a file or folder first.',true);return}const path=b.kind==='folder'?(b.path||''):(b.meta?.file||b.path);if(path==null){toast('This block has no code behind it.',true);return}const provider=$('#provider-select')?.value||'anthropic';
    toast(`${providerLabel(provider)} is reading ${path||'the project'}…`);try{const data=await request('command',{method:'POST',body:JSON.stringify({command:`/blocks generate ${quoteArg(path||'.')} --provider ${provider}`})});const r=data.result||{};await refresh();toast(`${r.blocks||0} blocks made by ${providerLabel(provider)}${r.failed?.length?`; ${r.failed.length} files failed`:''}.`);const owner=b.kind==='folder'?b.id:(boardBlocks()[r.files?.[0]?.block]?r.files[0].block:b.id);flyTo(owner)}
    catch(e){if(/API key/i.test(e.message))openModal('Connect an AI to make blocks',`<p>${esc(e.message)}</p><div class="row"><b>Use Claude on your account (no API key)</b><br>In Claude Code with the Plovane MCP connection, ask:<pre class="code-view">Use Plovane to generate blocks for ${esc(path)}: read the file, then call plovane_compile with action "blocks_generate", target "${esc(path)}" and your blocks.</pre></div><div class="modal-actions"><button class="secondary" data-action="split-functions">ƒ One block per function (no AI)</button><button data-action="connections">Add an API key…</button></div>`,'AI BLOCKS');else toast(e.message,true)}}
  /**
   * Input: b (object)
   * Output: Promise<void>
   * Processing: Blocks for a file without AI: its functions, with their statements inside.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  async function splitFunctions(b){const path=b&&(b.meta?.file||b.path);if(!path){toast('Zoom into a file first.',true);return}try{await request('command',{method:'POST',body:JSON.stringify({command:`/blocks view ${quoteArg(path)} --detail 2 --discard`})});$('#modal').open&&$('#modal').close();await refresh();const owner=Object.values(boardBlocks()).find(x=>x.kind==='file'&&x.path===path);if(owner)flyTo(owner.id)}catch(e){toast(e.message,true)}}
  /**
   * Input: b (object)
   * Output: Promise<void>
   * Processing: Open a block's file in the code editor, scrolled to and selecting the block's lines.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  async function editCode(b){const src=codeSource(b),path=src?.path||(b?.kind==='file'?b.path:null);if(!path){toast('This block has no file to edit.',true);return}
    try{const data=await request('command',{method:'POST',body:JSON.stringify({command:`/open ${quoteArg(path)}`})}),text=data.result?.content??'',ed=$('#code-editor'),area=$('#code-editor textarea');ed.dataset.path=path;ed.base=text;area.value=text;$('#code-editor .editor-path').textContent=path;ed.hidden=false;syncGutter();area.focus();
      if(src&&src.to!==Infinity){const lines=text.split('\n'),start=lines.slice(0,src.from-1).join('\n').length+(src.from>1?1:0),end=lines.slice(0,src.to).join('\n').length;area.setSelectionRange(start,end);area.scrollTop=(src.from-3)*parseFloat(getComputedStyle(area).lineHeight)}else area.setSelectionRange(0,0)}catch(e){toast(e.message,true)}}
  /**
   * Input: none
   * Output: void
   * Processing: Line numbers beside the editor, scrolled with it.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function syncGutter(){const area=$('#code-editor textarea'),gutter=$('#code-editor .editor-gutter'),n=area.value.split('\n').length;if(gutter.childElementCount!==n)gutter.textContent=Array.from({length:n},(_,i)=>i+1).join('\n');gutter.scrollTop=area.scrollTop;$('#code-editor').classList.toggle('dirty',area.value!==$('#code-editor').base)}
  /**
   * Input: none
   * Output: Promise<void>
   * Processing: Save the editor's text to the file (refused if the file changed meanwhile); undo restores it.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  async function saveCode(){const ed=$('#code-editor'),path=ed.dataset.path,content=$('#code-editor textarea').value;try{const data=await request('op',{method:'POST',body:JSON.stringify({op:'file.save',args:{path,content,base:ed.base}})});ed.base=content;syncGutter();delete nest.code[path];toast(data.result?.changed===false?'No changes to save.':`Saved ${path}. ⌘Z on the board undoes it.`);(data.notes||[]).forEach(n=>toast(n));await refresh()}catch(e){toast(e.message,true)}}
  /**
   * Input: none
   * Output: void
   * Processing: Close the editor, asking first when there are unsaved edits.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function closeEditor(){const ed=$('#code-editor');if($('#code-editor textarea').value!==ed.base&&!confirm('Discard your unsaved edits?'))return;ed.hidden=true}
  function renderSidebar(){
    const board=state.data.board||state.data,blocks=Object.values(board.blocks||{}),host=$('#sidebar-content');
    if(state.tab==='files'){
      const folders=blocks.filter(b=>b.kind==='folder'),files=blocks.filter(b=>b.kind==='file');
      host.innerHTML=`<div class="side-section-title">PROJECT FILES <button data-action="new-file">＋</button></div><div class="tree-row selected"><span class="tree-icon">⌄</span><span class="tree-icon">▰</span>${esc(projectName())}</div>${folders.map(f=>`<div class="tree-row tree-indent" data-open="${esc(f.id)}"><span class="tree-icon">⌄</span><span class="tree-icon">▰</span>${esc(f.name)}</div>${files.filter(x=>inFolder(x,f)).map(x=>`<div class="tree-row tree-indent" data-open="${esc(x.id)}"><span class="tree-icon file">▧</span>${esc(x.name)}<span class="tree-count">${x.status==='generated'?'●':''}</span></div>`).join('')}`).join('')}${files.filter(x=>!folders.some(f=>inFolder(x,f))).map(x=>`<div class="tree-row tree-indent" data-open="${esc(x.id)}"><span class="tree-icon file">▧</span>${esc(x.name)}</div>`).join('')}${folders.length||files.length?'':'<div class="empty-state">No files on the board yet. Use File › Import project, or ＋ to create one.</div>'}`;
    }else if(state.tab==='agents'){
      host.innerHTML=agentsPanel(board);
    }else if(state.tab==='activity'){
      host.innerHTML=activityPanel();
    }else if(state.tab==='services'){
      const svcs=blocks.filter(b=>['service','server','database','external'].includes(b.kind));
      host.innerHTML=`<div class="side-section-title">SERVICES <button data-action="service-new" title="Add a service">＋</button></div>${svcs.map(s=>`<div class="service-card"><b>${kinds[s.kind]||'⬡'} &nbsp;${esc(s.name)}</b><small>${esc(s.meta?.language||s.kind)} · ${Object.values(board.endpoints||{}).filter(e=>e.service===s.id).length||'—'} endpoints</small></div>`).join('')||'<div class="empty-state">No services on this layer.</div>'}`;
    }else{
      const code=blocks.filter(b=>['file','function','code'].includes(b.kind));
      host.innerHTML=`<div class="side-section-title">CODE & PROVENANCE <button data-command="/history">◷</button></div>${code.map(b=>`<div class="code-card"><div class="tree-row" data-open="${esc(b.id)}"><span class="tree-icon file">${kinds[b.kind]}</span>${esc(b.name)}<span class="tree-count">${b.status==='generated'?'NEW':'✓'}</span></div><small style="display:block;padding:2px 7px;color:#a1a6b0;font-size:8px">${esc(b.path||'Prompt: '+(b.meta?.prompt||'verified library'))} · ${Number(b.meta?.tokens||0)} tokens</small></div>`).join('')||'<div class="empty-state">Select a file to inspect its source.</div>'}`;
    }
    host.onclick=e=>{if(e.target.closest('[data-action],[data-command]'))return;const open=e.target.closest('[data-open]');if(open)flyTo(open.dataset.open)};
  }
  function showSelection(node,id=node.dataset.block){const p=$('#selection-popup'),r=node.getBoundingClientRect(),v=$('#canvas-viewport').getBoundingClientRect(),b=boardBlocks()[id];if(!b)return;p.style.left=`${Math.max(8,Math.min(v.width-360,r.left-v.left))}px`;p.style.top=`${Math.max(52,r.top-v.top-40)}px`;p.innerHTML=`${enterable(id)?`<button data-action="zoom-into" data-block="${esc(id)}">⤢ Zoom in</button>`:''}<button data-action="info">ⓘ Details</button>${(b.path||b.meta?.file)?'<button data-action="generate-blocks">✦ AI blocks</button><button data-action="edit-code">✎ Code</button>':''}<button data-action="edit">Rename</button><button data-action="connect">↗ Connect</button>`;p.classList.add('show')}
  function openModal(title,body,eyebrow='PLOVANE'){ $('#modal-title').textContent=title;$('#modal-eyebrow').textContent=eyebrow;$('#modal-body').innerHTML=body;$('#modal').showModal() }
  function openBlock(id){const board=state.data.board||state.data,b=board.blocks?.[id];if(!b)return;state.selected=id;const p=position(b),arrows=(state.data.arrows||Object.values(board.arrows||{})).filter(a=>a.source===id||a.target===id);let body=`<div class="row"><b>${esc(b.kind)}</b> · ${esc(b.status||'verified')}<br><span class="muted">${esc(b.path||b.id)} · Layer ${b.layer}</span></div><div class="row">Position ${Math.round(p.x)}, ${Math.round(p.y)} · ${p.w||b.w} × ${p.h||b.h}</div>`;if(b.kind==='code'||b.kind==='function')body+=`<div class="row">Source · ${esc(b.path||b.meta?.file||b.name)}</div><button data-action="open-code" data-block="${esc(id)}">Show code</button>`;if(b.kind==='file')body+=`<div class="row">Source · ${esc(b.path||b.name)}</div><button data-action="open-code" data-block="${esc(id)}">Open source</button>`;body+=`<div class="row"><b>Connections (${arrows.length})</b><br>${arrows.map(a=>`${esc(a.type)} · ${esc(a.endpoint||`${a.source} → ${a.target}`)} · ${esc(a.status)}`).join('<br>')||'No connections'}</div><div class="row">Provenance · ${b.status==='generated'?'Generated code awaiting approval':'Verified library code'}<br><span class="muted">${esc(b.meta?.prompt||'Prompt provenance available in project history')} · ${Number(b.meta?.tokens||0)} tokens</span></div>`;openModal(b.name,body,'BLOCK DETAILS');render()}
  function record(prompt,data){const entry={prompt,at:new Date().toISOString(),tokens:Math.max(1,Math.ceil(prompt.trim().split(/\s+/).length*1.35)),result:data?.result?.message||data?.result?.id||'Command completed',kind:'prompt'};state.log.unshift(entry);state.log=state.log.slice(0,100);state.lastPrompt=prompt;localStorage.setItem('plovane-prompt-history',JSON.stringify(state.log));$('#token-count').textContent=`${state.log.reduce((n,x)=>n+x.tokens,0)} tokens`;$('#change-count').textContent=`${state.log.length} recent ${state.log.length===1?'change':'changes'}`;render()}
  async function runCommand(command,confirm=false,prompt=command){try{const data=await request('command',{method:'POST',body:JSON.stringify({command,confirm})});if(data.status==='needs_confirmation'){openModal('Review before applying',`<p>${esc(data.message||'This command needs confirmation.')}</p><pre>${esc(JSON.stringify(data.preview||{},null,2))}</pre><div class="modal-actions"><button class="secondary" data-action="close-modal">Cancel</button><button data-action="confirm-command" data-command="${esc(command)}">Review & apply</button></div>`,'CONFIRMATION REQUIRED');return}record(prompt,data);await refresh();const changed=Object.keys(data.changes||{}).length>0;if(!changed&&data.result&&!(data.result.diff||data.diff)){openModal(command.split(' ').slice(0,2).join(' '),`${data.notes?`<div class="row">${esc([].concat(data.notes).join(' '))}</div>`:''}<pre>${esc(JSON.stringify(data.result,null,2))}</pre>`,'RESULT');return}toast(data.result?.message||data.result?.id||'Change applied to project graph.');const result=data.result||{};if(result.diff||data.diff){const diff=result.diff||data.diff,next=result.next||'';openModal('Review generated changes',`<p>Review this generated diff before it changes project files. Affected tests run in the backend sandbox.</p><pre>${esc(diff)}</pre>${result.tests?`<div class="row">Tests · ${result.tests.passed===false?'failed':'passed'} · ${esc(JSON.stringify(result.tests))}</div>`:''}<div class="modal-actions"><button class="secondary" data-action="close-modal">Keep reviewing</button>${next.startsWith('/compile apply')?`<button data-action="apply-compile" data-command="${esc(next)}">Approve & apply</button>`:''}</div>`,'GENERATED CODE');}}catch(e){toast(e.message,true)}}
  async function refresh(){try{state.data=await request('board');state.online=true;state.demo=false;$('#breadcrumbs').title=state.data.project||'';showProject();if(await autoImport())state.data=await request('board');render();applyFlash();return true}catch(e){state.data=demoBoard();state.online=false;render();return false}}
  async function history(action){try{await request(action,{method:'POST',body:'{}'});await refresh();toast(`${action} complete.`)}catch(e){toast(e.message,true)}}
  function promptCommand(text){
    const v=text.trim();if(!v)return;
    if(v.startsWith('/'))return v;
    let m=v.match(/\b(?:add|create)\s+(?:a\s+)?(service|server|database|queue|external|folder|group|agent)\s+(?:called|named)\s+["“]?([^"”]+?)["”]?\s*$/i);
    if(m){const kind=m[1].toLowerCase(),name=m[2].trim();if(kind==='folder')return `/folder create ${name}`;if(kind==='agent')return `/agent add "${name}"`;const layer=['service','server','database','queue','external'].includes(kind)?1:2;return `/block add ${kind} "${name}" --layer ${layer}`}
    m=v.match(/\b(?:add|create)\s+(?:a\s+)?file\s+(?:at\s+)?["“]?([\w./-]+)["”]?\s*$/i);if(m)return `/file create ${m[1]}`;
    m=v.match(/\bconnect\s+["“]?([\w./-]+)["”]?\s+to\s+["“]?([\w./:-]+)["”]?/i);if(m)return `/connect ${m[1]} -> ${m[2]} --type api`;
    if(/\b(scan|security scan|check for vulnerabilities)\b/i.test(v))return '/scan';
    if(state.selected)return `/block edit ${state.selected} "${v.replaceAll('"','\\"')}"`;
    return `/rank "${v.replaceAll('"','\\"')}" --k 5`;
  }
  function submitPrompt(){const input=$('#prompt-input'),text=input.value.trim();if(!text)return;if(/^\/agent\s+add\b/i.test(promptCommand(text)||'')){input.value='';resizePrompt();openCli(promptCommand(text)+' ');toast('Agents are added in the Plovane CLI. Press Enter there to add it.');return}input.value='';resizePrompt();$('#autocomplete').hidden=true;if(text.startsWith('/'))runCommand(text,false,text);else planPrompt(text)}
  async function planPrompt(prompt){try{const plan=await request('prompt/plan',{method:'POST',body:JSON.stringify({prompt,provider:$('#provider-select')?.value||'anthropic'})});state.pendingPrompts=state.pendingPrompts||{};state.pendingPrompts[plan.plan_id]={prompt,tokens:plan.tokens||{}};state.history.unshift(prompt);state.history=state.history.slice(0,40);localStorage.setItem('plovane-prompt-history',JSON.stringify(state.history));const candidates=plan.library_candidates||[];const library=candidates.length?`<div class="row"><b>Verified library candidates</b><br>${candidates.map(x=>`${esc(x.name||x.block_id)} · ${esc(x.block_id)} · score ${esc(x.score??'n/a')}`).join('<br>')}</div>`:'<div class="row">No verified library match cleared the cutoff. Plan uses new operations.</div>';openModal('Review AI plan',`<p>${esc(plan.summary||'Review the proposed operations before applying them.')}</p>${library}<div class="row"><b>Planned commands</b><pre>${esc(plan.commands.join('\n'))}</pre></div><div class="row"><b>Server preview</b><pre>${esc(JSON.stringify(plan.preview,null,2))}</pre></div><div class="row">${esc(providerLabel(plan.provider))} tokens · ${Number(plan.tokens?.input_tokens||0)} input · ${Number(plan.tokens?.output_tokens||0)} output</div><div class="modal-actions"><button class="secondary" data-action="close-modal">Cancel</button><button data-action="approve-prompt-plan" data-plan-id="${esc(plan.plan_id)}">Approve and apply</button></div>`,'APPROVAL REQUIRED')}catch(error){toast(error.message,true)}}
  async function applyPromptPlan(planId,confirm=false){const plan=state.pendingPrompts?.[planId];if(!plan)return;try{const data=await request('prompt/apply',{method:'POST',body:JSON.stringify({plan_id:planId,confirm})});if(data.status==='needs_confirmation'){openModal('Confirm risky operations',`<p>${esc(data.message||'These operations need confirmation.')}</p><pre>${esc(JSON.stringify(data.preview||{},null,2))}</pre><div class="modal-actions"><button class="secondary" data-action="close-modal">Cancel</button><button data-action="confirm-prompt-plan" data-plan-id="${esc(planId)}">Confirm and apply</button></div>`,'CONFIRMATION REQUIRED');return}$('#modal').close();const tokens=Number(plan.tokens?.input_tokens||0)+Number(plan.tokens?.output_tokens||0);state.log.unshift({prompt:plan.prompt,at:new Date().toISOString(),tokens,token_usage:plan.tokens,kind:'prompt',result:'Approved AI plan'});state.log=state.log.slice(0,100);localStorage.setItem('plovane-prompt-history',JSON.stringify(state.log));$('#token-count').textContent=`${state.log.reduce((n,x)=>n+(x.tokens||0),0)} tokens`;await refresh();toast('Approved plan saved to the project graph and files.')}catch(error){toast(error.message,true)}}
  function applyLayout(){const root=document.documentElement;root.style.setProperty('--sidebar-width',`${layout.sideWidth}px`);root.style.setProperty('--sidebar-visible-width',`${layout.sideOpen?layout.sideWidth:0}px`);root.style.setProperty('--prompt-height',`${layout.bottomOpen?layout.bottomHeight:0}px`);root.style.setProperty('--corner-overlap',`${layout.sideOpen&&layout.bottomOpen?layout.overlap:0}px`);$('#sidebar').classList.toggle('collapsed',!layout.sideOpen);$('.prompt-dock').classList.toggle('closed',!layout.bottomOpen);if(!layout.bottomOpen){$('#cli-panel').classList.remove('open');$('.prompt-dock').classList.remove('cli-open');document.body.classList.remove('cli-open');const button=$('[data-action="toggle-cli"]');button.classList.remove('active');button.setAttribute('aria-expanded','false')}localStorage.setItem('plovane-whiteboard-layout',JSON.stringify(layout))}
  function toggleSide(){layout.sideOpen=!layout.sideOpen;applyLayout()}
  function toggleBottom(){layout.bottomOpen=!layout.bottomOpen;applyLayout()}
  function setTheme(theme){document.body.dataset.theme=theme;localStorage.setItem('plovane-theme',theme);updateGrid()}
  const accents={red:['#D94A4A','#FCE8E6'],white:['#B7BDC8','#F2F3F5'],orange:['#E77928','#FFF0E3'],yellow:['#FCCE03','#FFF6CC'],green:['#25845F','#E7F5ED'],blue:['#397BD1','#E8F1FE'],purple:['#7357E8','#F0EDFF'],tan:['#A87845','#F5EBDD']};
  function setAccent(name){const colors=accents[name]||accents.purple;document.documentElement.style.setProperty('--accent',colors[0]);document.documentElement.style.setProperty('--accent-soft',colors[1]);localStorage.setItem('plovane-accent',name)}
  function openSettings(){const dark=document.body.dataset.theme==='dark',selected=localStorage.getItem('plovane-accent')||'purple';openModal('Settings',`<button class="library-link" data-action="connections">AI providers, API keys and MCP setup…</button><label class="settings-row" for="theme-toggle"><span><b>Dark mode</b><br><small>Use a darker whiteboard appearance.</small></span><input class="theme-switch" id="theme-toggle" type="checkbox" ${dark?'checked':''}></label><div class="settings-section"><b>Theme color</b><small>Choose your accent color.</small><div class="theme-colors">${Object.entries(accents).map(([name,colors])=>`<button class="theme-color ${selected===name?'selected':''}" type="button" data-theme-color="${name}" aria-pressed="${selected===name}" title="${name[0].toUpperCase()+name.slice(1)}"><span style="--swatch:${colors[0]}"></span>${name[0].toUpperCase()+name.slice(1)}</button>`).join('')}</div></div><div class="settings-section"><b>Grid</b><label class="settings-row" for="grid-toggle"><span>Show grid</span><input class="theme-switch" id="grid-toggle" type="checkbox" ${preferences.gridVisible?'checked':''}></label><label class="settings-control">Spacing <output>${preferences.gridSpacing}px</output><input id="grid-spacing" type="range" min="12" max="36" step="2" value="${preferences.gridSpacing}"></label><label class="settings-control">Contrast <output>${Math.round(preferences.gridContrast*100)}%</output><input id="grid-contrast" type="range" min="25" max="100" step="5" value="${Math.round(preferences.gridContrast*100)}"></label></div><div class="settings-section"><b>Canvas behavior</b><label class="settings-control">Zoom sensitivity <output>${Math.round(preferences.zoomSensitivity*100)}%</output><input id="zoom-sensitivity" type="range" min="50" max="200" step="10" value="${Math.round(preferences.zoomSensitivity*100)}"></label><label class="settings-control">Pan gesture<select id="pan-mode"><option value="drag" ${preferences.panMode==='drag'?'selected':''}>Drag canvas</option><option value="space" ${preferences.panMode==='space'?'selected':''}>Space + drag</option></select></label></div><div class="settings-section"><b>Code display</b><label class="settings-control">Font size <output>${preferences.codeFontSize}px</output><input id="code-font-size" type="range" min="10" max="20" step="1" value="${preferences.codeFontSize}"></label><label class="settings-control">Syntax theme<select id="code-theme"><option value="auto" ${preferences.codeTheme==='auto'?'selected':''}>Match appearance</option><option value="light" ${preferences.codeTheme==='light'?'selected':''}>Light</option><option value="dark" ${preferences.codeTheme==='dark'?'selected':''}>Dark</option></select></label></div>`,'')}
  function bindPanelResize(handle,kind){let start=null;handle.addEventListener('pointerdown',e=>{e.preventDefault();e.stopPropagation();start={x:e.clientX,y:e.clientY,width:layout.sideWidth,height:layout.bottomHeight,overlap:layout.overlap};handle.setPointerCapture(e.pointerId)});handle.addEventListener('pointermove',e=>{if(!start)return;if(kind==='side'){const width=start.width+e.clientX-start.x;if(width<80){layout.sideOpen=false}else{layout.sideOpen=true;layout.sideWidth=Math.max(180,Math.min(440,width))}}else if(kind==='bottom'){const height=start.height+start.y-e.clientY,maximum=Math.max(100,Math.min(innerHeight*.55,innerHeight-448));if(height<80){layout.bottomOpen=false}else{layout.bottomOpen=true;layout.bottomHeight=Math.max(100,Math.min(maximum,height))}}else{layout.overlap=Math.max(0,Math.min(180,start.overlap+start.x-e.clientX))}applyLayout()});const stop=()=>{start=null};handle.addEventListener('pointerup',stop);handle.addEventListener('pointercancel',stop)}
  bindPanelResize($('#side-resize'),'side');bindPanelResize($('#bottom-resize'),'bottom');bindPanelResize($('#corner-resize'),'corner');applyLayout();setTheme(localStorage.getItem('plovane-theme')||'light');setAccent(localStorage.getItem('plovane-accent')||'purple');applyCodePreferences();$('#workspace-name').textContent=localStorage.getItem('plovane-workspace-name')||'Payments API';
  function setTab(tab){state.tab=tab;$$('.side-tab').forEach(x=>x.classList.toggle('active',x.dataset.tab===tab));renderSidebar()}
  function menuClick(action){if(action==='toggle-cli'){const panel=$('#cli-panel'),open=!panel.classList.contains('open'),button=$('[data-action="toggle-cli"]');panel.classList.toggle('open',open);$('.prompt-dock').classList.toggle('cli-open',open);document.body.classList.toggle('cli-open',open);button.classList.toggle('active',open);button.setAttribute('aria-expanded',String(open));if(open)$('#cli-panel iframe').contentWindow.postMessage({type:'plovane-cli-focus'},location.origin)}else if(action==='home'){if(window.plovaneDesktop?.goHome)window.plovaneDesktop.goHome().catch(error=>toast(error.message,true));else toast('Home page opens inside the Plovane desktop app.',true)}else if(action==='undo'||action==='redo')history(action);else if(action==='zoom-in')zoomAt(state.scale*1.18);else if(action==='zoom-out')zoomAt(state.scale/1.18);else if(action==='fit')fit(true);else if(action==='toggle-sidebar')toggleSide();else if(action==='toggle-bottom-bar')toggleBottom();else if(action==='settings')openSettings();else if(action==='edit-workspace-name'){const current=$('#workspace-name').textContent,name=prompt('Workspace name',current);if(name?.trim()){const value=name.trim();$('#workspace-name').textContent=value;localStorage.setItem('plovane-workspace-name',value)}}else if(action==='close-modal')$('#modal').close();else if(action==='snapshot')runCommand('/snapshot "whiteboard snapshot"');else if(action==='import')runCommand('/project import');else if(action==='new-file'){const path=prompt('New file path','src/new_module.py');if(path)runCommand(`/file create ${path}`)}else if(action==='save')runCommand('/snapshot "whiteboard snapshot"');else if(action==='github')showGit();else if(action==='human-edit'){const note=prompt('Describe the edit you made by hand','Manual edit');if(note!==null)runCommand(`/snapshot "human edit: ${(note||'manual edit').replaceAll('"','\'')}"`)}else if(action==='commands'||action==='command-list')showCommands();else if(action==='history')showHistory();else if(action==='shortcuts')openModal('Keyboard shortcuts','<div class="row">Enter · Run prompt</div><div class="row">/ · Browse slash commands</div><div class="row">⌘ Z · Undo</div><div class="row">⇧ ⌘ Z · Redo</div><div class="row">Scroll · Zoom canvas</div><div class="row">Drag canvas · Pan</div>','HELP');else if(action==='about')openModal('Plovane Whiteboard','<p>An agentic coding workspace. Verified library code, the project graph, and your prompts meet on one infinite canvas.</p>','ABOUT');else if(action==='attach')showAttach();else if(action==='sidebar-menu')openModal('Workspace options','<button data-command="/project import">Import existing project</button> &nbsp; <button data-command="/history">View project history</button>','WORKSPACE');else if(action==='approve-change'){$('#modal').close();toast('Approval flow requires a previewed compile. Use /compile then /compile apply.')}else if(action==='connect'&&state.selected){const from=state.selected,to=prompt('Connect to block (name or id):');if(to)runCommand(`/connect ${from} -> ${to} --type api`)}else if(action==='edit'&&state.selected){const b=state.data.board.blocks[state.selected],name=prompt('New block name',b?.name||'');if(name)runCommand(`/block rename ${state.selected} "${name}"`)}else if(action==='info'&&state.selected)openBlock(state.selected);else if(action==='open-code'&&state.selected)showCode(state.selected)}
  function showCommands(){const list=state.commands;openModal('Slash commands',list.length?list.map(c=>`<div class="row"><code>${esc(c.command)}</code> <span class="muted">${esc(c.summary||'')}</span><br><button data-run-example="${esc(c.example||c.command)}">${esc(c.example||c.command)}</button></div>`).join(''):'<p>Connect to the whiteboard backend to load its full command reference.</p>','COMMAND REFERENCE')}
  async function showHistory(){try{const result=await request('log?last=100'),entries=result.entries||[];const rows=entries.map(x=>({prompt:x.args?.prompt||x.op||'Operation',tokens:x.args?.token_usage?.input_tokens||0,at:x.at,kind:x.op==='prompt.provenance'?'Prompt':'operation'}));const all=[...state.log,...rows].sort((a,b)=>new Date(b.at||0)-new Date(a.at||0));openModal('Project history',all.map(x=>`<div class="row"><b>${x.kind==='human'?'Human edit':x.kind==='operation'?'Backend operation':'Prompt'}</b> · ${esc(x.tokens||0)} tokens<br>${esc(x.prompt||'')}<br><span class="muted">${x.at?new Date(x.at).toLocaleString():''}</span></div>`).join('')||'<p>No project changes recorded yet.</p>','SERVER HISTORY')}catch(error){toast(error.message,true)}}
  function handleAction(node){if(extraAction(node))return;if(node.dataset.action==='approve-prompt-plan')applyPromptPlan(node.dataset.planId);else if(node.dataset.action==='confirm-prompt-plan')applyPromptPlan(node.dataset.planId,true);else if(node.dataset.action==='confirm-command'||node.dataset.action==='apply-compile')runCommand(node.dataset.command,node.dataset.action==='confirm-command'||node.dataset.action==='apply-compile');else if(node.dataset.command)runCommand(node.dataset.command);else if(node.dataset.action)menuClick(node.dataset.action);else if(node.dataset.runExample)runCommand(node.dataset.runExample)}
  function resizePrompt(){const t=$('#prompt-input');t.style.height='auto';t.style.height=`${Math.min(78,t.scrollHeight)}px`}
  function autocomplete(){const input=$('#prompt-input'),box=$('#autocomplete'),v=input.value;if(!v.startsWith('/')){box.hidden=true;return}const groupAliases={blocks:'block',endpoints:'endpoint',layers:'layer',agents:'agent',services:'service',files:'file',folders:'folder',arrows:'arrow',templates:'template',connections:'connection'};const normalize=value=>{const [head,...tail]=value.toLowerCase().split(/\s+/);return [`/${groupAliases[head.slice(1)]||head.slice(1)}`,...tail].join(' ')};const q=normalize(v);const isGroupQuery=[...Object.keys(groupAliases),...Object.values(groupAliases)].includes(q.slice(1));const limit=isGroupQuery?30:9;const list=state.commands.filter(c=>normalize(c.command).startsWith(q)||c.command.toLowerCase().includes(v.toLowerCase())||c.summary?.toLowerCase().includes(v.toLowerCase())).slice(0,limit);box.innerHTML=(list.length?list.map(c=>`<button data-example="${esc(c.example||c.command)}"><span>${esc(c.command)} <small>${esc(c.summary||'')}</small></span><small>↵</small></button>`):[`/rank "${esc(v.slice(1))}" --k 5`].map(c=>`<button data-example="${esc(c)}"><span>${esc(c)} <small>Search verified library</small></span><small>↵</small></button>`)).join('');box.hidden=false;$$('button',box).forEach(b=>b.onclick=()=>{input.value=b.dataset.example;box.hidden=true;input.focus()})}
  document.addEventListener('click',e=>{const trigger=e.target.closest('.menu-trigger');if(trigger){const wrap=trigger.closest('.menu-wrap'),was=wrap.classList.contains('open');$$('.menu-wrap').forEach(x=>{x.classList.remove('open');$('.menu-trigger',x).setAttribute('aria-expanded','false')});if(!was){wrap.classList.add('open');trigger.setAttribute('aria-expanded','true')}return}if(!e.target.closest('.menu-wrap'))$$('.menu-wrap').forEach(x=>x.classList.remove('open'));const tab=e.target.closest('.side-tab');if(tab)setTab(tab.dataset.tab);const action=e.target.closest('[data-action],[data-command],[data-run-example]');if(action)handleAction(action);if(e.target.id==='canvas-viewport'||e.target.id==='connections'||e.target.id==='blocks'){hidePopup();state.selected=null;render()}});
  document.addEventListener('change',e=>{if(e.target.id==='theme-toggle')setTheme(e.target.checked?'dark':'light')});
  $('#modal').addEventListener('click',e=>{if(e.target===$('#modal'))$('#modal').close()});
  $('#modal-body').addEventListener('input',e=>{const id=e.target.id;if(id==='grid-spacing'){preferences.gridSpacing=Number(e.target.value);e.target.previousElementSibling.value=`${preferences.gridSpacing}px`;updateGrid()}else if(id==='grid-contrast'){preferences.gridContrast=Number(e.target.value)/100;e.target.previousElementSibling.value=`${e.target.value}%`;updateGrid()}else if(id==='zoom-sensitivity'){preferences.zoomSensitivity=Number(e.target.value)/100;e.target.previousElementSibling.value=`${e.target.value}%`}else if(id==='code-font-size'){preferences.codeFontSize=Number(e.target.value);e.target.previousElementSibling.value=`${preferences.codeFontSize}px`;applyCodePreferences()}if(['grid-spacing','grid-contrast','zoom-sensitivity','code-font-size'].includes(id))savePreferences()});
  $('#modal-body').addEventListener('change',e=>{const id=e.target.id;if(id==='grid-toggle'){preferences.gridVisible=e.target.checked;updateGrid()}else if(id==='pan-mode')preferences.panMode=e.target.value;else if(id==='code-theme'){preferences.codeTheme=e.target.value;applyCodePreferences()}if(['grid-toggle','pan-mode','code-theme'].includes(id))savePreferences()});
  $('#modal-body').addEventListener('click',e=>{const button=e.target.closest('[data-theme-color]');if(!button)return;setAccent(button.dataset.themeColor);$$('[data-theme-color]',$('#modal-body')).forEach(item=>{const selected=item===button;item.classList.toggle('selected',selected);item.setAttribute('aria-pressed',String(selected))})});
  $$('.menu-wrap').forEach(wrap=>{
    const trigger=$('.menu-trigger',wrap);
    wrap.addEventListener('pointerenter',()=>trigger.setAttribute('aria-expanded','true'));
    wrap.addEventListener('pointerleave',()=>{
      if(!wrap.contains(document.activeElement))trigger.setAttribute('aria-expanded','false');
    });
    wrap.addEventListener('focusout',e=>{
      if(!wrap.contains(e.relatedTarget))trigger.setAttribute('aria-expanded','false');
    });
  });
  const workspaceName=$('#workspace-name');workspaceName.addEventListener('blur',()=>{const value=workspaceName.textContent.trim()||'Payments API';workspaceName.textContent=value;localStorage.setItem('plovane-workspace-name',value)});workspaceName.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();workspaceName.blur()}if(e.key==='Escape'){workspaceName.textContent=localStorage.getItem('plovane-workspace-name')||'Payments API';workspaceName.blur()}});
  $('#send-prompt').onclick=submitPrompt;$('#prompt-input').addEventListener('input',()=>{resizePrompt();autocomplete()});$('#prompt-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();submitPrompt()}if(e.key==='Escape')$('#autocomplete').hidden=true});$('#autocomplete').onclick=e=>{const b=e.target.closest('[data-example]');if(b){$('#prompt-input').value=b.dataset.example;$('#autocomplete').hidden=true;$('#prompt-input').focus()}};
  const viewport=$('#canvas-viewport');viewport.addEventListener('wheel',e=>{e.preventDefault();const r=viewport.getBoundingClientRect(),top=focusEntry();if(top.leaf&&!e.ctrlKey&&!e.metaKey){nest.target=null;state.x-=e.deltaX;state.y-=e.deltaY;applyCamera();return}zoomAt(state.scale*Math.exp(-e.deltaY*(e.ctrlKey?.01:.0015)*preferences.zoomSensitivity),e.clientX-r.left,e.clientY-r.top)},{passive:false});
  viewport.addEventListener('pointerdown',e=>{if(e.button!==0||e.target.closest('.zoom-controls,.minimap,.selection-popup,.focus-bar,button,.code-editor')||(preferences.panMode==='space'&&!spacePressed))return;state.drag={x:e.clientX,y:e.clientY,sx:state.x,sy:state.y,id:e.pointerId,moved:false}});
  viewport.addEventListener('pointermove',e=>{const d=state.drag;if(!d)return;if(!d.moved&&Math.hypot(e.clientX-d.x,e.clientY-d.y)<4)return;if(!d.moved){d.moved=true;viewport.classList.add('panning');viewport.setPointerCapture(d.id);hidePopup()}state.x=d.sx+e.clientX-d.x;state.y=d.sy+e.clientY-d.y;applyTransform()});
  viewport.addEventListener('pointerup',()=>{if(state.drag?.moved){const stop=ev=>{ev.stopPropagation();ev.preventDefault()};window.addEventListener('click',stop,{capture:true,once:true});setTimeout(()=>window.removeEventListener('click',stop,{capture:true}),0)}state.drag=null;viewport.classList.remove('panning')});
  viewport.addEventListener('click',e=>{if(!e.target.closest('.nest-card,.nest-tile,.selection-popup,.focus-bar')){state.selected=null;$$('#blocks .nest-card.selected').forEach(x=>x.classList.toggle('selected',false));hidePopup()}});
  viewport.addEventListener('dblclick',e=>{if(!e.target.closest('.nest-card,.nest-tile,.focus-bar'))fit(true)});window.addEventListener('resize',()=>{if(nest.stack.length===1)nest.stack=[];render()});window.addEventListener('keydown',e=>{if(e.code==='Space'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){spacePressed=true;if(preferences.panMode==='space')e.preventDefault()}if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='z'){e.preventDefault();history(e.shiftKey?'redo':'undo')}if(e.key==='/'&&document.activeElement!==$('#prompt-input')){$('#prompt-input').focus();$('#prompt-input').value='/';autocomplete()}if(e.key==='Escape'){$$('.menu-wrap').forEach(x=>x.classList.toggle('open',false));if(!$('#modal').open&&$('#code-editor').hidden&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&leave())fit(true)}});window.addEventListener('keyup',e=>{if(e.code==='Space')spacePressed=false});
  const promptHistory=state.history;state.log=promptHistory.slice(0,100);$('#token-count').textContent=`${state.log.reduce((n,x)=>n+(x.tokens||0),0)} tokens`;
  (async()=>{try{state.commands=(await request('commands')).commands||[]}catch(_){}await refresh();fit();if(!state.online)toast('Demo graph shown. Run "plovane open" in your project to load it.',true);else{connectEvents();loadProviders()}})();

  /* ---- Live updates, AI agents and connectors ---- */
  /**
   * Input: none
   * Output: string
   * Processing: The open project's folder name.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function projectName(){const p=String(state.data?.project||'');return p.split(/[\\/]/).filter(Boolean).pop()||'project'}
  /**
   * Input: file (object), folder (object)
   * Output: boolean
   * Processing: Whether a file block sits inside a folder block, by parent or path.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function inFolder(file,folder){if(file.parent)return file.parent===folder.id;const fp=(folder.path||folder.name||'').replace(/\/$/,'');return !!fp&&(file.path||'').startsWith(fp+'/')}
  /**
   * Input: provider (string)
   * Output: string
   * Processing: Display name for a provider id.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function providerLabel(provider){return {anthropic:'Claude',deepseek:'DeepSeek',openai:'OpenAI'}[provider]||'Claude'}
  /**
   * Input: none
   * Output: void
   * Processing: Put the real project name in the top bar and workspace heading.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function showProject(){const name=projectName();$('.project-name').textContent=name;$('.project-name').title=state.data?.project||'';if(!localStorage.getItem('plovane-workspace-name'))$('#workspace-name').textContent=name;$('.github-card small').textContent=name}
  /**
   * Input: none
   * Output: void
   * Processing: Briefly highlight blocks the last live change touched.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function applyFlash(){if(!flashIds.size)return;const ids=flashIds;flashIds=new Set();ids.forEach(id=>{const n=document.querySelector(`#blocks [data-block="${CSS.escape(id)}"],#blocks [data-tile="${CSS.escape(id)}"]`);if(n){n.classList.add('agent-flash');setTimeout(()=>n.classList.remove('agent-flash'),2600)}})}
  /**
   * Input: text (string), kind (string)
   * Output: void
   * Processing: Show the live connection state in the prompt bar.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function liveStatus(text,kind){const el=$('#live-status');if(el){el.textContent=text;el.dataset.kind=kind}}
  /**
   * Input: event (object)
   * Output: void
   * Processing: Add a change or agent step to the activity feed and redraw it when visible.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function pushActivity(event){activity.unshift(event);if(activity.length>200)activity.length=200;const badge=$('#activity-badge');if(badge&&state.tab!=='activity'){badge.hidden=false;badge.textContent=String(Math.min(99,Number(badge.textContent||0)+1))}if(state.tab==='activity'&&state.data)renderSidebar()}
  /**
   * Input: none
   * Output: void
   * Processing: Subscribe to the server's live events; redraw on every change, whoever made it.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function connectEvents(){if(!window.EventSource)return;const source=new EventSource(`${API}/events${TOKEN?`?token=${encodeURIComponent(TOKEN)}`:''}`);
    const schedule=()=>{clearTimeout(refreshTimer);refreshTimer=setTimeout(refresh,120)};
    source.addEventListener('hello',()=>liveStatus('● Live','on'));
    source.addEventListener('change',ev=>{const e=JSON.parse(ev.data);pushActivity(e);(e.changes?.files||[]).forEach(f=>delete nest.code[f]);const b=e.changes?.blocks||{};[...(b.added||[]),...(b.changed||[])].forEach(id=>flashIds.add(id));if(e.actor&&e.actor!=='user')toast(`${e.actor}: ${e.command||e.op}`);schedule()});
    source.addEventListener('external',ev=>{pushActivity(JSON.parse(ev.data));nest.code={};schedule()});
    source.addEventListener('agent',ev=>agentEvent(JSON.parse(ev.data)));
    source.onerror=()=>liveStatus('○ Reconnecting…','off')}
  /**
   * Input: event (object)
   * Output: void
   * Processing: Track an AI run's progress from its live events.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function agentEvent(e){if(e.run){const r=runs[e.run]||(runs[e.run]={id:e.run,agent:e.actor,status:'running',steps:0});if(e.type==='started')Object.assign(r,{provider:e.provider,model:e.model,prompt:e.prompt});if(e.type==='command')r.steps+=1;if(e.type==='finished'){r.status=e.status;toast(`${e.actor} ${e.status==='done'?'finished':e.status}${e.error?`: ${e.error}`:''}`,e.status==='error')}}if(e.type!=='tool')pushActivity(e);if(state.tab==='agents'&&state.data)renderSidebar()}
  /**
   * Input: e (object)
   * Output: string
   * Processing: One activity feed row.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function activityRow(e){const who=e.actor||'another process',time=e.at?new Date(e.at).toLocaleTimeString():'';let what;if(e.kind==='external')what='Board changed outside this window';else if(e.kind==='agent'){what=e.type==='started'?`Started (${esc(providerLabel(e.provider))}): ${esc(e.prompt||'')}`:e.type==='thought'?esc(e.text||''):e.type==='command'?`${e.ok?'✓':'✗'} <code>${esc(e.command||'')}</code>${e.error?` <span class="muted">${esc(e.error)}</span>`:''}`:e.type==='finished'?`Finished · ${esc(e.status)}${e.summary?` · ${esc(e.summary)}`:''}${e.error?` · ${esc(e.error)}`:''}`:e.type==='token'?`API token issued for ${esc(e.agent)}`:esc(e.type||'')}else what=`<code>${esc(e.command||e.op||'')}</code>`;return `<div class="activity-row ${e.actor&&e.actor!=='user'?'by-agent':''}"><div><b>${esc(who)}</b><span class="muted"> · ${esc(time)}</span></div><div>${what}</div></div>`}
  /**
   * Input: none
   * Output: string
   * Processing: The Activity tab: every live change and AI step, newest first.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function activityPanel(){const badge=$('#activity-badge');if(badge){badge.hidden=true;badge.textContent=''}return `<div class="side-section-title">LIVE ACTIVITY <span id="live-status-side" class="muted">${activity.length}</span></div>${activity.length?activity.map(activityRow).join(''):'<div class="empty-state">Changes from you, the CLI and AI agents appear here as they happen.</div>'}`}
  /**
   * Input: agent (object)
   * Output: string
   * Processing: A readable summary of an agent's grants.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function grantsText(agent){const g=agent.grants||[],r=agent.regions||[];if(!g.length&&!r.length)return 'No access yet';return [...g.map(x=>`${x.path&&x.path!=='.'?x.path:'whole project'}: ${x.mode}`),...r.map(x=>`area ${x.area.join(',')}: ${x.mode}`)].join(' · ')}
  /**
   * Input: board (object)
   * Output: string
   * Processing: The Agents tab: agents with their access, connect and run buttons, and running AI jobs.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function agentsPanel(board){const agents=Object.values(board.agents||{}),active=Object.values(runs).filter(r=>r.status==='running');return `<div class="side-section-title">AI AGENTS <button data-action="agent-new" title="Add an agent in the Plovane CLI">›_</button></div><button class="library-link ai-run-link" data-action="ai-run">✦ Let Claude or DeepSeek build</button>${active.map(r=>`<div class="agent-card running"><span class="agent-avatar">⟳</span><div><b>${esc(r.agent)}</b><small>${esc(providerLabel(r.provider))} · ${r.steps} commands</small></div><button class="access-chip" data-action="stop-run" data-run="${esc(r.id)}">Stop</button></div>`).join('')}${agents.length?agents.map(a=>`<div class="agent-card"><span class="agent-avatar">◉</span><div><b>${esc(a.name)}</b><small>${esc(grantsText(a))}</small></div></div><div class="agent-actions"><button data-action="agent-grant" data-agent="${esc(a.name)}">Access</button><button data-action="agent-connect" data-agent="${esc(a.name)}">Connect</button><button data-action="agent-revoke" data-agent="${esc(a.name)}">Revoke key</button></div>`).join(''):`<div class="empty-state">No agents yet. Agents are added in the Plovane CLI:<br><code>plovane agent add claude --access read-write</code></div><button class="library-link" data-action="agent-new">›_ Add an agent in the CLI</button>`}<button class="library-link" data-action="connections">⚙ AI providers and MCP setup</button>`}
  /**
   * Input: none
   * Output: Promise<void>
   * Processing: Load which AI providers have keys and fill the provider picker.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  async function loadProviders(){try{providerList=(await request('providers')).providers||[];const sel=$('#provider-select');if(sel){const keep=sel.value||localStorage.getItem('plovane-provider')||'anthropic';sel.innerHTML=providerList.map(p=>`<option value="${esc(p.provider)}">${esc(p.label)}${p.configured?'':' (no key)'}</option>`).join('');sel.value=providerList.some(p=>p.provider===keep)?keep:'anthropic'}}catch(_){}}
  /**
   * Input: none
   * Output: Promise<void>
   * Processing: The connections dialog: provider keys, MCP setup for Claude Code / Desktop, and API use.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  async function showConnections(){await loadProviders();const project=state.data?.project||'<project folder>';openModal('AI providers and connections',`<div class="settings-section"><b>API keys</b><small>Used for plain-English prompts and AI agent runs. Saved on this machine in ~/.plovane/keys.json, readable only by you.</small>${providerList.map(p=>`<div class="provider-row"><span><b>${esc(p.label)}</b> <span class="muted">${esc(p.model)}</span><br><small class="${p.configured?'ok-text':'muted'}">${p.configured?'Connected':'No key'}</small></span><input type="password" id="key-${esc(p.provider)}" placeholder="${p.configured?'Replace key':'Paste API key'}" autocomplete="off"><button data-action="save-key" data-provider="${esc(p.provider)}">Save</button></div>`).join('')}</div><div class="settings-section"><b>Connect Claude Code, Claude Desktop or Cursor (MCP)</b><small>The AI acts as an agent: it only touches what you grant it in the Agents tab, and every change shows up here live.</small><pre>claude mcp add plovane -- plovane mcp --dir ${esc(project)} --agent claude</pre><small>Claude Desktop / Cursor config:</small><pre>${esc(JSON.stringify({mcpServers:{plovane:{command:'plovane',args:['mcp','--dir',project,'--agent','claude']}}},null,2))}</pre></div><div class="settings-section"><b>From the terminal</b><pre>plovane agent run "add a payments service" --provider deepseek\nplovane wb "/block add service payments"</pre><b>Your own code (HTTP API)</b><small>Get a key with Connect on an agent, then send it as a Bearer token:</small><pre>curl -X POST http://127.0.0.1:8700/api/v1/whiteboard/command \\\n  -H "Authorization: Bearer plv_…" \\\n  -d '{"command": "/block add service payments"}'</pre></div>`,'CONNECTIONS')}
  /**
   * Input: none
   * Output: Promise<void>
   * Processing: The AI run dialog: pick Claude, DeepSeek or OpenAI, the access, and the task.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  async function showRunDialog(){await loadProviders();const chosen=$('#provider-select')?.value||'anthropic',task=$('#prompt-input').value.trim();openModal('Let an AI build on the whiteboard',`<label class="settings-control">Provider<select id="run-provider">${providerList.map(p=>`<option value="${esc(p.provider)}" ${p.provider===chosen?'selected':''}>${esc(p.label)} · ${esc(p.model)}${p.configured?'':' (no key — add one first)'}</option>`).join('')}</select></label>${agentNames().length?`<label class="settings-control">Agent<select id="run-agent">${agentNames().map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('')}</select></label>`:'<div class="row">No agents yet. Add one in the Plovane CLI first, e.g. <code>plovane agent add claude</code>, then come back.</div>'}<label class="settings-control">Access<select id="run-access"><option value="read-write">Read and write the whole project</option><option value="read">Read only</option><option value="existing">Keep the agent's current access</option></select></label><label class="settings-control">Task<textarea id="run-task" rows="4" placeholder="Add a payments service with a POST /charge endpoint and connect it to orders">${esc(task)}</textarea></label><div class="modal-actions"><button class="secondary" data-action="connections">API keys…</button><button class="secondary" data-action="close-modal">Cancel</button>${agentNames().length?'<button data-action="ai-run-start">Start</button>':'<button data-action="agent-new">Open the CLI</button>'}</div>`,'AI AGENT')}
  /**
   * Input: none
   * Output: Promise<void>
   * Processing: Start the AI run from the dialog and switch to the activity feed.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  async function startRun(){const task=$('#run-task').value.trim();if(!task){toast('Describe the task first.',true);return}const provider=$('#run-provider').value,agent=$('#run-agent')?.value;if(!agent){toast('Add an agent in the Plovane CLI first.',true);return}try{const run=await request('agent/run',{method:'POST',body:JSON.stringify({prompt:task,provider,agent,access:$('#run-access').value})});runs[run.id]={id:run.id,agent:run.agent,provider:run.provider,status:'running',steps:0,prompt:run.prompt};$('#modal').close();$('#prompt-input').value='';setTab('activity');toast(`${run.agent} is working with ${providerLabel(run.provider)}.`)}catch(e){toast(e.message,true)}}
  /**
   * Input: agent (string)
   * Output: void
   * Processing: The access dialog for one agent: folder path and mode.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function showGrant(agent){const a=(state.data.board.agents||{})[agent]||{};openModal(`Access for ${agent}`,`<p class="muted">Current: ${esc(grantsText(a))}. The most specific folder wins, so "none" on a subfolder blocks it.</p><label class="settings-control">Folder<input id="grant-path" value="." autocomplete="off"></label><label class="settings-control">Access<select id="grant-mode"><option value="read-write">Read and write</option><option value="read">Read only</option><option value="none">No access</option></select></label><div class="modal-actions"><button class="secondary" data-action="close-modal">Cancel</button><button data-action="agent-grant-apply" data-agent="${esc(agent)}">Apply</button></div>`,'AGENT ACCESS')}
  /**
   * Input: value (string)
   * Output: string
   * Processing: Quote a slash-command argument that has spaces or quotes.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function quoteArg(value){return /^[\w./:-]+$/.test(value)?value:`"${value.replaceAll('"','\\"')}"`}
  /**
   * Input: agent (string)
   * Output: Promise<void>
   * Processing: Issue an API key for an agent and show how to connect an AI with it.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  async function connectAgent(agent){try{const data=await request(`agents/${encodeURIComponent(agent)}/token`,{method:'POST',body:'{}'});const project=state.data?.project||'.';openModal(`Connect ${agent}`,`<p>This key is shown once. Anything using it acts as <b>${esc(agent)}</b>, limited to: ${esc(grantsText({grants:data.grants}))}.</p><pre class="token-box">${esc(data.token)}</pre><button data-action="copy-token" data-token="${esc(data.token)}">Copy key</button><div class="row"><b>MCP (Claude Code, Claude Desktop, Cursor)</b> — no key needed, the CLI makes one:<pre>claude mcp add plovane -- plovane mcp --dir ${esc(project)} --agent ${esc(agent)}</pre></div><div class="row"><b>HTTP API</b><pre>curl -X POST ${esc(API)}/command \\\n  -H "Authorization: Bearer ${esc(data.token)}" \\\n  -d '{"command": "/block add service payments"}'</pre></div>`,'AGENT KEY');render()}catch(e){toast(e.message,true)}}
  /**
   * Input: id (string)
   * Output: Promise<void>
   * Processing: Show a block's real source code from the project.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  async function showCode(id){const b=state.data.board.blocks[id];if(!b)return;const target=b.path||b.meta?.file||id;try{const data=await request('command',{method:'POST',body:JSON.stringify({command:`/open ${quoteArg(target)}`})});const r=data.result||{};openModal(b.name||target,r.content!==undefined?`<div class="row muted">${esc(r.path)}${r.line?` · line ${r.line}`:''}</div><pre class="code-view">${esc(r.content)}</pre>`:`<div class="row">${esc(r.path)}</div><pre>${esc((r.files||[]).join('\n'))}</pre>`,'SOURCE')}catch(e){toast(e.message,true)}}
  /**
   * Input: none
   * Output: Promise<void>
   * Processing: Show the project's real Git status.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  async function showGit(){try{const data=await request('command',{method:'POST',body:JSON.stringify({command:'/git status'})});openModal('Git',`<p>Git is the source of truth for the code; the whiteboard maps onto it.</p><pre>${esc(JSON.stringify(data.result||{},null,2))}</pre><div class="modal-actions"><button class="secondary" data-action="close-modal">Close</button><button data-command="/git sync">Sync (pull and push)</button></div>`,'PROJECT')}catch(e){toast(e.message,true)}}
  /**
   * Input: none
   * Output: void
   * Processing: Pick project files to mention in the prompt as context.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function showAttach(){const files=Object.values(state.data?.board?.blocks||{}).filter(b=>b.kind==='file'&&b.path);openModal('Add context',files.length?`<p class="muted">Pick files to mention in your prompt.</p>${files.map(f=>`<button class="attach-row" data-action="attach-path" data-path="${esc(f.path)}">▧ ${esc(f.path)}</button>`).join('')}`:'<p>No files on the board yet. Import the project first (File › Import project).</p>','PROMPT CONTEXT')}
  /**
   * Input: none
   * Output: string[]
   * Processing: Names of the agents on the board (added in the Plovane CLI).
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function agentNames(){return Object.keys(state.data?.board?.agents||{}).sort()}
  /**
   * Input: text (string)
   * Output: void
   * Processing: Open the Plovane CLI panel and prefill its input, closing any dialog.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function openCli(text){if($('#modal').open)$('#modal').close();if(!layout.bottomOpen){layout.bottomOpen=true;applyLayout()}const panel=$('#cli-panel'),frame=$('#cli-panel iframe');if(!panel.classList.contains('open'))menuClick('toggle-cli');const send=()=>frame.contentWindow?.postMessage({type:'plovane-cli-focus',text},location.origin);if(frame.contentDocument?.readyState==='complete')send();else frame.addEventListener('load',send,{once:true});setTimeout(send,400)}
  /**
   * Input: node (HTMLElement)
   * Output: boolean
   * Processing: Handle the agent, AI and connection buttons; true when handled.
   * Changed by: Claude Code
   * Date: 2026-11-09
   */
  function extraAction(node){const a=node.dataset.action,d=node.dataset;if(!a)return false;
    if(a==='ai-run'){showRunDialog();return true}
    if(a==='ai-run-start'){startRun();return true}
    if(a==='connections'){showConnections();return true}
    if(a==='account'){showConnections();return true}
    if(a==='stop-run'){request(`agent/runs/${d.run}/stop`,{method:'POST',body:'{}'}).then(()=>toast('Stopping after the current step.')).catch(e=>toast(e.message,true));return true}
    if(a==='agent-new'){openCli('/agent add ');toast('Agents are added in the Plovane CLI: type a name and press Enter.');return true}
    if(a==='agent-grant'){showGrant(d.agent);return true}
    if(a==='agent-grant-apply'){const path=$('#grant-path').value.trim()||'.',mode=$('#grant-mode').value;$('#modal').close();runCommand(`/agent grant ${quoteArg(d.agent)} ${quoteArg(path)} --mode ${mode}`,true);return true}
    if(a==='agent-connect'){connectAgent(d.agent);return true}
    if(a==='agent-revoke'){if(confirm(`Revoke ${d.agent}'s API key? Anything using it stops working.`))request(`agents/${encodeURIComponent(d.agent)}/token`,{method:'DELETE'}).then(()=>toast(`${d.agent}'s key revoked.`)).catch(e=>toast(e.message,true));return true}
    if(a==='copy-token'){navigator.clipboard?.writeText(d.token).then(()=>toast('Key copied.'),()=>toast('Copy failed; select the key and copy it.',true));return true}
    if(a==='save-key'){const input=$(`#key-${d.provider}`),key=input?.value.trim();if(!key){toast('Paste a key first.',true);return true}request(`providers/${d.provider}/key`,{method:'POST',body:JSON.stringify({key})}).then(()=>{toast(`${providerLabel(d.provider)} connected.`);showConnections()}).catch(e=>toast(e.message,true));return true}
    if(a==='attach-path'){const input=$('#prompt-input');input.value=`${input.value.trim()} @${d.path}`.trim()+' ';$('#modal').close();input.focus();resizePrompt();return true}
    if(a==='open-code'&&d.block){showCode(d.block);return true}
    if(a==='scan'){const b=state.selected&&state.data?.board?.blocks?.[state.selected];runCommand(`/scan ${quoteArg(b?(b.path||b.id):'.')}`);return true}
    if(a==='find-code'){const q=prompt('What code do you need? (searches the verified library)',$('#prompt-input').value.trim()||'retry a failed request');if(q&&q.trim())runCommand(`/rank ${quoteArg(q.trim())} --k 5`);return true}
    if(a==='zoom-into'){hidePopup();flyInto(d.block||state.selected);return true}
    if(a==='zoom-up'){if(leave())fit(true);return true}
    if(a==='crumb'){nest.stack=nest.stack.slice(0,Number(d.crumb)+1);state.selected=null;render();fit();fadeIn();return true}
    if(a==='generate-blocks'){hidePopup();generateBlocks(actionBlock());return true}
    if(a==='split-functions'){splitFunctions(actionBlock());return true}
    if(a==='edit-code'){hidePopup();editCode(actionBlock());return true}
    if(a==='focus-info'){const top=nest.stack[nest.stack.length-1];if(top?.id)openBlock(top.id);return true}
    if(a==='editor-save'){saveCode();return true}
    if(a==='editor-close'){closeEditor();return true}
    if(a==='service-new'){const name=prompt('Service name','new-service');if(name&&name.trim())runCommand(`/block add service ${quoteArg(name.trim())}`);return true}
    return false}
  $('#provider-select')?.addEventListener('change',e=>localStorage.setItem('plovane-provider',e.target.value));
  /**
   * Input: none
   * Output: void
   * Processing: Hide the floating actions over a selected block.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  function hidePopup(){$('#selection-popup').classList.toggle('show',false)}
  /**
   * Input: none
   * Output: Promise<boolean>
   * Processing: Put the project's real folders and files on an empty board, once per project.
   * Changed by: Claude Code
   * Date: 2026-11-10
   */
  async function autoImport(){const blocks=Object.values(state.data?.board?.blocks||{}),key=`plovane-imported:${state.data?.project||''}`;if(blocks.some(b=>['file','folder'].includes(b.kind))||localStorage.getItem(key))return false;localStorage.setItem(key,'1');try{const data=await request('command',{method:'POST',body:JSON.stringify({command:'/project import'})});toast(`Imported ${data.result?.files_found||0} project files.`);return true}catch(e){toast(e.message,true);return false}}
  const editorArea=$('#code-editor textarea');editorArea.addEventListener('input',syncGutter);editorArea.addEventListener('scroll',syncGutter);editorArea.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='s'){e.preventDefault();saveCode()}else if(e.key==='Escape'){e.preventDefault();closeEditor()}else if(e.key==='Tab'){e.preventDefault();editorArea.setRangeText('    ',editorArea.selectionStart,editorArea.selectionEnd,'end');syncGutter()}e.stopPropagation()});
  window.PlovaneWhiteboard={request,refresh,runCommand,get board(){return state.data},get commands(){return state.commands}};
})();
