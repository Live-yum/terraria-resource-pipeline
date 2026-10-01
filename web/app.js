const $ = id => document.getElementById(id);
const families = ['items','tiles','walls','paints','npcs','buffs','prefixes','player','worldgen','markers','pixel','map','locales','ids'];
let selected = null, selectedDigest = null, clientRelease = null, clientManifest = null;
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
  $('empty-review').hidden=true;$('review').hidden=false;
  if(selectedDigest!==job.reviewDigest){$('confirm').checked=false;selectedDigest=job.reviewDigest;}
  $('job-state').textContent=job.state;$('version').textContent=job.gameVersion||'';
  $('summary').replaceChildren();
  for(const [name,value] of [['输入文件',job.filename],['输入哈希',job.inputSha256],['输出版本',job.release||'待提取'],['对象总量',job.objectBytes===undefined?'待计算':`${job.objectBytes} 字节`],['Git 提交',job.commit||'尚未发布']]){
    $('summary').append(node('dt',name),node('dd',String(value)));
  }
  $('diff').replaceChildren();
  for(const [family,change] of Object.entries(job.diff||{})){
    const row=node('tr');for(const value of [family,change.added.length,change.changed.length,change.removed.length])row.append(node('td',String(value)));$('diff').append(row);
  }
  $('coverage').textContent=job.error||JSON.stringify(job.coverage||{},null,2);
  $('confirm').disabled=job.state!=='READY_FOR_REVIEW';
  $('publish').disabled=job.state!=='READY_FOR_REVIEW'||!$('confirm').checked;
}
async function refreshJobs(){
  const jobs=await api('/api/jobs');$('jobs').replaceChildren();
  for(const job of jobs){const button=node('button',`${job.filename} · ${job.state}`,'job');button.onclick=()=>{selected=job.id;selectedDigest=null;renderReview(job);};$('jobs').append(button);}
  const current=jobs.find(job=>job.id===selected);if(current)renderReview(current);
}
$('upload').onsubmit=async event=>{
  event.preventDefault();const file=$('file').files[0];if(!file)return;
  const button=event.currentTarget.querySelector('button');button.disabled=true;
  status('upload-status','正在上传并排队提取…');
  try{const form=new FormData();form.append('file',file);const job=await api('/api/jobs',{method:'POST',body:form});selected=job.id;selectedDigest=null;status('upload-status','已接收，请查看审核面板');await refreshJobs();}
  catch(error){status('upload-status',error.message,true);}finally{button.disabled=false;}
};
$('confirm').onchange=()=>{$('publish').disabled=!$('confirm').checked;};
$('publish').onclick=async()=>{
  $('publish').disabled=true;
  try{const job=await api(`/api/jobs/${selected}/publish`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reviewDigest:selectedDigest,confirmed:$('confirm').checked})});status('publish-status',`已推送本地 Git：${job.commit}`);await refreshJobs();}
  catch(error){status('publish-status',error.message,true);}
};
async function hash(bytes){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');}
async function verified(path,digest){
  if(!/^(objects|releases)\/[A-Za-z0-9./-]+$/.test(path)||path.includes('..')||!/^([a-f0-9]{64})$/.test(digest))throw Error('非法资源引用');
  if(cache.has(digest))return cache.get(digest);
  const response=await fetch('/cdn/'+path);if(!response.ok)throw Error('CDN资源不可用');
  const bytes=await response.arrayBuffer();if(bytes.byteLength>32*1024*1024||await hash(bytes)!==digest)throw Error('资源大小或SHA-256校验失败');
  while(cachedBytes+bytes.byteLength>64*1024*1024&&cache.size){const [key,value]=cache.entries().next().value;cache.delete(key);cachedBytes-=value.byteLength;}
  cache.set(digest,bytes);cachedBytes+=bytes.byteLength;return bytes;
}
async function pack(reference){
  const bytes=await verified(reference.path,reference.sha256);
  const raw=await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
  if(raw.byteLength!==reference.rawBytes||await hash(raw)!==reference.rawSha256)throw Error('解压对象完整性校验失败');
  return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw));
}
$('refresh-client').onclick=async()=>{
  try{const pointer=await api('/api/current');if(!pointer){status('client-status','CDN还没有已审核发布的版本');return;}
    const bytes=await verified(pointer.manifest,pointer.manifestSha256);const candidate=JSON.parse(new TextDecoder().decode(bytes));
    if(candidate.schemaVersion!==1||!candidate.coverage.complete)throw Error('版本结构或覆盖不完整');
    // Verify shared dictionaries before changing the active snapshot. Failed
    // downloads leave the old manifest and visible content untouched.
    await pack(candidate.strings);await pack(candidate.images);
    clientManifest=candidate;clientRelease=pointer.release;
    status('client-status',`已验证版本 ${candidate.gameVersion} · ${clientRelease.slice(0,12)}，资源按需加载`);
  }catch(error){status('client-status',`${error.message}；保留原版本`,true);}
};
for(const family of families){const option=node('option',family);option.value=family;$('family').append(option);}
$('load-family').onclick=async()=>{
  if(!clientManifest){status('client-status','请先检查CDN更新',true);return;}
  const snapshot=clientManifest,release=clientRelease;
  try{const [data,strings,images]=await Promise.all([pack(snapshot.packs[$('family').value]),pack(snapshot.strings),pack(snapshot.images)]);
    const fragment=document.createDocumentFragment();
    for(const row of data.rows.slice(0,100)){
      const card=node('article',undefined,'card');card.append(node('h3',strings[row[1]['zh-Hans']]||String(row[0])),node('p',`ID ${row[0]}`));
      card.append(node('p',strings[row[2]['zh-Hans']]||''),node('pre',JSON.stringify(row[3],null,2)));
      if(row[4].length){const image=images[row[4][0]];const bytes=await verified(image.path,image.sha256);const img=node('img');img.alt='已验证合成资源';const url=URL.createObjectURL(new Blob([bytes],{type:'image/png'}));img.onload=()=>URL.revokeObjectURL(url);img.src=url;card.prepend(img);}
      fragment.append(card);
    }
    if(clientRelease===release)$('client-items').replaceChildren(fragment);
  }catch(error){status('client-status',error.message,true);}
};
let refreshing=false;
async function poll(){if(refreshing)return;refreshing=true;try{await refreshJobs();}catch(error){status('upload-status',error.message,true);}finally{refreshing=false;}}
poll();setInterval(poll,2000);
