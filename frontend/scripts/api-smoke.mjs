import assert from 'node:assert/strict';
const base=process.env.DASHBOARD_TEST_URL||'http://127.0.0.1:5173/api';
async function read(path){const result=await fetch(base+path,{signal:AbortSignal.timeout(15000)});assert.equal(result.status,200,`${path}: HTTP ${result.status}`);return result.json()}
const health=await read('/health');assert.equal(health.database,'connected');
const stats=await read('/dashboard/stats');for(const field of ['medicines','orders','active_sessions','calls','active_calls'])assert.equal(typeof stats[field],'number');
const catalogue=await read('/medicines?limit=100');assert.equal(catalogue.total,stats.medicines);
const exact=await read('/medicines/lookup/exact?name=BRUFEN');assert.equal(exact.length,3);
const matchResponse=await fetch(base+'/medicine-matching/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:'BRUFEN'})});assert.equal(matchResponse.status,200);const match=await matchResponse.json();assert.equal(match.status,'CLARIFICATION_REQUIRED');
for(const path of ['/dashboard/orders','/dashboard/sessions','/dashboard/calls']){const data=await read(path);assert(Array.isArray(data.items));assert.equal(typeof data.total,'number')}
const voice=await read('/voice/status');assert('stt'in voice&&'tts'in voice);
const telephone=await read('/telephony/status');assert.equal(telephone.live_verified,false);
const unauthenticated=await fetch('http://127.0.0.1:8001/dashboard/stats');assert.equal(unauthenticated.status,401);
console.log(JSON.stringify({real_api:true,database:health.database,medicines:stats.medicines,brufen_variants:exact.length,matching:match.status,orders:stats.orders,calls:stats.calls,telephony_enabled:telephone.enabled,live_verified:telephone.live_verified,protected_reads:true}));
