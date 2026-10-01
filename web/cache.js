// Content-addressed, quota-bounded cache. Game resources only, never save files
// or executable code. Updating the active snapshot is a separate transaction.
const database = new Promise((resolve, reject) => {
  const request = indexedDB.open('resource-pipeline-cache-v1', 1);
  request.onupgradeneeded = () => {
    request.result.createObjectStore('objects', {keyPath:'digest'});
    request.result.createObjectStore('meta');
  };
  request.onsuccess=()=>resolve(request.result);
  request.onerror=()=>reject(request.error);
});
function done(transaction){return new Promise((resolve,reject)=>{transaction.oncomplete=resolve;transaction.onerror=()=>reject(transaction.error);transaction.onabort=()=>reject(transaction.error||Error('Cache transaction aborted'));});}
export async function cachedObject(digest){
  const db=await database;
  return new Promise((resolve,reject)=>{
    const request=db.transaction('objects').objectStore('objects').get(digest);
    request.onsuccess=()=>resolve(request.result?.bytes||null);request.onerror=()=>reject(request.error);
  });
}
export async function storeObject(digest,bytes){
  if(bytes.byteLength>32*1024*1024)throw Error('单对象超过缓存限制');
  const db=await database,transaction=db.transaction('objects','readwrite'),store=transaction.objectStore('objects');
  const complete=done(transaction),request=store.getAll();
  request.onsuccess=()=>{
    const all=request.result.filter(x=>x.digest!==digest).sort((a,b)=>a.used-b.used);
    let total=all.reduce((n,x)=>n+x.bytes.byteLength,0)+bytes.byteLength;
    while(total>64*1024*1024&&all.length){const oldest=all.shift();store.delete(oldest.digest);total-=oldest.bytes.byteLength;}
    store.put({digest,bytes,used:Date.now()});
  };
  await complete;
}
export async function saveSnapshot(value){
  const db=await database,transaction=db.transaction('meta','readwrite');
  transaction.objectStore('meta').put(value,'active');await done(transaction);
}
export async function loadSnapshot(){
  const db=await database;
  return new Promise((resolve,reject)=>{const request=db.transaction('meta').objectStore('meta').get('active');request.onsuccess=()=>resolve(request.result||null);request.onerror=()=>reject(request.error);});
}
export async function boundedBytes(stream,maximum){
  if(!stream)throw Error('响应没有内容');
  const reader=stream.getReader(),chunks=[];let total=0;
  try{while(true){const {value,done}=await reader.read();if(done)break;total+=value.byteLength;if(total>maximum)throw Error('资源或解压体积超限');chunks.push(value);}}
  catch(error){await reader.cancel();throw error;}
  const output=new Uint8Array(total);let offset=0;for(const chunk of chunks){output.set(chunk,offset);offset+=chunk.byteLength;}return output.buffer;
}
