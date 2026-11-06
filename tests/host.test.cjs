const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
function fixture({chunks = [], exitCode = 0, timers = {setTimeout, clearTimeout}} = {}) {
  const calls = {writes: [], killed: 0};
  const pipe = chunks => ({readString: async () => chunks.shift() || ''});
  const process = {stdout: pipe([...chunks]), stderr: pipe(['sensitive ignored stderr', '']),
    stdin: {write: async text => calls.writes.push(text), close: async () => {}},
    wait: async () => ({exitCode}), kill: () => {calls.killed++;}};
  const context = vm.createContext({
    ...timers,
    ChromeUtils: {importESModule: () => ({Subprocess: {call: async options => {calls.options = options; return process;}}})},
    PathUtils: {isAbsolute: () => true, join: path.win32.join},
    IOUtils: {exists: async () => true, writeUTF8: async (file, contents) => {calls.script = {file, contents};}},
    Zotero: {File: {getResourceAsync: async () => '# bundled runner'}},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../addon/content/host.js'), 'utf8'), context);
  return {host: context.BabelHost, calls, process};
}
const args = {python: 'C:\\with spaces\\python.exe', rootURI: 'jar:file:///test.xpi!/', directory: 'C:\\jobs\\id',
  request: {apiKey: 'private-secret', model: 'local'}};
test('native host handles fragmented NDJSON and sends credentials only through stdin', async () => {
  const {host, calls} = fixture({chunks: ['noise\n{"type":"progress","stage":"work","percent":42}\n{"type":', '"result","mono":"a","dual":"b"}\n']});
  const progress = [];
  const result = await host.run({...args, onProgress: event => progress.push(event)});
  assert.equal(result.mono, 'a'); assert.equal(progress[0].percent, 42);
  assert.ok(!JSON.stringify(calls.options).includes('private-secret'));
  assert.equal(JSON.parse(calls.writes[0]).apiKey, 'private-secret');
  assert.equal(calls.options.command, args.python);
  assert.equal(host.process, null);
});
test('failure is redacted and a result on nonzero exit is rejected', async () => {
  const {host} = fixture({chunks: ['{"type":"error","message":"bad private-secret"}\n'], exitCode: 1});
  await assert.rejects(host.run(args), error => error.message === 'bad [REDACTED]');
  const second = fixture({chunks: ['{"type":"result","mono":"a","dual":"b"}\n'], exitCode: 2});
  await assert.rejects(second.host.run(args), /退出码 2/);
});
test('cancellation before process creation does not launch Python', async () => {
  const {host, calls} = fixture(); host.cancel();
  await assert.rejects(host.run(args), /取消/);
  assert.equal(calls.options, undefined);
});
test('resource preparation sends no request or API key and accepts ready events', async () => {
  const {host, calls} = fixture({chunks: ['{"type":"ready","babeldoc":"0.6.4"}\n']});
  await host.run({...args, prepare: true});
  assert.ok(calls.options.arguments.includes('--prepare'));
  assert.equal(calls.writes.length, 0);
});

test('Codex catalog request uses its dedicated metadata mode and returns models', async () => {
  const {host, calls} = fixture({chunks: ['{"type":"ready","models":[{"id":"account-model"}]}\n']});
  const result = await host.run({...args, request:{codexBinary:''}, listCodex:true});
  assert.ok(calls.options.arguments.includes('--list-codex-models'));
  assert.equal(result.models[0].id,'account-model');
  assert.deepEqual(JSON.parse(calls.writes[0]),{codexBinary:''});
});
test('startup watchdog stops waiting and returns an actionable error', async () => {
  const {host, calls} = fixture({timers: {setTimeout(fn) {queueMicrotask(fn); return 0;}, clearTimeout() {}}});
  await assert.rejects(host.run(args), /10 分钟.*准备资源/);
  assert.ok(calls.killed > 0);
});
test('cancellation during stdin write reports cancellation instead of a closed-pipe error', async () => {
  const {host,process} = fixture();
  process.stdin.write = async () => {host.cancel(); throw new Error('pipe closed');};
  await assert.rejects(host.run(args), /任务已取消/);
  assert.equal(host.process,null);
});
