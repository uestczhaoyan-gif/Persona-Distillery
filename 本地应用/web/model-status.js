'use strict';
(() => {
  const input=document.getElementById('model');
  if(!input)return;
  const check=document.createElement('button');check.type='button';check.textContent='检查本机模型';
  const message=document.createElement('p');message.className='hint';message.setAttribute('role','status');
  const select=document.createElement('select');select.setAttribute('aria-label','已发现的本机模型');select.hidden=true;
  input.after(check,message,select);
  check.onclick=async()=>{
    check.disabled=true;message.textContent='正在检查本机服务…';select.hidden=true;select.replaceChildren();
    try{
      const response=await fetch('/api/model-status',{headers:{'X-Local-Token':document.querySelector('meta[name="local-token"]').content}});
      const value=await response.json();if(!response.ok)throw Error(value.error||'检查未完成');
      message.textContent=value.message;
      if(value.models.length){const placeholder=document.createElement('option');placeholder.textContent='选择要使用的本机模型';placeholder.value='';select.append(placeholder);
        for(const name of value.models){const option=document.createElement('option');option.value=name;option.textContent=name;select.append(option);}
        select.hidden=false;
      }
    }catch(error){message.textContent=error.message;}finally{check.disabled=false;}
  };
  select.onchange=()=>{if(select.value){input.value=select.value;input.dispatchEvent(new Event('input',{bubbles:true}));}};
})();
