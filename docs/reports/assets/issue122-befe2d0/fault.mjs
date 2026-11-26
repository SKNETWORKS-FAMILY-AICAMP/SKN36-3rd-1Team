import http from 'node:http';
let fault=true;
http.createServer(async (req,res)=>{
 if(req.url==='/recover'){fault=false;res.end('ok');return}
 if(fault){res.writeHead(503);res.end('sample_only injected upstream failure');return}
 const r=await fetch('http://127.0.0.1:18300'+req.url);res.writeHead(r.status,{'content-type':'application/json'});res.end(await r.text());
}).listen(18306,'127.0.0.1');
