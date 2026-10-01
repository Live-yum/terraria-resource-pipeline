import {cachedObject,storeObject,saveSnapshot,loadSnapshot,boundedBytes} from './cache.js';
const $ = id => document.getElementById(id);
const familyLabels = {items:'物品',tiles:'方块',walls:'墙壁',paints:'油漆与涂层',npcs:'生物与图鉴',buffs:'增益与减益',prefixes:'前缀',player:'人物与装备',worldgen:'世界生成',markers:'地图标记',pixel:'像素查找',map:'地图颜色',locales:'本地化',ids:'ID 常量'};
const families = Object.keys(familyLabels);
const stateLabels = {UPLOADED:'已接收',EXTRACTING:'检查中',READY_FOR_REVIEW:'待审核',BLOCKED:'已阻止',PUBLISHING:'发布中',PUBLISHED:'已发布',INTERRUPTED:'已中断'};
let selected = null, selectedDigest = null, clientRelease = null, clientManifest = null;
let publishing=false, rawSubmitting=false, clientEpoch=0, familyEpoch=0, renderedJob=null;
const cache = new Map();
let cachedBytes = 0;
async function api(path, options={}) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw Error(typeof body.detail === 'string' ? body.detail : '请求失败');
  return body;
}
function status(id, text, error=false) { $(id).textContent=text; $(id).className=error?'error':'success'; }
function node(tag, text, className) {const el=document.createElement(tag); if(text!==undefined)el.textContent=text; if(className)el.className=className;return el;}
function renderReview(job) {
  const renderKey=[job.id,job.state,job.reviewDigest,job.error,job.commit].join('|');
  if(renderedJob===renderKey)return;renderedJob=renderKey;
  $('raw-result').hidden=job.kind!=='raw-input-preflight';
  if(job.kind==='raw-input-preflight'){
    selectedDigest=null;status('publish-status','');$('confirm').checked=false;$('confirm').disabled=true;$('publish').disabled=true;
    $('review').hidden=true;$('empty-review').hidden=false;$('empty-review').textContent='该任务仅检查真实输入；请查看下方来源与阻塞项';
    renderRaw(job);return;
  }
  $('empty-review').hidden=true;$('review').hidden=false;
  if(selectedDigest!==job.reviewDigest){status('publish-status','');$('confirm').checked=false;selectedDigest=job.reviewDigest;}
  $('job-state').textContent=`${stateLabels[job.state]||job.state} (${job.state})`;$('version').textContent=job.gameVersion||'';
  $('summary').replaceChildren();
  for(const [name,value] of [['输入文件',job.filename],['输入哈希',job.inputSha256],['输出版本',job.release||'待提取'],['对象总量',job.objectBytes===undefined?'待计算':`${job.objectBytes} 字节`],['Git 提交',job.commit||'尚未发布']]){
    $('summary').append(node('dt',name),node('dd',String(value)));
  }
  $('diff').replaceChildren();
  for(const [family,change] of Object.entries(job.diff||{})){
    const row=node('tr');for(const value of [familyLabels[family]||family,change.added.length,change.changed.length,change.removed.length])row.append(node('td',String(value)));$('diff').append(row);
  }
  $('coverage-summary').textContent=job.error||(!job.coverage?'正在检查输入…':`合成策略必需子项 ${job.coverage.verifiedRequired}/${job.coverage.requiredCount}；共 ${job.coverage.submanifestCount} 项。${job.coverage.complete?'合成合同校验通过，不代表真实游戏完整覆盖。':'存在缺失项，不能发布。'}`);
  $('coverage').textContent=JSON.stringify(job.coverage||{error:job.error||null},null,2);
  $('confirm').disabled=job.state!=='READY_FOR_REVIEW';
  $('publish').disabled=publishing||job.state!=='READY_FOR_REVIEW'||!$('confirm').checked;
}
async function refreshJobs(){
  const jobs=await api('/api/jobs');$('jobs').replaceChildren();
  for(const job of jobs){const button=node('button',`${job.filename} · ${stateLabels[job.state]||job.state}`,'job');button.onclick=()=>{selected=job.id;selectedDigest=null;renderedJob=null;renderReview(job);};$('jobs').append(button);}
  const current=jobs.find(job=>job.id===selected);if(current)renderReview(current);
}
$('upload').onsubmit=async event=>{
  event.preventDefault();const file=$('file').files[0];if(!file)return;
  const button=event.currentTarget.querySelector('button');button.disabled=true;
  status('upload-status','正在上传并排队提取…');
  try{const form=new FormData();form.append('file',file);const job=await api('/api/jobs',{method:'POST',body:form});selected=job.id;selectedDigest=null;status('upload-status','已接收，请查看审核面板');await refreshJobs();}
  catch(error){status('upload-status',error.message,true);}finally{button.disabled=false;}
};
function renderRaw(job){
  $('raw-blockers').replaceChildren();$('raw-sources').replaceChildren();
  status('raw-upload-status',`${stateLabels[job.state]||job.state} · 仅检查来源，未执行或发布`,job.state==='BLOCKED'||job.state==='INTERRUPTED');
  for(const blocker of job.blockers||[])$('raw-blockers').append(node('li',blocker.message));
  for(const [role,source] of Object.entries(job.sources||{})){
    const article=node('article',undefined,'raw-source');article.append(node('h3',role==='server'?'服务端来源':'客户端 Content 来源'));
    const list=node('dl');const version=source.versionEvidence;
    for(const [label,value] of [['文件',source.filename],['ZIP SHA-256',source.archiveSha256],['上传体积',`${source.archiveBytes} 字节`],['可信版本',version?.status==='verified'?version.gameVersion:'未验证'],['文件清单',source.inventory?`${source.inventory.fileCount??source.inventory.files?.length??0} 个文件，展开 ${source.inventory.expandedBytes} 字节`:'尚无有效清单']])list.append(node('dt',label),node('dd',value));
    article.append(list);const details=node('details'),summary=node('summary','展开逐文件 SHA-256 与调试详情'),pre=node('pre','展开后读取完整清单');details.append(summary,pre);
    details.ontoggle=async()=>{if(!details.open||details.dataset.loaded)return;details.dataset.loaded='loading';try{const full=await api(`/api/jobs/${job.id}`);pre.textContent=JSON.stringify(full.sources[role],null,2);details.dataset.loaded='yes';}catch(error){pre.textContent=error.message;delete details.dataset.loaded;}};
    article.append(details);$('raw-sources').append(article);
  }
}
$('raw-upload').onsubmit=async event=>{
  event.preventDefault();if(rawSubmitting)return;rawSubmitting=true;const button=event.currentTarget.querySelector('button');button.disabled=true;
  status('raw-upload-status','正在上传并排队检查两份来源…');
  try{const form=new FormData();for(const [field,id] of [['server_file','server-file'],['client_file','client-file']]){const file=$(id).files[0];if(file)form.append(field,file);}if($('declared-version').value.trim())form.append('declared_version',$('declared-version').value.trim());const job=await api('/api/raw-jobs',{method:'POST',body:form});selected=job.id;selectedDigest=null;renderedJob=null;await refreshJobs();}
  catch(error){status('raw-upload-status',error.message,true);}finally{rawSubmitting=false;button.disabled=false;}
};
$('confirm').onchange=()=>{$('publish').disabled=publishing||!$('confirm').checked;};
$('publish').onclick=async()=>{
  if(publishing)return;const identity=selected;publishing=true;$('publish').disabled=true;
  try{const job=await api(`/api/jobs/${identity}/publish`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reviewDigest:selectedDigest,confirmed:$('confirm').checked})});if(selected===identity)status('publish-status',`已推送本地 Git：${job.commit}`);await refreshJobs();}
  catch(error){if(selected===identity)status('publish-status',error.message,true);}
  finally{publishing=false;await refreshJobs();}
};
async function hash(bytes){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');}
async function verified(path,digest){
  if(!/^(objects|releases)\/[A-Za-z0-9./-]+$/.test(path)||path.includes('..')||!/^([a-f0-9]{64})$/.test(digest))throw Error('非法资源引用');
  if(cache.has(digest))return cache.get(digest);
  const stored=await cachedObject(digest);
  if(stored&&await hash(stored)===digest)return stored;
  const response=await fetch('/cdn/'+path);if(!response.ok)throw Error('CDN资源不可用');
  const bytes=await boundedBytes(response.body,32*1024*1024);if(await hash(bytes)!==digest)throw Error('资源SHA-256校验失败');
  while(cachedBytes+bytes.byteLength>64*1024*1024&&cache.size){const [key,value]=cache.entries().next().value;cache.delete(key);cachedBytes-=value.byteLength;}
  await storeObject(digest,bytes);cache.set(digest,bytes);cachedBytes+=bytes.byteLength;return bytes;
}
async function pack(reference){
  const bytes=await verified(reference.path,reference.sha256);
  if(!Number.isSafeInteger(reference.rawBytes)||reference.rawBytes<0||reference.rawBytes>64*1024*1024)throw Error('无效解压大小');
  const raw=await boundedBytes(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip')),reference.rawBytes);
  if(raw.byteLength!==reference.rawBytes||await hash(raw)!==reference.rawSha256)throw Error('解压对象完整性校验失败');
  return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw));
}
$('refresh-client').onclick=async()=>{
  const epoch=++clientEpoch;
  try{const pointer=await api('/api/current');if(!pointer){status('client-status','CDN还没有已审核发布的版本');return;}
    const bytes=await verified(pointer.manifest,pointer.manifestSha256);const candidate=JSON.parse(new TextDecoder().decode(bytes));
    if(candidate.schemaVersion!==1||!candidate.coverage.complete)throw Error('版本结构或覆盖不完整');
    // Verify shared dictionaries before changing the active snapshot. Failed
    // downloads leave the old manifest and visible content untouched.
    await pack(candidate.strings);await pack(candidate.images);
    if(epoch!==clientEpoch)return;
    await saveSnapshot({pointer,manifestBytes:bytes});
    if(epoch!==clientEpoch)return;
    clientManifest=candidate;clientRelease=pointer.release;
    status('client-status',`已验证版本 ${candidate.gameVersion} · ${clientRelease.slice(0,12)}，资源按需加载`);
  }catch(error){if(epoch===clientEpoch)status('client-status',`${error.message}；保留原版本`,true);}
};
for(const family of families){const option=node('option',familyLabels[family]);option.value=family;$('family').append(option);}
$('load-family').onclick=async()=>{
  if(!clientManifest){status('client-status','请先检查CDN更新',true);return;}
  const snapshot=clientManifest,release=clientRelease,epoch=++familyEpoch;
  try{const [data,strings,images]=await Promise.all([pack(snapshot.packs[$('family').value]),pack(snapshot.strings),pack(snapshot.images)]);
    const fragment=document.createDocumentFragment();
    for(const row of data.rows.slice(0,100)){
      const card=node('article',undefined,'card');card.append(node('h3',strings[row[1]['zh-Hans']]||String(row[0])),node('p',`ID ${row[0]}`));
      card.append(node('p',strings[row[2]['zh-Hans']]||''),node('pre',JSON.stringify(row[3],null,2)));
      if(row[4].length){const image=images[row[4][0]];const bytes=await verified(image.path,image.sha256);const img=node('img');img.alt='已验证合成资源';const url=URL.createObjectURL(new Blob([bytes],{type:'image/png'}));img.onload=()=>URL.revokeObjectURL(url);img.src=url;card.prepend(img);}
      fragment.append(card);
    }
    if(clientRelease===release&&epoch===familyEpoch)$('client-items').replaceChildren(fragment);
  }catch(error){if(epoch===familyEpoch)status('client-status',error.message,true);}
};
let refreshing=false;
async function poll(){if(refreshing)return;refreshing=true;try{await refreshJobs();}catch(error){status('upload-status',error.message,true);}finally{refreshing=false;}}
async function restoreClient(){const epoch=clientEpoch;try{const saved=await loadSnapshot();if(saved&&await hash(saved.manifestBytes)===saved.pointer.manifestSha256){const manifest=JSON.parse(new TextDecoder().decode(saved.manifestBytes));if(epoch===clientEpoch&&manifest.schemaVersion===1&&manifest.coverage.complete){clientManifest=manifest;clientRelease=saved.pointer.release;status('client-status',`已恢复已验证缓存版本 ${manifest.gameVersion}`);}}}catch(error){status('client-status','缓存不可用，可重新检查CDN更新',true);}}
restoreClient();poll();setInterval(poll,2000);
