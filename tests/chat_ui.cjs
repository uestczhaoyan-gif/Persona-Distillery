// Browser-controller recovery checks using original synthetic responses; no model or transcript files.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../本地应用/web/chat.js'),'utf8');
function harness(){
 const nodes={};
 const node=()=>({value:'',textContent:'',disabled:false,checked:true,options:[],children:[],append(...xs){this.children.push(...xs);this.options.push(...xs);},replaceChildren(...xs){this.children=xs;}});
 const get=id=>nodes[id]??=node();
 let record={person_id:'kongzi',display_name:'孔子',model:'synthetic',version:0,turns:[],job:null};
 let failure=null;let calls=[];
 const storage=new Map();
 const context=vm.createContext({document:{getElementById:get,querySelector:()=>({content:'test-token'}),createElement:node},sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},location:{search:''},URLSearchParams,setTimeout:()=>1,clearTimeout:()=>{},fetch:async(url,opts)=>{calls.push([url,opts]);if(failure&&opts.method==='GET'&&url.includes('/api/chats/')){if(failure==='network')throw Error('offline');return {ok:false,status:failure,json:async()=>({error:'内存对话已结束'})};}let data=url==='/api/overview'?{personas:[{person_id:'kongzi',display_name:'孔子',preview:true}]}:url==='/api/chats'?{session_id:'CH-test'}:record;return {ok:true,json:async()=>data};}});
 vm.runInContext(source,context);
 const settle=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
 const click=async id=>{get(id).onclick();await settle();};
 const submit=async id=>{get(id).onsubmit({preventDefault(){}});await settle();};
 return {get,click,submit,settle,calls,record,setFailure:v=>failure=v};
}
async function started(){const h=harness();await h.settle();h.get('model').value='synthetic';await h.submit('create-form');return h;}
test('failed model answer preserves question; successful answer clears only submitted draft',async()=>{
 const h=await started();h.get('question').value='Original synthetic question';h.record.job={status:'running'};await h.submit('send-form');assert.equal(h.get('question').value,'Original synthetic question');
 h.record.job={status:'failed',error:'Synthetic failure'};await h.click('refresh');assert.equal(h.get('question').value,'Original synthetic question');assert.equal(h.get('send').disabled,false);
 h.record.job={status:'running'};await h.submit('send-form');h.record.version=1;h.record.job={status:'done'};h.record.turns=[{question:'Original synthetic question',answer:'Synthetic answer'}];await h.click('refresh');assert.equal(h.get('question').value,'');
 h.get('question').value='Second original question';h.record.job={status:'running'};await h.submit('send-form');h.get('question').value='Next draft written during generation';h.record.version=2;h.record.job={status:'done'};await h.click('refresh');assert.equal(h.get('question').value,'Next draft written during generation');
});
test('network recovery is explicit and does not resubmit; missing session allows new conversation',async()=>{
 const h=await started();h.setFailure('network');await h.click('refresh');assert.equal(h.get('alert').textContent,'offline');h.setFailure(null);await h.click('refresh');assert.equal(h.calls.filter(([u,o])=>u.endsWith('/send')&&o.method==='POST').length,0);
 h.setFailure(404);await h.click('refresh');assert.equal(h.get('send').disabled,true);assert.equal(h.get('status').textContent,'对话已结束');h.setFailure(null);await h.submit('create-form');assert.equal(h.get('send').disabled,false);
});
