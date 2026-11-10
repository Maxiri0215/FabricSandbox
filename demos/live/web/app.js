'use strict';
const $ = s => document.querySelector(s);
const esc = x => String(x ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = (x, n=1) => Number.isFinite(Number(x)) && x !== null ? Number(x).toLocaleString('zh-CN',{maximumFractionDigits:n}) : '—';
const compact = x => typeof x === 'object' ? JSON.stringify(x) : String(x ?? '—');
const empty = text => `<div class="empty compact"><p>${esc(text)}</p></div>`;
const raw = (x, title='展开原始证据') => `<details class="raw"><summary>${esc(title)}</summary><pre>${esc(JSON.stringify(x,null,2))}</pre></details>`;
const facts = rows => `<div class="fact-grid">${rows.map(r=>`<div class="fact-row"><span>${esc(r.label)}</span><strong>${esc(compact(r.value))}</strong>${r.detail?`<small>${esc(r.detail)}</small>`:''}</div>`).join('')}</div>`;
const metric = (label,value,unit,note) => `<div class="metric"><span>${esc(label)}</span><strong>${esc(value)} <small>${esc(unit||'')}</small></strong><p>${esc(note||'')}</p></div>`;
const tag = (text,kind='') => `<span class="tag ${kind}">${esc(text)}</span>`;
let token='', plan=null, applied=null, gpu=null, gpuStep=0, tcp=null, tcpStep=0, tcpBusy=false, polling=false, noticeTimer, pollTimer, tcpEpoch=0, tcpStageKey='', tcpStepsKey='';

function notice(message, error=false){
  clearTimeout(noticeTimer); const el=$('#notice'); el.hidden=false; el.textContent=message;
  el.style.borderColor=error?'#e49b9b':''; noticeTimer=setTimeout(()=>el.hidden=true,error?14000:6500);
}
async function api(path,body){
  const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Demo-Token':token},body:JSON.stringify(body)};
  const response=await fetch(path,options); const data=await response.json();
  if(!response.ok) throw new Error(data.error||`请求失败 ${response.status}`); return data;
}
function mode(name){
  document.body.dataset.mode=name;
  document.querySelectorAll('.mode-panel').forEach(el=>el.hidden=el.id!==`${name}Panel`);
  document.querySelectorAll('.nav-item').forEach(el=>{const yes=el.dataset.mode===name;el.classList.toggle('active',yes);if(yes)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
  const copy={network:['网络方案评估','同一份负载，比较 GPU 放置如何改变共享链路竞争。','模型计算 · SYNTHETIC'],tcp:['真实通信故障','实际终止主通道，观察规则 Agent 用证据完成恢复。','本机真实 TCP · LIVE'],gpu:['3090 实测证据','沿同一次 DDP 恢复事件，逐项核对服务器导出的证据。','历史实测 · RECORDED']}[name];
  ['#pageTitle','#pageSubtitle','#modeBadge'].forEach((id,i)=>$(id).textContent=copy[i]);
}
document.querySelectorAll('.nav-item').forEach(b=>b.addEventListener('click',()=>mode(b.dataset.mode)));

const defaultPlacement={A:['n0:gpu0','n1:gpu0','n2:gpu0','n3:gpu0'],B:['n0:gpu1','n1:gpu1','n2:gpu1','n3:gpu1']};
let defaultTopology={nodes:[0,1,2,3].map(i=>({id:`n${i}`,rack_id:`r${Math.floor(i/2)}`,gpu_count:2,memory_gib:24})),node_bandwidth_gbps:100,uplink_bandwidth_gbps:50,local_bandwidth_gbps:200,link_latency_us:5,local_latency_us:2};
let networkEpoch=0, networkBusy=false, topologyValidating=false, validatedTopology=null;
const isCustomTopology=()=>$('#topologyMode').value==='custom';
function parsedTopology(){
  let data;try{data=JSON.parse($('#topologyJson').value);}catch(e){throw new Error('拓扑 JSON 格式错误：'+e.message);}
  if(!data||typeof data!=='object'||Array.isArray(data))throw new Error('拓扑 JSON 必须为对象。');
  if(Object.hasOwn(data,'topology')){
    if(Object.keys(data).length!==1)throw new Error('包装对象仅保留 topology 字段；也可直接粘贴拓扑对象。');
    data=data.topology;
  }
  if(!data||typeof data!=='object'||Array.isArray(data))throw new Error('topology 必须为对象。');
  return data;
}
function input(){
  if(isCustomTopology())return {topology:parsedTopology(),steps:30,blocked_nodes:[]};
  const preset=$('#resourcePreset').value;
  return {uplink_gbps:Number($('#uplink').value),memory_gib:preset==='memory'?1:24,blocked_nodes:preset==='node'?['n0']:[],steps:30};
}
function previewTopology(){
  if(isCustomTopology())return validatedTopology;
  const p=input();return {...defaultTopology,uplink_bandwidth_gbps:p.uplink_gbps,nodes:defaultTopology.nodes.map(n=>({...n,memory_gib:p.memory_gib}))};
}
function networkSVG(placement=defaultPlacement,topology=previewTopology(),blocked=[]){
  if(!topology)return empty('先校验自定义 JSON，或点击分析当前方案，查看对应拓扑。');
  const owners={}, gpuNodes=new Map(), racks=new Map();
  Object.entries(placement).forEach(([job,ids])=>ids.forEach(id=>owners[id]=job));
  topology.nodes.forEach(node=>{if(!racks.has(node.rack_id))racks.set(node.rack_id,[]);racks.get(node.rack_id).push(node);for(let g=0;g<node.gpu_count;g++)gpuNodes.set(`${node.id}:gpu${g}`,node);});
  const cross=Object.values(placement).some(ids=>new Set(ids.map(id=>gpuNodes.get(id)?.rack_id).filter(Boolean)).size>1);
  const rackList=[...racks],cols=Math.min(3,rackList.length),width=Math.max(760,cols*370+20), rackWidth=350;
  const nodeHeight=n=>65+Math.ceil(n.gpu_count/2)*32;
  const rackHeight=nodes=>55+Array.from({length:Math.ceil(nodes.length/2)},(_,i)=>Math.max(...nodes.slice(i*2,i*2+2).map(nodeHeight))+12).reduce((a,b)=>a+b,0);
  const rows=Array.from({length:Math.ceil(rackList.length/cols)},(_,i)=>rackList.slice(i*cols,i*cols+cols));
  const height=90+rows.reduce((h,row)=>h+Math.max(...row.map(([,nodes])=>rackHeight(nodes)))+40,0)+35;
  const fit=(text,length=18)=>String(text).length>length?String(text).slice(0,length-1)+'…':String(text);
  const gpuCount=topology.nodes.reduce((n,x)=>n+x.gpu_count,0);
  let svg=`<svg class="network-diagram" style="min-width:${width}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="${racks.size}个机架、${topology.nodes.length}个节点、${gpuCount}张GPU的逻辑资源拓扑"><rect width="${width}" height="${height}" fill="#f7f9fc" rx="14"/><rect x="${width/2-175}" y="12" width="350" height="45" rx="10" fill="#e7effa"/><text x="${width/2}" y="32" text-anchor="middle" fill="#275783" font-size="13">机架上联各 ${num(topology.uplink_bandwidth_gbps)} Gbps / 方向</text><text x="${width/2}" y="49" text-anchor="middle" fill="#61758a" font-size="11">${racks.size} 机架 · ${topology.nodes.length} 节点 · ${gpuCount} GPU</text>`;
  const busEnd=72+rows.slice(0,-1).reduce((h,row)=>h+Math.max(...row.map(([,nodes])=>rackHeight(nodes)))+40,0);
  svg+=`<path d="M${width/2} 57 V65 H8 V${busEnd}" stroke="${cross?'#2674cc':'#bbc6d4'}" stroke-width="3" fill="none"/>`;
  let rowY=90;
  rows.forEach(row=>{
    const totalWidth=row.length*rackWidth+(row.length-1)*20,left=(width-totalWidth)/2,rowHeight=Math.max(...row.map(([,nodes])=>rackHeight(nodes)));
    svg+=`<path d="M8 ${rowY-18} H${left+(row.length-1)*370+rackWidth/2}" stroke="${cross?'#2674cc':'#bbc6d4'}" stroke-width="3" fill="none"/>`;
    row.forEach(([rackId,nodes],r)=>{
      const x=left+r*370,rh=rackHeight(nodes);
      svg+=`<path d="M${x+rackWidth/2} ${rowY-18} V${rowY}" stroke="${cross?'#2674cc':'#bbc6d4'}" stroke-width="3"/><rect x="${x}" y="${rowY}" width="${rackWidth}" height="${rh}" rx="12" fill="#fff" stroke="#d5deea"/><text x="${x+16}" y="${rowY+27}" font-size="14" font-weight="700" fill="#223950"><title>${esc(rackId)}</title>机架 ${esc(fit(rackId,28))}</text>`;
      let nodeY=rowY+42;
      for(let i=0;i<nodes.length;i+=2){
        const pair=nodes.slice(i,i+2),pairHeight=Math.max(...pair.map(nodeHeight));
        pair.forEach((node,n)=>{
          const nx=x+12+n*164,nh=nodeHeight(node),disabled=blocked.includes(node.id);
          svg+=`<rect x="${nx}" y="${nodeY}" width="158" height="${nh}" rx="8" fill="${disabled?'#fff1f1':'#f3f6fb'}" stroke="${disabled?'#d65252':'#e0e7ef'}"/><text x="${nx+9}" y="${nodeY+19}" fill="#35475e" font-size="12"><title>${esc(node.id)}</title>${esc(fit(node.id,18))}${disabled?' ×':''}</text><text x="${nx+9}" y="${nodeY+38}" fill="#697d91" font-size="10">${num(topology.node_bandwidth_gbps)} Gbps · ${num(node.memory_gib)} GiB / 卡</text>`;
          for(let g=0;g<node.gpu_count;g++){
            const id=`${node.id}:gpu${g}`,job=owners[id],gx=nx+8+(g%2)*72,gy=nodeY+49+Math.floor(g/2)*32,color=disabled?'#a6afbc':job==='A'?'#2674cc':job==='B'?'#d88a2b':'#a6afbc';
            svg+=`<rect x="${gx}" y="${gy}" width="69" height="26" rx="5" fill="${color}"/><text x="${gx+34.5}" y="${gy+17}" text-anchor="middle" fill="white" font-size="10"><title>${esc(id)} · ${disabled?'不可用':esc(job||'空闲')}</title>GPU ${g} · ${disabled?'×':esc(job||'空')}</text>`;
          }
        });
        nodeY+=pairHeight+12;
      }
    });
    rowY+=rowHeight+40;
  });
  svg+=`<text x="${width/2}" y="${height-18}" text-anchor="middle" font-size="12" fill="#66788d">${cross?'存在跨机架通信，使用共享上联容量':'当前放置无跨机架通信'} · 逻辑资源模型</text></svg>`;
  return svg;
}
function updateNetworkControls(){
  const custom=isCustomTopology();$('#topologyEditor').hidden=!custom;
  $('#uplink').disabled=custom||networkBusy;$('#resourcePreset').disabled=custom||networkBusy;
  $('#analyzeBtn').disabled=networkBusy;$('#applyBtn').disabled=networkBusy||!plan?.selected_candidate||!!applied;
  $('#validateTopologyBtn').disabled=topologyValidating;
}
function renderNetworkInput(){
  const topology=previewTopology(),custom=isCustomTopology(),count=topology?.nodes.reduce((a,n)=>a+n.gpu_count,0),rackCount=topology?new Set(topology.nodes.map(n=>n.rack_id)).size:0;
  $('#networkStatus').textContent=custom?'自定义拓扑：带宽与显存以 JSON 为准。分析时会校验并冻结本次输入。':'输入已就绪。点击分析，校验候选并运行原项目仿真。';
  $('#placementLabel').textContent=custom?'自定义拓扑 · 待分析':'初始放置';
  $('#networkTopology').innerHTML=networkSVG(custom?{}:defaultPlacement,topology,custom?[]:input().blocked_nodes);
  $('#networkKpis').innerHTML=metric('资源规模',count??'—','GPU',topology?`${rackCount} 机架 / ${topology.nodes.length} 节点`:'校验 JSON 后显示')+metric('并行负载','2','DDP 作业','每组 4 卡，30 steps')+metric('机架上联',topology?num(topology.uplink_bandwidth_gbps):'—','Gbps','每个方向独立容量')+metric('结果来源','模型','SYNTHETIC','尚未运行本次评估');
  $('#networkDiagnosis').innerHTML=empty('分析将指出共享资源和约束依据。');$('#candidateList').innerHTML=empty('等待候选校验。');$('#comparisonChart').innerHTML=empty('等待相同负载的模型对照。');$('#networkVerification').innerHTML='';
  try{$('#networkRaw pre').textContent=JSON.stringify(input(),null,2);}catch(e){$('#networkRaw pre').textContent=$('#topologyJson').value||'尚未输入拓扑 JSON。';}
  updateNetworkControls();
}
function resetNetwork(clearValidation=true){
  networkEpoch++;plan=null;applied=null;
  if(clearValidation){validatedTopology=null;$('#topologyValidation').textContent='输入已更改，请校验拓扑或直接分析。';$('#topologyValidation').className='topology-validation';}
  renderNetworkInput();
}
['#uplink','#resourcePreset','#topologyMode'].forEach(id=>$(id).addEventListener('change',()=>{if(isCustomTopology()&&!$('#topologyJson').value.trim())$('#topologyJson').value=JSON.stringify(defaultTopology,null,2);resetNetwork();}));
$('#topologyJson').addEventListener('input',()=>resetNetwork());
$('#exampleTopologyBtn').addEventListener('click',()=>{
  const topology={nodes:['a','b','c'].flatMap(r=>[0,1].map(i=>({id:`node-${r}${i}`,rack_id:`rack-${r}`,gpu_count:2,memory_gib:24}))),node_bandwidth_gbps:100,uplink_bandwidth_gbps:50,local_bandwidth_gbps:200,link_latency_us:5,local_latency_us:2};
  $('#topologyJson').value=JSON.stringify(topology,null,2);resetNetwork();
});
$('#defaultTopologyBtn').addEventListener('click',()=>{$('#topologyMode').value='default';resetNetwork();});
$('#topologyFile').addEventListener('change',async()=>{
  const file=$('#topologyFile').files?.[0];if(!file)return;
  resetNetwork();const epoch=networkEpoch;
  try{if(file.size>65536)throw new Error('JSON 文件不能超过 64 KiB。');const text=await file.text();if(epoch!==networkEpoch)return;$('#topologyJson').value=text;resetNetwork();$('#topologyValidation').textContent=`已载入 ${file.name}，请校验或分析。`;}
  catch(e){if(epoch===networkEpoch){$('#topologyValidation').textContent=e.message;$('#topologyValidation').className='topology-validation error';notice(e.message,true);}}
  finally{$('#topologyFile').value='';}
});
$('#validateTopologyBtn').addEventListener('click',async()=>{
  resetNetwork();const epoch=networkEpoch;topologyValidating=true;updateNetworkControls();$('#topologyValidation').textContent='正在校验拓扑……';
  try{const result=await api('/api/topology/validate',{topology:parsedTopology()});if(epoch!==networkEpoch)return;validatedTopology=result.topology;const s=result.summary;$('#topologyValidation').textContent=`校验通过：${s.racks} 机架 / ${s.nodes} 节点 / ${s.gpus} GPU。${s.gpus<8?'双作业方案评估仍需至少 8 张 GPU。':'可点击分析当前方案。'}`;$('#topologyValidation').className='topology-validation ok';renderNetworkInput();}
  catch(e){if(epoch===networkEpoch){$('#topologyValidation').textContent=e.message;$('#topologyValidation').className='topology-validation error';notice(e.message,true);}}
  finally{topologyValidating=false;updateNetworkControls();}
});
function candidatesHTML(candidates,selected){
  return candidates.map(c=>{const ok=c.admissible??c.feasible??c.accepted;return `<article class="candidate ${ok?'':'rejected'} ${c.id===selected?'selected':''}"><div class="candidate-heading"><strong>${esc(c.label||c.id||c.candidate?.candidate_id||'候选')}</strong>${tag(c.id===selected?'推荐':ok?(c.metrics?.all_jobs_completed?'可行':'时域内未完成'):'拒绝',ok?(c.metrics?.all_jobs_completed?'ok':'warn'):'bad')}</div><p>${esc(ok?`${c.metrics?.all_jobs_completed?`仿真完成时间 ${num(c.metrics.makespan_ms)} ms`:'评估时域内尚未完成全部作业'}；移动 ${c.moved_gpu_assignments??'—'} 个 GPU 分配。`:(c.reasons||c.rejection_reasons||[]).join('；'))}</p>${c.placements?`<small>A：${esc((c.placements.A||[]).join(' / '))}<br>B：${esc((c.placements.B||[]).join(' / '))}</small>`:''}</article>`;}).join('');
}
function showPlan(){
  const d=plan.diagnosis, selected=plan.selected_candidate, distributed=plan.comparison?.distributed;
  const initial=distributed?.metrics, after=selected?.metrics, successful=applied?.evaluation?.metrics;
  $('#networkKpis').innerHTML=metric('当前分散方案',initial?.all_jobs_completed?num(initial.makespan_ms):'未完成',initial?.all_jobs_completed?'ms':'',initial?.all_jobs_completed?'2 个作业均完成':initial?'评估时域内未完成全部作业':'当前资源不满足完整作业组')+metric(applied?'采用方案重算':'推荐方案预测',num((successful||after)?.makespan_ms),'ms',applied?'重新执行原仿真内核':'有限候选中最短仿真完成时间')+metric('可行候选',String(plan.candidates.filter(c=>c.admissible).length),`/ ${plan.candidates.length}`,'先校验显存、健康与完整通信组')+metric('同负载时间比',initial?.all_jobs_completed&&after?.all_jobs_completed?num(initial.makespan_ms/after.makespan_ms,2):'—','×','原方案时间 / 推荐方案时间');
  $('#networkTopology').innerHTML=networkSVG(applied?applied.candidate.placements:plan.placements,plan.topology,plan.request?.blocked_nodes||[]);
  $('#placementLabel').textContent=applied?'已采用 · 仿真重算':'原始分散方案';
  $('#networkDiagnosis').innerHTML=`${tag(plan.status==='blocked'?(plan.candidates.some(c=>c.admissible)?'时域内未完成':'约束不满足'):d.code==='shared_uplink_contention'?'共享容量竞争':'方案评估',plan.status==='blocked'?'bad':d.code==='shared_uplink_contention'?'warn':'ok')}<h3>${esc(d.explanation||d.code)}</h3><p>校验健康资源、每卡显存与完整通信组；对通过的候选运行原仿真。按完成时间、移动分配数和候选 ID 依次排序。</p>${facts([{label:'推荐方案',value:selected?.label||'无可行方案'},{label:'输入版本指纹',value:plan.input_hash?.slice(0,16)},{label:'数据来源',value:d.source||'原仿真内核'}])}${raw(d.evidence,'查看诊断使用的资源样本')}`;
  const feasible=plan.candidates.filter(c=>c.admissible), rejected=plan.candidates.filter(c=>!c.admissible);
  $('#candidateList').innerHTML=candidatesHTML(feasible,plan.selected_candidate_id)+(rejected.length?`<details class="rejections"><summary>${rejected.length} 个候选被拒绝 · 点击查看约束依据</summary>${candidatesHTML(rejected,null)}</details>`:'');
  const series=[['distributed','原方案 · A+B 分散','#2674cc'],['compact','候选 · A+B 各自紧凑','#0d9488'],['compact_reverse','候选 · 反向紧凑放置','#6ca29c']].filter(([k])=>plan.comparison?.[k]);
  const max=Math.max(...series.filter(([k])=>plan.comparison[k].metrics.all_jobs_completed).map(([k])=>plan.comparison[k].metrics.makespan_ms),1);
  $('#comparisonChart').innerHTML=series.length?series.map(([key,label,color])=>{const m=plan.comparison[key].metrics,done=m.all_jobs_completed;return `<div class="comparison-row"><div><strong>${esc(label)}</strong><span>${done?num(m.makespan_ms)+' ms':'时域内未完成'}</span></div><div class="bar-track">${done?`<div class="bar-fill" style="width:${m.makespan_ms/max*100}%;background:${color}"></div>`:''}</div></div>`;}).join('')+`<p class="section-note">固定拓扑、负载、profile 与评估时长，仅改变 GPU 放置。A 单独运行：${plan.comparison.baseline?.metrics.all_jobs_completed?num(plan.comparison.baseline.metrics.makespan_ms)+' ms':'时域内未完成'}，仅作竞争对照，不参与双作业选优。</p>`:empty('无可行双作业方案。系统返回等待 / 不可行，不生成恢复成功或加速结论。');
  $('#networkVerification').innerHTML=applied?`${tag('仿真重算完成','ok')}${facts([{label:'完整作业完成',value:`${successful.completed_jobs} / 2`},{label:'容量最大使用比例',value:num(successful.max_capacity_ratio,3)},{label:'候选与执行输入一致',value:applied.evaluation.input_hash===applied.candidate.input_hash?'是（SHA256 一致）':'否'},{label:'同负载对照',value:plan.comparison_contract?.same_workload_hash?'一致':'见原始记录'}])}<p>采用方案会重新运行冻结输入的仿真；这一步不在真实 GPU 上迁移正在运行的作业。</p>`:'';
  $('#networkRaw pre').textContent=JSON.stringify(applied?{analysis:plan,execution:applied}:plan,null,2);
  $('#networkStatus').textContent=applied?'已采用推荐放置并完成独立重算。':plan.status==='blocked'?(feasible.length?'分析结束：存在通过约束的候选，但评估时域内未完成全部作业，暂不推荐。':'分析结束：没有满足约束的完整方案。'):'分析结束：候选已校验，点击采用推荐方案验证结果。';
  $('#applyBtn').disabled=!selected||!!applied;
}
$('#analyzeBtn').addEventListener('click',async()=>{
  let payload;try{payload=input();}catch(e){notice(e.message,true);$('#networkStatus').textContent=e.message;return;}
  const epoch=++networkEpoch;networkBusy=true;plan=null;applied=null;updateNetworkControls();
  $('#networkStatus').textContent='正在校验有限候选，并运行原项目仿真……';
  try{const result=await api('/api/planning/analyze',payload);if(epoch!==networkEpoch)return;plan=result;applied=null;if(isCustomTopology()){validatedTopology=result.topology;$('#topologyValidation').textContent='拓扑已校验，并冻结于本次分析记录。';$('#topologyValidation').className='topology-validation ok';}showPlan();}
  catch(e){if(epoch===networkEpoch){notice(e.message,true);$('#networkStatus').textContent='本次分析失败：'+e.message;}}
  finally{networkBusy=false;updateNetworkControls();}
});
$('#applyBtn').addEventListener('click',async()=>{
  if(!plan)return;const epoch=networkEpoch,activePlan=plan;networkBusy=true;updateNetworkControls();
  try{const result=await api('/api/planning/apply',{run_id:activePlan.run_id,candidate_id:activePlan.selected_candidate_id});if(epoch!==networkEpoch)return;applied=result;showPlan();notice('已按冻结输入重新计算所选方案。');}
  catch(e){if(epoch===networkEpoch)notice(e.message,true);}
  finally{networkBusy=false;updateNetworkControls();}
});

const shortStages=['输入与初始方案','真实异常','诊断证据','候选及拒绝理由','所选方案','执行过程','新进度与验证'];
function stepButtons(container,stages,index,callback){
  container.innerHTML=shortStages.map((title,i)=>`<button type="button" class="step ${index===i?'active':''} ${esc(stages[i]?.state||'')}" data-step="${i}" aria-pressed="${index===i}"><span>${String(i+1).padStart(2,'0')}</span><strong>${title}</strong><small>${({pending:'等待',active:'执行中',done:'已留证',error:'有异常'})[stages[i]?.state]||'实测记录'}</small></button>`).join('');
  container.querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>callback(Number(b.dataset.step))));
}
const labels={version:'方案版本',active_channel:'当前通道',code:'诊断代码',certainty:'确定性',explanation:'诊断解释',affected:'受影响对象',evidence_ids:'依据记录',current_version:'当前输入版本',recovered:'验证恢复',feasible:'通过约束',rejection_reasons:'拒绝理由',candidate_id:'候选 ID',cost:'代价',reason:'理由',old_channel:'原通道',channel:'目标通道',status:'执行状态',error:'错误',action:'动作',target:'目标',selected_candidate_id:'所选候选',details:'详细证据',evidence:'验证证据',strategy:'策略',placement:'放置',old_version:'原版本'};
function objectFacts(obj){return facts(Object.entries(obj||{}).filter(([k])=>!['events','trace','logs'].includes(k)).slice(0,14).map(([k,v])=>({label:labels[k]||k,value:compact(v).length>700?compact(v).slice(0,700)+'…':v})));}
function stageSummary(index,f){
  if(!f)return empty('工具正在执行，等待证据写入。');
  if(index===0)return `<p>客户端经主通道 A 传输；备用 B 已配置。约束和输入版本在故障前记录。</p>${facts([{label:'初始通道',value:f.active_channel},{label:'初始版本',value:f.version},{label:'端点',value:f.endpoints},{label:'执行约束',value:f.constraints}])}`;
  if(index===1){const obs=f.independent_observation||{};return `<p>注入操作实际终止本会话的子进程。诊断使用随后独立采集的探针和传输结果。</p><h3>独立观察</h3>${facts([{label:'观察状态',value:obs.status},{label:'当前通道',value:obs.facts?.active_channel},{label:'接收端 / 通道探测',value:obs.facts?.probes},{label:'实际数据传输',value:obs.facts?.delivery}])}${raw(f.experiment_injection,'核对注入事实（不传给诊断器）')}`;}
  if(index===2)return `<p>${f.code==='channel_unavailable'?'接收端仍可达，但当前通道的端到端探测和数据传输失败，才判定通道不可用。':'诊断以实际探测为依据；证据不足时不宣称已隔离故障或恢复成功。'}</p>${objectFacts(f)}`;
  if(index===3)return f.map(r=>`<article class="candidate ${r.feasible?'':'rejected'}"><div class="candidate-heading"><strong>${esc(r.candidate?.id)}</strong>${tag(r.feasible?'通过校验':'拒绝',r.feasible?'ok':'bad')}</div>${facts([{label:'传输路径',value:(r.candidate?.path||[]).join(' → ')},{label:'切换代价',value:r.candidate?.cost},{label:'校验结论',value:r.feasible?'接收端与候选通道探测通过':(r.rejection_reasons||[]).join('；')}])}</article>`).join('');
  if(index===4){const c=f.candidate||{};return `<p>先排除不可行候选，再最小化通道变更代价。所选候选仍需校验输入版本。</p>${facts([{label:'候选',value:c.id},{label:'目标通道',value:c.target},{label:'代价',value:c.cost},{label:'输入版本',value:c.version},{label:'最终校验',value:f.validation?.passed??f.validation}])}`;}
  if(index===5)return `<p>原执行器写入新活动路径，并推进版本。执行回执只说明操作已提交，恢复仍由下一步的数据验证确认。</p>${objectFacts(f)}`;
  const deliveries=f.details?.deliveries||[];
  return `<h3>${f.recovered?'失败块重试与新数据校验通过':'未证实恢复成功'}</h3>${facts([{label:'验证结论',value:f.recovered?'recovered = true':'recovered = false'},{label:'验证通道 / 版本',value:`${f.details?.channel??'—'} / ${f.details?.version??'—'}`},{label:'已确认唯一数据块',value:f.details?.acknowledged_unique_chunks},{label:'接收端唯一数据块',value:f.details?.receiver?.unique_chunks}])}<div class="table-wrap"><table><thead><tr><th>数据块</th><th>ACK</th><th>长度</th><th>SHA256</th></tr></thead><tbody>${deliveries.map(d=>`<tr><td>${esc(d.chunk_id)}</td><td>${d.ok&&d.response?.ok?'通过':'失败'}</td><td>${num(d.bytes,0)} B</td><td>${esc(d.sha256)}</td></tr>`).join('')}</tbody></table></div><p>上方计数继续增长，说明验证之后仍有新数据传输；探针流量不计入数据块数。</p>`;
}
function tcpSVG(s){
  const t=s?.topology||{}, m=s?.metrics||{}, route=m.current_route, processes=[['primary','主通道 A',225,24],['spare','备用通道 B',225,157],['receiver','接收端',505,90]];
  let svg=`<svg viewBox="0 0 760 285" role="img" aria-label="本机TCP主备路径与实际进程状态"><rect width="760" height="285" rx="14" fill="#f7f9fc"/><path d="M155 132 L225 65 H400 L505 131" fill="none" stroke="${route==='A'?'#2674cc':'#c4cedb'}" stroke-width="4"/><path d="M155 132 L225 198 H400 L505 131" fill="none" stroke="${route==='B'?'#0d9488':'#c4cedb'}" stroke-width="4"/><rect x="22" y="94" width="135" height="78" rx="10" fill="#e6effa" stroke="#aac7e6"/><text x="89" y="123" text-anchor="middle" fill="#284768" font-size="15" font-weight="700">传输客户端</text><text x="89" y="150" text-anchor="middle" fill="#59738b" font-size="12">64 KiB / 数据块</text>`;
  processes.forEach(([key,name,x,y])=>{const p=t[key],exists=!!p?.pid,alive=p?.alive,color=exists?(alive?'#0d9488':'#d05252'):'#95a3b3';svg+=`<rect x="${x}" y="${y}" width="185" height="91" rx="11" fill="white" stroke="${color}" stroke-width="2"/><circle cx="${x+17}" cy="${y+23}" r="4" fill="${color}"/><text x="${x+29}" y="${y+28}" fill="#284055" font-size="14" font-weight="700">${name} · ${exists?(alive?'运行':'已退出'):'待启动'}</text><text x="${x+13}" y="${y+52}" fill="#61758a" font-size="12">PID ${p?.pid||'—'} / 端口 ${p?.port||'—'}</text><text x="${x+13}" y="${y+74}" fill="#61758a" font-size="12">127.0.0.1${exists&&!alive?` · exit ${p.returncode??'—'}`:''}</text>`;});
  return svg+`<text x="380" y="266" text-anchor="middle" font-size="12" fill="#61758a">原 Controller：观察 → 诊断 → 枚举 → 校验 → 应用 → 验证 · 本机应用层 TCP</text></svg>`;
}
function showTcpStage(){
  const keys=JSON.stringify([tcp?.session_id,tcpStep,(tcp?.stages||[]).map(s=>s.state)]);
  if(keys!==tcpStepsKey){tcpStepsKey=keys;stepButtons($('#tcpSteps'),tcp?.stages||[],tcpStep,i=>{tcpStep=i;showTcpStage();});}
  const stage=tcp?.stages?.[tcpStep];
  const key=JSON.stringify([tcp?.session_id,tcpStep,stage]);if(key===tcpStageKey)return;tcpStageKey=key;
  if(!stage||stage.state==='pending'){$('#tcpStageEvidence').innerHTML=`<h2>${shortStages[tcpStep]}</h2>`+empty('该阶段尚未发生。等待真实实验产生证据。');return;}
  const f=stage.facts;
  $('#tcpStageEvidence').innerHTML=`<div class="section-heading"><h2>${tcpStep+1}. ${esc(stage.title||shortStages[tcpStep])}</h2>${tag(stage.state==='error'?'需关注':'当次运行证据',stage.state==='error'?'warn':'ok')}</div>${stageSummary(tcpStep,f)}${raw(f)}`;
}
function showTcp(s){
  tcp=s;const m=s.metrics||{};
  $('#tcpKpis').innerHTML=metric('已获 ACK 数据块',num(m.acknowledged_blocks,0),'块','后台真实传输，非动画计数')+metric('已确认数据量',num((m.acknowledged_bytes||0)/1048576,2),'MiB','64 KiB / 块，SHA256 校验')+metric('当前路径',m.current_route||'—','',`方案版本 ${m.version??'—'}`)+metric('失败尝试',num(m.failed_attempts??m.failed_blocks??0,0),'次','重试可成功，不等于永久丢失');
  $('#tcpTopology').innerHTML=tcpSVG(s);
  const texts={idle:'尚未启动。',running:'真实传输进行中。点击注入故障，实际终止主通道进程。',recovering:'异常已注入。原规则 Agent 正在观察、诊断、选择与验证。',recovered:'已验证恢复，正在通过备用通道持续传输。点击下方七个阶段查看证据。',blocked:'没有可用通道：系统明确返回不可行，不执行虚假修复。',failed:'本次实验未完成恢复，请展开诊断与原始证据。',stopped:'实验已停止，本会话进程已清理；证据仍可查看。'};
  $('#tcpStatus').textContent=(texts[s.status]||s.status)+(s.session_id?`  会话：${s.session_id}`:'');
  $('#startTcp').disabled=tcpBusy;$('#startTcp').textContent=s.session_id?'重新开始实验':'启动真实通信';
  $('#injectTcp').disabled=tcpBusy||s.status!=='running';$('#faultKind').disabled=tcpBusy||s.status==='recovering';
  $('#stopTcp').disabled=tcpBusy||!['running','recovering','recovered','blocked','failed'].includes(s.status)||s.cleanup_complete;
  showTcpStage();
  $('#tcpEventLog').innerHTML=(s.events||[]).slice(-35).reverse().map(e=>`<div class="event"><small>${esc(typeof e.timestamp==='number'?new Date(e.timestamp*1000).toLocaleTimeString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}):e.timestamp||'')} · ${esc(e.kind)}</small><p>${esc(e.message||'工具返回')}</p></div>`).join('')||empty('等待真实事件。');
}
function schedulePoll(delay){clearTimeout(pollTimer);pollTimer=setTimeout(pollTcp,delay);}
async function pollTcp(){
  if(polling){schedulePoll(650);return;}polling=true;const epoch=tcpEpoch;
  try{const s=await api('/api/tcp/status');if(epoch===tcpEpoch&&!tcpBusy)showTcp(s);}catch(e){notice('读取实验状态失败：'+e.message,true);}
  finally{polling=false;if(epoch===tcpEpoch&&tcp?.session_id&&!tcp.cleanup_complete)schedulePoll(document.body.dataset.mode==='tcp'?650:1500);}
}
async function tcpAction(path,body,step){
  tcpBusy=true;tcpEpoch++;clearTimeout(pollTimer);['#startTcp','#injectTcp','#stopTcp'].forEach(id=>$(id).disabled=true);
  try{const s=await api(path,path==='/api/tcp/start'?body:{...body,session_id:tcp?.session_id});if(step!==undefined)tcpStep=step;showTcp(s);if(path!=='/api/tcp/stop')schedulePoll(250);}catch(e){notice(e.message,true);if(tcp?.session_id&&!tcp.cleanup_complete)schedulePoll(650);}
  finally{tcpBusy=false;if(tcp)showTcp(tcp);else $('#startTcp').disabled=false;}
}
$('#startTcp').addEventListener('click',()=>tcpAction('/api/tcp/start',{},0));
$('#injectTcp').addEventListener('click',()=>tcpAction('/api/tcp/inject',{kind:$('#faultKind').value},1));
$('#stopTcp').addEventListener('click',()=>tcpAction('/api/tcp/stop',{}));

function gpuSVG(){
  const failed=gpuStep>=1,newGroup=gpuStep>=5,selected=gpuStep===4;
  let s=`<svg viewBox="0 0 760 220" role="img" aria-label="GPT2 DDP GPU2和GPU3故障后选择GPU2和GPU4"><rect width="760" height="220" rx="14" fill="#f7f9fc"/><text x="380" y="32" text-anchor="middle" fill="#344c64" font-size="14">${newGroup?'新 DDP 组 · GPU 2 / 4 · execution epoch 1':selected?'已选候选 GPU 2 / 4 · 待执行':'初始 DDP 组 · GPU 2 / 3 · execution epoch 0'}</text>`;
  [2,3,4].forEach((id,i)=>{const x=30+i*250,isBad=id===3&&failed,active=newGroup?id!==3:id!==4,color=isBad?'#d25252':active?'#2674cc':'#96a5b5';let status=id===4?'备用卡':'原训练组';if(isBad)status='worker 已退出 / 策略隔离';else if(newGroup)status=gpuStep===6?'新组已有进度，完成 step20':'新组启动，等待进度验证';else if(selected)status=id===2||id===4?'所选新组，尚未执行':status;else if(failed&&id===2)status='旧组不能独立继续';s+=`<rect x="${x}" y="60" width="200" height="112" rx="12" fill="white" stroke="${color}" stroke-width="2"/><text x="${x+100}" y="95" text-anchor="middle" font-size="19" font-weight="700" fill="${color}">RTX 3090 · GPU ${id}</text><text x="${x+100}" y="124" text-anchor="middle" font-size="11" fill="#52697f">${status}</text><text x="${x+100}" y="149" text-anchor="middle" font-size="11" fill="#718295">24 GiB · 历史实测</text>`;});
  return s+`<text x="380" y="201" text-anchor="middle" font-size="12" fill="#61758a">进程退出不代表 GPU 硬件损坏；DDP 按完整通信组重建</text></svg>`;
}
function showGpu(){
  if(!gpu)return;const stage=gpu.stages[gpuStep];
  $('#gpuIncident').textContent=`事件 ${gpu.incident_id} · ${gpu.captured_at} · 历史实测 / 当前不执行 GPU 任务`;
  stepButtons($('#gpuSteps'),gpu.stages,gpuStep,i=>{gpuStep=i;showGpu();});$('#gpuTopology').innerHTML=gpuSVG();
  $('#gpuStageEvidence').innerHTML=`<span class="eyebrow">${gpuStep+1} / 7 · 同一次服务器恢复事件</span><h2>${esc(stage.title)}</h2><p class="stage-summary">${esc(stage.summary)}</p>${facts(stage.facts||[])}<div class="presenter-note">讲解重点：${esc(stage.presenter_note||'根据当次证据解释系统行为。')}</div><details class="raw"><summary>来源文件与 SHA256</summary>${(stage.evidence||[]).map(e=>`<p><code>${esc(e.path)}</code><br><small>SHA256 ${esc(e.sha256)}</small></p>`).join('')}</details>${raw(stage.raw)}`;
  $('#gpuPrev').disabled=gpuStep===0;$('#gpuNext').disabled=gpuStep===6;$('#gpuCounter').textContent=`${gpuStep+1} / 7`;
}
$('#gpuPrev').addEventListener('click',()=>{gpuStep=Math.max(0,gpuStep-1);showGpu();});
$('#gpuNext').addEventListener('click',()=>{gpuStep=Math.min(6,gpuStep+1);showGpu();});
$('#helpBtn').addEventListener('click',()=>$('#helpDialog').showModal());$('#closeHelp').addEventListener('click',()=>$('#helpDialog').close());
$('#fullscreenBtn').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch(e){notice('可使用浏览器的全屏菜单进入演示。');}});
$('#exportBtn').addEventListener('click',async()=>{try{const data=await api('/api/export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`FabricSandbox_demo_${new Date().toISOString().replace(/[:.]/g,'-')}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notice('本次输入、方案与真实 TCP 事件记录已导出。');}catch(e){notice(e.message,true);}});
resetNetwork();showTcp({status:'idle',metrics:{},stages:[],events:[],topology:{}});
(async()=>{try{const config=await api('/api/config');token=config.token;const inputEpoch=networkEpoch;const defaults=await api('/api/topology/default');defaultTopology=defaults.topology;if(!$('#topologyJson').value.trim())$('#topologyJson').value=JSON.stringify(defaultTopology,null,2);if(inputEpoch===networkEpoch&&!plan)renderNetworkInput();gpu=await api('/api/gpu-evidence');showGpu();const epoch=tcpEpoch;const s=await api('/api/tcp/status');if(epoch===tcpEpoch&&!tcpBusy){showTcp(s);if(s.session_id&&!s.cleanup_complete)pollTcp();}}catch(e){notice('无法连接本地服务，请通过“启动演示.command”打开：'+e.message,true);}})();
