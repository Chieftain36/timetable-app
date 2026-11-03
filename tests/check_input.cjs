const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const nodes = new Map();
function node() { return {
  textContent: '', children: [], open: false, dataset: {},
  classList: {add(){}, remove(){}, toggle(){}},
  setAttribute(){}, getAttribute(){return '';},
  append(...children){this.children.push(...children);},
  addEventListener(){}, remove(){},
  insertAdjacentElement(_, child){nodes.set(child.id, child);},
  setCustomValidity(message){this.customMessage=message;}, validity:{valid:true},
}; }
for (const id of ['toast-region','delete-schedule-dialog','confirm-schedule-delete','cancel-schedule-delete','login-form','logout-button','toggle-password','login-email','login-password']) nodes.set(id,node());
let status = 422;
let payload = {detail:'Ошибка поля'};
const context = vm.createContext({
  document: {addEventListener(){}, getElementById: id => nodes.get(id), createElement: node},
  localStorage: {getItem: () => null},
  setTimeout: () => 1, clearTimeout(){},
  fetch: async () => ({ok:false,status,headers:{get:()=>'application/json'},json:async()=>payload}),
});
vm.runInContext(fs.readFileSync('tests/frontend.js','utf8'),context);
context.input = node();
context.input.required = true;
context.input.type = 'text';
for (const value of ['CS-1','ИТ 1','Қазақ тілі','École']) {
  context.input.value = value;
  assert(vm.runInContext('validateInput(input, TEXT_PATTERN)',context));
}
for (const value of ['A<B','A_B','A\n','😀','汉','  ','']) {
  context.input.value = value;
  assert(!vm.runInContext('validateInput(input, TEXT_PATTERN)',context),value);
}
context.input.type='number';
for (const value of ['0','-1','1.1','1e2','9007199254740992']) {
  context.input.value=value;
  assert(!vm.runInContext('validateInput(input, POSITIVE_INTEGER_PATTERN)',context));
}
context.input.value='2';
assert(vm.runInContext('validateInput(input, POSITIVE_INTEGER_PATTERN)',context));
vm.runInContext("showToast('<img src=x onerror=alert(1)>')",context);
assert.equal(nodes.get('toast-region').children[0].children[0].textContent,'<img src=x onerror=alert(1)>');
(async()=> {
  for (const code of [400,409,422,500]) {
    status=code;
    await assert.rejects(vm.runInContext("api('/test')",context),error => error.message === (code===500 ? 'Server error. Please try again later.' : 'Ошибка поля'));
  }
  console.log('Frontend validation: allowed/disallowed text, integers, safe toast text and server error statuses passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
