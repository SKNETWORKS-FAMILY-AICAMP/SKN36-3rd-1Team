import {tab} from './cdp.mjs';
import fs from 'node:fs/promises';
const d=await tab();const result=[];
for(const width of [320,390]){await d.send('Emulation.setDeviceMetricsOverride',{width,height:844,deviceScaleFactor:1,mobile:true});await d.nav('http://127.0.0.1:18301/policies/POL_YC_007/?theme=dark');
result.push({width,links:await d.ev('[...document.querySelectorAll("a")].filter(a=>a.getBoundingClientRect().height>0).map(a=>[a.innerText,a.getBoundingClientRect().height])')});}
console.log(JSON.stringify(result));await fs.writeFile(new URL('mobile-links.json',import.meta.url),JSON.stringify(result,null,2));d.close();
