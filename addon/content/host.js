/* Native Python subprocess; no HTTP bridge and no shell command construction. */
var BabelHost = {
  process: null,
  cancelled: false,

  async resource(url) {
    // Resource channels support jar: paths containing '@' in installed XPI names.
    // Zotero.HTTP is intended for network URLs and rejects those paths in Zotero 10.
    return Zotero.File.getResourceAsync ? Zotero.File.getResourceAsync(url) : Zotero.File.getResource(url);
  },

  async run({python, rootURI, directory, request, check = false, prepare = false, checkCodex = false, loginCodex = false, listCodex = false, onProgress = () => {}, startupTimeoutMs = 600000}) {
    if (this.process) throw new Error('已有任务正在运行。');
    const {Subprocess} = ChromeUtils.importESModule('resource://gre/modules/Subprocess.sys.mjs');
    if (!PathUtils.isAbsolute(python) || !await IOUtils.exists(python)) {
      throw new Error('Python 路径不存在，请选择安装脚本输出的可执行文件。');
    }
    const script = PathUtils.join(directory, 'worker.py');
    await IOUtils.writeUTF8(script, await this.resource(rootURI + 'content/worker.py'));
    await IOUtils.writeUTF8(PathUtils.join(directory, 'codex_backend.py'), await this.resource(rootURI + 'content/codex_backend.py'));
    await IOUtils.writeUTF8(PathUtils.join(directory, 'translation_support.py'), await this.resource(rootURI + 'content/translation_support.py'));
    if (this.cancelled) throw new Error('任务已取消。');
    const process = await Subprocess.call({command: python,
      arguments: ['-u', script, ...(check ? ['--check'] : prepare ? ['--prepare'] : checkCodex ? ['--check-codex'] : loginCodex ? ['--login-codex'] : listCodex ? ['--list-codex-models'] : [])],
      workdir: directory, stderr: 'pipe',
      environmentAppend: true, environment: {PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8',
        TEMP: directory, TMP: directory, TMPDIR: directory}});
    this.process = process;
    if (this.cancelled) process.kill(0);
    let result = null, failure = '', buffer = '';
    let timedOut = false;
    // Initial imports and downloads used to wait indefinitely with a 0% bar.
    // Bound this startup phase; model translation has its own upstream timeouts.
    const startupTimer = setTimeout(() => {
      timedOut = true;
      try { process.kill(0); } catch (_) {}
    }, startupTimeoutMs);
    const secret = request?.apiKey || '';
    const redact = message => secret ? String(message).split(secret).join('[REDACTED]') : String(message);
    const consume = line => {
      if (!line.trim()) return;
      let event;
      try { event = JSON.parse(line); } catch (_) { return; }
      if (event.phase === 'translation' || ['result', 'ready', 'error'].includes(event.type)) clearTimeout(startupTimer);
      if (event.type === 'progress') onProgress({stage: redact(event.stage), percent: event.percent});
      if (event.type === 'error') failure = redact(event.message);
      if (event.type === 'result' || ((check || prepare || checkCodex || loginCodex || listCodex) && event.type === 'ready')) result = event;
    };
    const readOut = (async () => {
      while (true) {
        const chunk = await process.stdout.readString();
        if (!chunk) break;
        buffer += chunk;
        if (buffer.length > 2 * 1024 * 1024) throw new Error('BabelDOC 返回了过大的进度消息。');
        let index;
        while ((index = buffer.indexOf('\n')) !== -1) {
          consume(buffer.slice(0, index)); buffer = buffer.slice(index + 1);
        }
      }
      if (buffer.trim()) consume(buffer);
    })();
    // Always drain stderr to prevent deadlocks. It may contain keys or paper text;
    // never show or persist raw dependency diagnostics.
    const readErr = (async () => { while (await process.stderr.readString()) {} })();
    try {
      if (!check && !prepare && !this.cancelled) await process.stdin.write(JSON.stringify(request) + '\n');
      await process.stdin.close();
      const wait = process.wait();
      await Promise.all([readOut, readErr]);
      const {exitCode} = await wait;
      if (this.cancelled) throw new Error('任务已取消。');
      if (timedOut) throw new Error('启动或资源准备已超过 10 分钟，任务已停止。已下载的资源会保留；请检查网络/代理后点击“准备资源”重试。');
      if (failure) throw new Error(failure);
      if (exitCode !== 0 || !result) throw new Error(`BabelDOC 进程未正常完成（退出码 ${exitCode}）。请检查 Python 环境和模型接口。`);
      return result;
    } catch (error) {
      // Cancellation can race stdin.write/readString and close native pipes.
      // Normalize those errors instead of showing a misleading pipe failure.
      if (this.cancelled) throw new Error('任务已取消。');
      if (timedOut) throw new Error('启动或资源准备已超过 10 分钟，任务已停止。已下载的资源会保留；请检查网络/代理后点击“准备资源”重试。');
      throw error;
    } finally {
      clearTimeout(startupTimer);
      try { process.kill(0); } catch (_) {}
      await process.wait().catch(() => {});
      if (this.process === process) this.process = null;
    }
  },

  cancel() {
    this.cancelled = true;
    try { this.process?.kill(0); } catch (_) {}
  }
};
