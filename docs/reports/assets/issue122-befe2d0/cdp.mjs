import fs from 'node:fs/promises';
const chrome='http://127.0.0.1:18303';
export async function tab(url='about:blank'){
 const target=await (await fetch(chrome+'/json/new?'+encodeURIComponent(url),{method:'PUT'})).json();
 return connect(target);
}
export async function connect(target){
 const ws=new WebSocket(target.webSocketDebuggerUrl);
 await new Promise((ok,fail)=>{ws.onopen=ok;ws.onerror=fail});
 let id=0;const pending=new Map(),events=[];
 ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result)}else events.push(m)};
 const send=(method,params={})=>new Promise((resolve,reject)=>{pending.set(++id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});
 const ev=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true,userGesture:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description??expression);return r.result.value};
 const wait=async expression=>{for(let i=0;i<35;i++){try{const v=await ev(expression);if(v)return v}catch{}await new Promise(r=>setTimeout(r,150))}throw Error('timeout '+expression)};
 const nav=async url=>{await send('Page.navigate',{url});await wait('document.readyState==="complete" && location.href.includes('+JSON.stringify(url)+')')};
 const shot=async name=>fs.writeFile(new URL(name+'.png',import.meta.url),Buffer.from((await send('Page.captureScreenshot')).data,'base64'));
 await send('Page.enable');await send('Network.enable');
 return {target,send,ev,wait,nav,shot,events,close:()=>ws.close()};
}
