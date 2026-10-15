const $ = id => document.getElementById(id);
let busy = false;
const names = {goal:'Цель',problem:'Задача',level:'Уровень',timing:'Сроки',format:'Формат',budget:'Бюджет',decision:'Решение'};
function add(text, type = '') {
  const node = document.createElement('div'); node.className = 'message ' + type;
  node.textContent = text; $('messages').append(node); node.scrollIntoView({block:'nearest'}); return node;
}
function choices(values) {
  $('choices').replaceChildren();
  for (const value of values || []) {const b = document.createElement('button'); b.textContent = value; b.onclick = () => send(value); $('choices').append(b);}
}
function profile(answers) {
  $('profile').replaceChildren();
  for (const [key,value] of Object.entries(answers || {})) {const dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = names[key] || key; dd.textContent = value; $('profile').append(dt,dd);}
}
async function api(url, options = {}) {
  const r = await fetch(url,{...options,headers:{'Content-Type':'application/json',...options.headers}});
  if (!r.ok) throw new Error(r.status === 429 ? 'Слишком много сообщений. Повторите через минуту.' : 'Ошибка соединения (' + r.status + '). Попробуйте ещё раз.');
  return r.json();
}
async function send(text) {
  if (busy || !text.trim()) return;
  busy = true; $('error').textContent = ''; $('input').value = ''; $('send').disabled = true;
  choices([]); add(text,'user'); const pending = add('Ассистент думает…','pending');
  try {const result = await api('/api/chat',{method:'POST',body:JSON.stringify({text,request_id:crypto.randomUUID()})}); pending.remove(); add(result.text,result.error ? 'error' : ''); choices(result.buttons); profile(result.answers);}
  catch (e) {pending.remove(); $('error').textContent = e.message; $('input').value = text;}
  finally {busy = false; $('send').disabled = false; $('input').focus();}
}
$('form').onsubmit = e => {e.preventDefault(); send($('input').value);};
$('input').onkeydown = e => {if(e.key === 'Enter' && !e.shiftKey){e.preventDefault();send($('input').value);}};
$('manager').onclick = () => send('Менеджер'); $('stop').onclick = () => send('/stop');
$('reset').onclick = () => send('/start'); $('delete').onclick = () => send('/delete');
(async () => {
  try {
    await api('/api/session',{method:'POST'});
    const health = await api('/health');
    $('status').textContent = health.llm_configured ? '● LLM настроена' : '○ Нужен ключ LLM';
    $('status').className = 'pill ' + (health.llm_configured ? 'ready' : 'warning');
    const h = await api('/api/history');
    if (h.messages.length) {h.messages.forEach(m => add(m.text,m.direction === 'Клиент' ? 'user' : ''));profile(h.state?.answers);choices(h.state?.status === 'stopped' ? ['/start'] : ['Продолжить подбор','Менеджер']);}
    else await send('/start');
  } catch(e) {$('status').textContent = 'Нет соединения'; $('error').textContent = e.message;}
})();
