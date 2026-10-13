"use strict";
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="local-token"]').content;
const statuses = {draft:"准备就绪",running:"讨论进行中",paused:"已暂停",awaiting_user:"等待继续",finishing:"待成稿",completed:"已成稿"};
let current = null, state = null, busy = false, lastVersion = null, pollBusy = false;
function element(tag, text, cls) { const el = document.createElement(tag); if(text !== undefined) el.textContent = text; if(cls) el.className = cls; return el; }
async function api(path, data) {
  const options = {headers:{"X-Local-Token":token}, cache:"no-store"};
  if(data !== undefined) { options.method="POST"; options.headers["Content-Type"]="application/json"; options.body=JSON.stringify(data); }
  const response = await fetch(path, options), value = await response.json();
  if(!response.ok) throw new Error(value.error || "请求未完成，请刷新后重试。");
  return value;
}
function report(text) { $("alert").textContent = text; }
function modelSettings() {
  const model = $("model").value.trim();
  if(!model || !$("local-confirm").checked) throw new Error("请填写本机已安装的模型名，并确认使用本机服务。");
  return {model, confirm_local_service:true};
}
async function perform(fn) {
  if(busy) return; busy=true; controls(); report("");
  try { await fn(); } catch(error) { report(error.message); }
  finally { busy=false; if(current) await refresh().catch(error=>report(error.message)); controls(); }
}
function controls() {
  const status=state?.session.status, generating=state?.job?.status === "running";
  $("next").disabled=busy || generating || !["draft","running","paused","awaiting_user"].includes(status);
  $("next").textContent=status === "draft" ? "开始讨论" : "继续一个回合";
  $("pause").disabled=busy || status !== "running";
  $("finish").disabled=busy || !["running","paused","awaiting_user"].includes(status);
  $("interject").disabled=busy || !["running","paused"].includes(status);
  $("edit").disabled=busy || generating || status !== "finishing";
}
async function history() {
  const data=await api("/api/sessions"); $("sessions").replaceChildren();
  if(!data.sessions.length) $("sessions").append(element("p","还没有保存的圆桌。","hint"));
  data.sessions.forEach(s=>{const button=element("button",s.title); button.append(element("small", statuses[s.status] || s.status)); button.onclick=()=>perform(()=>select(s.session_id)); $("sessions").append(button);});
}
async function select(id) { current=id; state=null; lastVersion=null; controls(); $("artifact-preview").textContent="请选择下方可用的结果。"; await refresh(); }
async function refresh() {
  const id=current; if(!id || pollBusy) return; pollBusy=true;
  try {
    const data=await api(`/api/sessions/${id}`); if(current!==id) return; state=data;
    const session=data.session, job=data.job;
    $("status").textContent=statuses[session.status] || session.status;
    $("session-title").textContent=session.title;
    $("progress").textContent=`已提交 ${data.turns.length} / ${session.limits.max_turns} 回合 · ${job?.status === "running" ? "本机模型生成中，可以暂停或插话；底层计算可能仍继续。" : "每次继续生成一个回合。"}`;
    if(job?.status === "failed") report(job.error);
    if(lastVersion!==session.state_version) { render(data); lastVersion=session.state_version; }
    controls();
  } finally { pollBusy=false; }
}
function render(data) {
  const names=Object.fromEntries(data.session.participants.map(p=>[p.person_id,p.display_name]));
  $("turns").replaceChildren();
  if(!data.turns.length) $("turns").append(element("p","准备好了就开始讨论。人物会在这里依次发言。","empty"));
  const rows=[...data.turns.map(t=>({sequence:t.sequence,turn:t})),...data.events.map(e=>({sequence:e.after_sequence+.5,event:e}))].sort((a,b)=>a.sequence-b.sequence);
  rows.forEach(row=>{
    if(row.event) { $("turns").append(element("div","你的插话："+row.event.text,"user-event")); return; }
    const t=row.turn, article=element("article",undefined,"turn"), heading=element("div",undefined,"turn-header");
    heading.append(element("span",String(t.sequence),"turn-number"),element("span",names[t.speaker_id] || t.speaker_id));
    const details=element("details"); details.append(element("summary","查看回应与依据"),element("p",`回应：${t.responds_to.join("、")}\n证据：${t.basis.evidence_ids.join("、") || "无引用卡片，模拟推演"}\n来源：${t.basis.source_ids.join("、") || "无"}\n引用编号可核查，内容质量仍需人工审阅。`));
    article.append(heading,element("p",t.text),details); $("turns").append(article);
  });
  $("artifacts").replaceChildren();
  const files={"transcript.md":"逐轮记录","provenance.md":"依据附录"};
  if(data.article) Object.assign(files,{"essay.md":"编辑讨论稿","article.json":"结构化稿件"});
  Object.entries(files).forEach(([file,label])=>{
    const read=element("button",label); read.onclick=()=>perform(async()=>{const result=await api(`/api/artifacts/${current}/${file}`); $("artifact-preview").textContent=result.content;});
    const download=element("button",`下载${label}`,"quiet"); download.onclick=()=>perform(async()=>{const result=await api(`/api/artifacts/${current}/${file}`); const url=URL.createObjectURL(new Blob([result.content],{type:"text/plain;charset=utf-8"})); const a=element("a"); a.href=url;a.download=`${current}-${file}`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    $("artifacts").append(read,download);
  });
}
$("create-form").onsubmit=event=>{event.preventDefault();perform(async()=>{
  const participants=[...document.querySelectorAll('input[name="person"]:checked')].map(el=>el.value);
  const s=await api("/api/sessions",{topic:$("topic").value,goal:$("goal").value,participants,max_turns:Number($("turn-limit").value),preview:$("preview").checked});
  await select(s.session_id); await history();
});};
$("next").onclick=()=>perform(async()=>{const settings=modelSettings();const status=state.session.status;if(status==="draft") await api(`/api/sessions/${current}/start`,{});else if(["paused","awaiting_user"].includes(status)) await api(`/api/sessions/${current}/resume`,{});await api(`/api/sessions/${current}/generate`,settings);});
$("pause").onclick=()=>perform(()=>api(`/api/sessions/${current}/pause`,{}));
$("finish").onclick=()=>perform(()=>api(`/api/sessions/${current}/finish`,{}));
$("edit").onclick=()=>perform(()=>api(`/api/sessions/${current}/edit`,modelSettings()));
$("interject-form").onsubmit=event=>{event.preventDefault();perform(async()=>{await api(`/api/sessions/${current}/interject`,{text:$("interjection").value});$("interjection").value="";});};
$("refresh-list").onclick=()=>perform(history);
async function init() {
  const data=await api("/api/library");
  data.personas.forEach((p,i)=>{const label=element("label",undefined,"person"),input=element("input");input.type="checkbox";input.name="person";input.value=p.person_id;input.checked=i<3;label.append(input,element("span",p.display_name),element("small",p.requires_preview?"草稿":"可用"));$("personas").append(label);});
  await history();
  const requested=new URLSearchParams(location.search).get("session");
  if(requested) await select(requested);
}
init().catch(error=>report(error.message));
setInterval(()=>{if(!busy) refresh().catch(error=>report(error.message));},2000);
