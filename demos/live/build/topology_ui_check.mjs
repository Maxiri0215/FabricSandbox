// Offline DOM harness: runs app.js handlers with actual model-generated fixtures
// and mocked fetch responses. Does not test HTTP transport, TCP processes or CSS.
// First run: python3 build/generate_topology_fixtures.py
// Then run: node build/topology_ui_check.mjs (Node.js 18+).
import fs from 'node:fs/promises';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../',import.meta.url));
const fixtures=JSON.parse(await fs.readFile(new URL('./topology_fixtures.json',import.meta.url),'utf8'));
const html=await fs.readFile(root+'/web/index.html','utf8');
const script=await fs.readFile(root+'/web/app.js','utf8');
const elements=new Map(),timers=new Set(),requests=[],errors=[];
class Element{
 constructor(attrs=''){this.value='';this.attrs=attrs;this.handlers={};this.dataset={};this.style={};this._html='';this._text='';this.hidden=/\bhidden\b/.test(attrs);this.disabled=/\bdisabled\b/.test(attrs);this.classes=new Set((attrs.match(/class="([^"]*)"/)?.[1]||'').split(/\s+/));this.classList={toggle:(k,b)=>b?this.classes.add(k):this.classes.delete(k)};for(const a of attrs.matchAll(/data-([a-z]+)="([^"]*)"/g))this.dataset[a[1]]=a[2];}
 set innerHTML(x){this._html=x;this._text='';this.children=[...x.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)].map(m=>{const e=new Element(m[1]);e.innerHTML=m[2];return e;});}
 get innerHTML(){return this._html;}
 set textContent(x){this._text=String(x);this._html='';}
 get textContent(){return this._text||this._html.replace(/<[^>]*>/g,' ').replace(/&[^;]+;/g,' ');}
 addEventListener(k,fn){this.handlers[k]=fn;}
 async click(){if(!this.disabled)return this.handlers.click?.();}
 setAttribute(k,v){this[k]=v;}
 removeAttribute(k){delete this[k];}
 querySelectorAll(q){if(q==='button')return this.children||[];throw Error(q);}
 showModal(){this.open=true;}
 close(){this.open=false;}
}
for(const match of html.matchAll(/<[a-z][a-z0-9]*\b([^>]*\bid="([^"]+)"[^>]*)>/g))elements.set('#'+match[2],new Element(match[1]));
elements.set('#networkRaw pre',new Element());
const nav=[...html.matchAll(/<button\b([^>]*class="nav-item[^>]+)>/g)].map(m=>new Element(m[1]));
const document={body:{dataset:{}},querySelector:s=>{if(!elements.has(s))throw Error('Missing '+s);return elements.get(s);},querySelectorAll:s=>s==='.nav-item'?nav:s==='.mode-panel'?['network','tcp','gpu'].map(n=>{const e=elements.get('#'+n+'Panel');e.id=n+'Panel';return e;}):[],createElement:()=>new Element(),documentElement:{requestFullscreen:async()=>{}},exitFullscreen:async()=>{}};
elements.get('#topologyMode').value='default';elements.get('#uplink').value='50';elements.get('#resourcePreset').value='healthy';elements.get('#faultKind').value='channel_a';
let holdAnalyze=null;
const sandbox={document,console,Blob,URL,fetch:async(path,options)=>{
 const payload=options?.body?JSON.parse(options.body):{},top=payload.topology;
 const name=!top?'default':top.nodes[0].id==='single'?'one':top.nodes[0].id.startsWith('n-')?'large':'three';
 let data;if(path==='/api/config')data={token:'test-token'};
 else if(path==='/api/topology/default')data=fixtures.defaults;
 else if(path==='/api/gpu-evidence')data=fixtures.gpu;
 else if(path==='/api/tcp/status')data={status:'idle',metrics:{},stages:[],events:[],topology:{}};
 else if(path==='/api/topology/validate')data=fixtures.validations[name];
 else if(path==='/api/planning/analyze')data=fixtures[name];
 else if(path==='/api/planning/apply')data=fixtures[payload.run_id+'_applied'];
 else throw Error('unexpected path '+path);
 requests.push([path,200]);if(path==='/api/planning/analyze'&&holdAnalyze)await holdAnalyze;
 return {ok:true,status:200,json:async()=>data};
},setTimeout:(fn,ms)=>{const t=setTimeout(async()=>{timers.delete(t);try{await fn();}catch(e){errors.push(e.message);}},ms);timers.add(t);return t;},clearTimeout:t=>{clearTimeout(t);timers.delete(t);}};
const context=vm.createContext(sandbox);vm.runInContext(script,context,{filename:'app.js'});
const get=id=>elements.get(id);const evaluate=code=>vm.runInContext(code,context);
async function until(fn,timeout=15000){const start=Date.now();while(!fn()){if(Date.now()-start>timeout)throw Error('Timeout');await new Promise(r=>setTimeout(r,50));}}

try{
 await until(()=>get('#gpuIncident').textContent.includes('历史实测'));
 await get('#analyzeBtn').click();assert.equal(evaluate('plan.selected_variant'),'compact');
 await get('#applyBtn').click();assert(get('#networkVerification').textContent.includes('SHA256 一致'));
 get('#topologyMode').value='custom';get('#topologyMode').handlers.change();assert.equal(get('#uplink').disabled,true);assert.equal(get('#resourcePreset').disabled,true);assert.equal(get('#applyBtn').disabled,true);
 await get('#exampleTopologyBtn').click();await get('#validateTopologyBtn').click();assert(get('#topologyValidation').textContent.includes('3 机架 / 6 节点 / 12 GPU'));
 assert(get('#networkTopology').innerHTML.includes('3个机架、6个节点、12张GPU'));
 const payload=evaluate('input()');assert.equal('uplink_gbps' in payload,false);assert.equal('memory_gib' in payload,false);
 await get('#analyzeBtn').click();assert(evaluate('plan.topology.nodes.some(n=>n.id==="node-c1")'));assert.equal(get('#applyBtn').disabled,false);
 await get('#applyBtn').click();assert(get('#networkVerification').textContent.includes('SHA256 一致'));
 get('#topologyJson').value='{bad';get('#topologyJson').handlers.input();assert.equal(evaluate('plan'),null);assert.equal(get('#applyBtn').disabled,true);const count=requests.length;await get('#analyzeBtn').click();assert.equal(requests.length,count);assert(get('#notice').textContent.includes('格式错误'));
 const one={nodes:[{id:'single',rack_id:'one-rack',gpu_count:8,memory_gib:24}]};get('#topologyJson').value=JSON.stringify(one);get('#topologyJson').handlers.input();await get('#validateTopologyBtn').click();assert(get('#networkTopology').innerHTML.includes('1个机架、1个节点、8张GPU'));await get('#analyzeBtn').click();assert(evaluate('plan.selected_candidate!==null'));
 const large={nodes:Array.from({length:16},(_,i)=>({id:`n-${i}`,rack_id:`rack-${Math.floor(i/4)}`,gpu_count:4,memory_gib:24}))};get('#topologyFile').files=[{name:'large.json',size:2000,text:async()=>JSON.stringify(large)}];await get('#topologyFile').handlers.change();await get('#validateTopologyBtn').click();assert(get('#networkTopology').innerHTML.includes('4个机架、16个节点、64张GPU'));assert(get('#networkTopology').innerHTML.includes('n-15:gpu3'));
 await get('#exampleTopologyBtn').click();let release;holdAnalyze=new Promise(r=>release=r);const pending=get('#analyzeBtn').click();await until(()=>requests.at(-1)?.[0]==='/api/planning/analyze');await get('#defaultTopologyBtn').click();release();await pending;holdAnalyze=null;assert.equal(evaluate('plan'),null);assert.equal(get('#applyBtn').disabled,true);assert.equal(get('#uplink').disabled,false);assert.equal(get('#topologyEditor').hidden,true);
 await get('#analyzeBtn').click();assert.equal(evaluate('plan.selected_variant'),'compact');
 assert.deepEqual(errors,[]);
 evaluate('plan='+JSON.stringify(fixtures.slow)+';applied=null;showPlan()');assert(get('#networkKpis').textContent.includes('未完成'));assert(!get('#networkKpis').textContent.includes('2 个作业均完成'));assert(!get('#comparisonChart').innerHTML.includes('NaN'));assert(!get('#comparisonChart').innerHTML.includes('bar-fill'));
 const report={status:'passed',method:'app.js event handlers in lightweight DOM with actual model outputs and mocked fetch',generated_at_utc:new Date().toISOString(),fixture_metadata:fixtures.metadata,checks:['existing default analyze/apply','mode preserves default controls','3 rack validate/analyze/apply','custom payload excludes legacy overrides','invalid JSON rejected before network','1 rack graph and simulation','file import 16 nodes 64 GPUs and 4 racks','late analyze response discarded after default switch','return to original entry','incomplete jobs have no false makespan or bars'],mocked_fetch_calls:requests.length,javascript_errors:errors,http_transport:'not exercised',browser_visual_check:'not performed; DOM harness does not render CSS',source_sha256:{app_js:createHash('sha256').update(script).digest('hex'),index_html:createHash('sha256').update(html).digest('hex')}};
 await fs.mkdir(root+'/audit',{recursive:true});await fs.writeFile(root+'/audit/topology_ui_checks.json',JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify(report));
}finally{for(const t of timers)clearTimeout(t);}
