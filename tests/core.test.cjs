const {test} = require('node:test');
const assert = require('node:assert/strict');
const core = require('../addon/content/core.js');

const valid = {...core.defaults, python: 'C:\\Python\\python.exe', model: 'local-model'};
test('page ranges are task input and blank means the entire paper', () => {
  assert.equal('pages' in core.defaults, false);
  assert.equal(core.validatePages('  '), '');
  assert.equal(core.validatePages(' 3-8,12 '), '3-8,12');
  for (const pages of ['0', '8-3', '1.5', '9007199254740992']) assert.throws(() => core.validatePages(pages));
});
test('Codex mode preserves API fields and requires a selected model', () => {
  const c=core.validate({...valid,backend:'codex',model:'',codexModel:'selected-model'});
  assert.equal(c.backend,'codex'); assert.equal(c.codexModel,'selected-model');
  assert.equal(c.baseURL,valid.baseURL);
  assert.throws(()=>core.validate({...valid,backend:'codex',codexModel:''}), /选择.*Codex/);
});
test('Codex effort defaults to low and validates saved values', () => {
  assert.equal(core.validate(valid).codexEffort,'low');
  assert.equal(core.validate({...valid,codexEffort:'medium'}).codexEffort,'medium');
  assert.throws(()=>core.validate({...valid,codexEffort:'unsupported'}));
});
test('Codex concurrency defaults to two and stays within bounded limits', () => {
  assert.equal(core.validate(valid).codexConcurrency,2);
  assert.equal(core.validate({...valid,codexConcurrency:'3'}).codexConcurrency,3);
  for (const codexConcurrency of [0,5,1.5,'bad']) assert.throws(()=>core.validate({...valid,codexConcurrency}));
});
test('configuration accepts local and cloud endpoints, rejects embedded secrets and bad page ranges', () => {
  assert.equal(core.validate(valid).baseURL, 'http://127.0.0.1:11434/v1');
  assert.equal(core.validate({...valid, baseURL: 'https://api.example.org/v1/'}).baseURL, 'https://api.example.org/v1');
  for (const baseURL of ['file:///tmp/x', 'https://key@example.org/v1', 'https://example.org/v1?key=secret']) {
    assert.throws(() => core.validate({...valid, baseURL}));
  }
  for (const pages of ['0', '0-4', '5-2', '1;bad', '1--2']) assert.throws(() => core.validate({...valid, pages}));
  assert.equal(core.validate({...valid, pages: '1-5,8,-3,9-'}).pages, '1-5,8,-3,9-');
});

function paper(id = 1) { return {id, libraryID: 1, isAttachment: () => false, isRegularItem: () => true, getAttachments: () => [2,3]}; }
function pdf(id, generated = false) { return {id, parentItemID: 1, attachmentContentType: 'application/pdf',
  isAttachment: () => true, getTags: () => generated ? [{tag: 'babeldoc:generated'}] : [], getFilePathAsync: async () => `/pdf/${id}.pdf`}; }
function itemAPI(items) { return {get: id => items.find(item => item.id === id), exists: async () => true, editable: () => true}; }
test('paper selects source PDFs and ignores generated attachments, explicit PDF selection works', async () => {
  const items = [paper(), pdf(2), pdf(3, true)], api = itemAPI(items);
  const target = await core.target([items[0]], api);
  assert.equal(target.parent.id, 1); assert.deepEqual(target.candidates.map(c => c.item.id), [2]);
  assert.equal((await core.target([items[2]], api)).candidates[0].item.id, 3);
});
test('rejects missing, standalone, inaccessible, read-only and multi-selected sources', async () => {
  const items = [paper(), pdf(2), pdf(3)], api = itemAPI(items);
  await assert.rejects(core.target([], api));
  await assert.rejects(core.target(items, api));
  await assert.rejects(core.target([{...pdf(2), parentItemID: null}], api));
  await assert.rejects(core.target([items[0]], {...api, exists: async () => false}));
  await assert.rejects(core.target([items[0]], {...api, editable: () => false}));
});
function commitAPI(failAt = 0) {
  const state = {created: [], erased: [], checked: []};
  return {...state, validatePDF: async path => state.checked.push(path), assertParent: async () => {},
    importFile: async (path, parentID, title) => {
      if (failAt === state.created.length + 1) throw new Error('disk full');
      const item = {path, parentID, title, tags: [], addTag(tag) {this.tags.push(tag);}, setField(_, value) {this.title = value;}, setNote(note) {this.note=note;}, saveTx: async () => {}};
      state.created.push(item); return item;
    }, erase: async item => state.erased.push(item)};
}
const job = {parentID: 42, sourceID: 'SOURCE12', langOut: 'zh', paths: {mono: '/job/mono.pdf', dual: '/job/dual.pdf'}};
test('both PDFs are imported under the captured parent and tagged for source tracking', async () => {
  const api = commitAPI(); const result = await core.commit(job, api);
  assert.equal(result.length, 2); assert.deepEqual(api.checked, ['/job/mono.pdf', '/job/dual.pdf']);
  assert.ok(result.every(item => item.parentID === 42 && item.tags.includes('babeldoc:source:SOURCE12')));
  assert.match(result[0].title, /译文/); assert.match(result[1].title, /双语对照/);
});
test('incomplete output never mutates the library; second import failure rolls back first', async () => {
  const api = commitAPI(); await assert.rejects(core.commit({...job, paths: {mono: '/job/mono.pdf'}}, api));
  assert.equal(api.created.length, 0);
  const fail = commitAPI(2); await assert.rejects(core.commit(job, fail), /disk full/);
  assert.deepEqual(fail.erased, fail.created);
});

test('partial translation still imports both PDFs with review tags and escaped notes', async () => {
  const api=commitAPI();
  const items=await core.commit({...job,warnings:{hasWarnings:true,untranslatedParagraphs:2,pages:{3:2},reasons:[{message:'<script>provider</script>',count:1}]}},api);
  assert.equal(items.length,2);
  assert.ok(items.every(item=>item.title.includes('含未译原文') && item.tags.includes('babeldoc:partial')));
  assert.match(items[0].note,/3（2 段）/); assert.ok(!items[0].note.includes('<script>'));
});
test('parent deletion and tag-save failure cannot leave a half-imported pair', async () => {
  const api = commitAPI(); let checks = 0;
  api.assertParent = async () => {if (++checks === 2) throw new Error('parent deleted');};
  await assert.rejects(core.commit(job, api), /parent deleted/); assert.equal(api.erased.length, 1);
  const save = commitAPI(), original = save.importFile;
  save.importFile = async (...args) => {const item = await original(...args); item.saveTx = async () => {throw new Error('save failed');}; return item;};
  await assert.rejects(core.commit(job, save), /save failed/); assert.equal(save.erased.length, 1);
});
