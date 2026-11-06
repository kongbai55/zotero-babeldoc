var BabelDOC = {
  rootURI: '', windows: new Map(), listeners: new Set(), busy: false,
  status: '就绪', percent: 0, importing: false, stopped: false, settingsWindow: null, translationWindow: null,
  pref: 'extensions.babeldoc-local.config',
  codexModels: [], codexCatalogKey: '',

  config() {
    try {
      const {pages, ...saved} = JSON.parse(Services.prefs.getStringPref(this.pref, '{}'));
      return {...BabelCore.defaults, ...saved};
    }
    catch (_) { return {...BabelCore.defaults}; }
  },
  key(baseURL) {
    return Services.logins.findLogins('chrome://babeldoc-local', null, baseURL.replace(/\/+$/, ''))[0]?.password || '';
  },
  async save(config, key) {
    const c = BabelCore.validate(config);
    const old = c.backend === 'api' ? Services.logins.findLogins('chrome://babeldoc-local', null, c.baseURL)[0] : null;
    if (c.backend === 'api' && key) {
      const login = Components.classes['@mozilla.org/login-manager/loginInfo;1'].createInstance(Components.interfaces.nsILoginInfo);
      login.init('chrome://babeldoc-local', null, c.baseURL, 'api-key', key, '', '');
      if (old) Services.logins.modifyLogin(old, login);
      else await Services.logins.addLoginAsync(login);
    } else if (old) Services.logins.removeLogin(old);
    // Persist an allowlist, never caller-provided credentials or arbitrary keys.
    const clean = Object.fromEntries(Object.keys(BabelCore.defaults).map(name => [name, c[name]]));
    Services.prefs.setStringPref(this.pref, JSON.stringify(clean));
    this.setStatus('设置已保存');
  },
  setStatus(status, percent = this.percent) {
    this.status = status; this.percent = Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : 0;
    for (const fn of this.listeners) { try { fn(); } catch (_) {} }
    for (const {translate, cancel} of this.windows.values()) {
      translate.disabled = this.busy;
      cancel.disabled = !this.busy || this.importing;
    }
  },
  alert(win, message) { Services.prompt.alert(win || Zotero.getMainWindow(), 'BabelDOC · 论文翻译', message); },
  openSettings(win) {
    if (this.settingsWindow && !this.settingsWindow.closed) { this.settingsWindow.focus(); return; }
    this.settingsWindow = (win || Zotero.getMainWindow()).openDialog(
      'chrome://babeldoc-local/content/settings.xhtml', 'babeldoc-local-settings',
      'chrome,centerscreen,resizable,width=700,height=770', {controller: this});
  },
  choosePages(win, title) {
    return new Promise(resolve => {
      const choice = {title, pages: null};
      const url = 'chrome://babeldoc-local/content/translate.xhtml';
      const dialog = (win || Zotero.getMainWindow()).openDialog(
        url, 'babeldoc-local-translate-options',
        'chrome,centerscreen,resizable,width=540,height=370', choice);
      this.translationWindow = dialog;
      const onUnload = event => {
        // Opening a chrome window first unloads its initial about:blank document.
        if (event.target.documentURI !== url) return;
        dialog.removeEventListener('unload', onUnload);
        if (this.translationWindow === dialog) this.translationWindow = null;
        resolve(choice.pages);
      };
      dialog.addEventListener('unload', onUnload);
    });
  },
  attachWindow(win) {
    if (this.windows.has(win)) return;
    const doc = win.document;
    const make = (id, label, handler) => {
      const node = doc.createXULElement('menuitem'); node.id = id; node.setAttribute('label', label);
      node.addEventListener('command', handler); return node;
    };
    const menu = doc.createXULElement('menu'); menu.id = 'babeldoc-local-menu'; menu.setAttribute('label', 'BabelDOC · 论文翻译');
    const popup = doc.createXULElement('menupopup'); menu.append(popup);
    const translate = make('babeldoc-local-translate', '翻译当前论文（译文 + 双语 PDF）', () => this.translate(win));
    const cancel = make('babeldoc-local-cancel', '取消翻译', () => this.cancel());
    popup.append(translate, cancel, make('babeldoc-local-settings', '设置与任务进度…', () => this.openSettings(win)));
    doc.getElementById('menu_ToolsPopup')?.append(menu);
    const context = make('babeldoc-local-context', 'BabelDOC：翻译 PDF 并添加双语附件', () => this.translate(win));
    doc.getElementById('zotero-itemmenu')?.append(context);
    this.windows.set(win, {menu, context, translate, cancel});
    this.setStatus(this.status);
  },
  detachWindow(win) {
    const entry = this.windows.get(win);
    entry?.menu.remove(); entry?.context.remove(); this.windows.delete(win);
  },
  selected(win) {
    const reader = Zotero.Reader.getByTabID(win.Zotero_Tabs?.selectedID);
    if (reader?.itemID) return [Zotero.Items.get(reader.itemID)];
    return win.ZoteroPane?.getSelectedItems() || [];
  },
  itemAPI() {
    return {get: id => Zotero.Items.get(id), exists: path => IOUtils.exists(path),
      editable: id => { const library = Zotero.Libraries.get(id); return library?.editable && library?.filesEditable; }};
  },
  async directory() {
    const root = PathUtils.join(Zotero.Profile.dir, 'babeldoc-local', 'jobs');
    await IOUtils.makeDirectory(root, {createAncestors: true, ignoreExisting: true});
    const id = Services.uuid.generateUUID().toString().replace(/[{}]/g, '');
    const directory = PathUtils.join(root, id);
    await IOUtils.makeDirectory(directory);
    return directory;
  },
  async checkEnvironment(python = this.config().python) {
    if (this.busy) throw new Error('请等待当前任务完成。');
    this.busy = true; BabelHost.cancelled = false;
    this.setStatus('正在检查 Python 和 BabelDOC…', 0);
    let directory;
    const timer = setTimeout(() => BabelHost.cancel(), 60000);
    try {
      directory = await this.directory();
      const result = await BabelHost.run({python, rootURI: this.rootURI, directory, check: true});
      this.setStatus(`环境可用：BabelDOC ${result.babeldoc} / Python ${result.python}`, 100);
      return result;
    } catch (error) { this.setStatus(error.message); throw error; }
    finally {
      clearTimeout(timer);
      this.busy = false;
      if (directory) await IOUtils.remove(directory, {recursive: true, ignoreAbsent: true}).catch(() => {});
      this.setStatus(this.status);
    }
  },
  async prepareResources(python = this.config().python) {
    if (this.busy) throw new Error('请先等待或取消当前任务。');
    this.busy = true; BabelHost.cancelled = false;
    this.setStatus('正在准备本地资源；不会调用翻译 API…', 0);
    let directory;
    try {
      directory = await this.directory();
      await BabelHost.run({python, rootURI: this.rootURI, directory, prepare: true,
        onProgress: ({stage, percent}) => this.setStatus(stage, percent)});
      this.setStatus('本地资源已就绪，后续论文将复用这些资源。', 100);
    } catch (error) { this.setStatus(error.message); throw error; }
    finally {
      this.busy = false;
      if (directory) await IOUtils.remove(directory, {recursive: true, ignoreAbsent: true}).catch(() => {});
      this.setStatus(this.status);
    }
  },
  async checkCodex(config, login = false) {
    if (this.busy) throw new Error('请等待当前任务完成。');
    this.busy = true; BabelHost.cancelled = false;
    this.setStatus(login ? '请在官方浏览器页面完成 Codex 的 ChatGPT 登录…' : '正在检查 Codex 的 ChatGPT 登录状态…', 0);
    let directory;
    try {
      directory = await this.directory();
      const result = await BabelHost.run({python: config.python, rootURI: this.rootURI, directory,
        checkCodex: !login, loginCodex: login, request: {codexBinary: config.codexBinary, codexModel: config.codexModel, codexEffort: config.codexEffort || 'low'}});
      if (login) { this.codexModels = []; this.codexCatalogKey = ''; }
      this.setStatus('Codex 已通过 ChatGPT 账户登录；翻译使用账户额度，临时会话不保存聊天记录。', 100);
      return result;
    } catch (error) { this.setStatus(error.message); throw error; }
    finally {
      this.busy = false;
      if (directory) await IOUtils.remove(directory, {recursive: true, ignoreAbsent: true}).catch(() => {});
      this.setStatus(this.status);
    }
  },
  async listCodexModels(config, refresh = false) {
    const key = `${config.python}\n${config.codexBinary || ''}`;
    if (!refresh && this.codexCatalogKey === key && this.codexModels.length) return this.codexModels;
    if (this.busy) throw new Error('请等待当前任务完成，再刷新模型列表。');
    this.busy = true; BabelHost.cancelled = false;
    this.setStatus('正在读取当前 Codex 提供的模型列表…', 0);
    let directory;
    try {
      directory = await this.directory();
      const result = await BabelHost.run({python: config.python, rootURI: this.rootURI, directory,
        listCodex: true, request: {codexBinary: config.codexBinary || ''}});
      if (!Array.isArray(result.models) || !result.models.length) throw new Error('Codex 没有返回可选择的模型。');
      this.codexModels = result.models; this.codexCatalogKey = key;
      this.setStatus(`已读取 Codex 提供的 ${result.models.length} 个模型。`, 100);
      return result.models;
    } catch (error) { this.setStatus(error.message); throw error; }
    finally {
      this.busy = false;
      if (directory) await IOUtils.remove(directory, {recursive: true, ignoreAbsent: true}).catch(() => {});
      this.setStatus(this.status);
    }
  },
  async translate(win) {
    if (this.busy || this.stopped) return;
    // Capture the source and parent before any asynchronous translation starts.
    const selected = this.selected(win);
    let directory, keepFiles = false, alertMessage = '';
    this.busy = true; BabelHost.cancelled = false; this.percent = 0;
    this.setStatus('正在检查原文 PDF…');
    try {
      let config;
      try { config = BabelCore.validate(this.config()); }
      catch (error) { this.openSettings(win); throw error; }
      const target = await BabelCore.target(selected, this.itemAPI());
      let candidate = target.candidates[0];
      if (target.candidates.length > 1) {
        const index = {value: 0};
        const confirmed = Services.prompt.select(win, '选择原文 PDF', '此条目有多个 PDF，请选择需要翻译的文件：',
          target.candidates.map(c => c.item.getField('title') || PathUtils.filename(c.path)), index);
        if (!confirmed) { this.setStatus('已取消选择'); return; }
        candidate = target.candidates[index.value];
      }
      const parentID = target.parent.id, sourceID = candidate.item.key;
      this.setStatus('请选择本次翻译页码；留空翻译全文。');
      const pages = await this.choosePages(win, candidate.item.getField('title') || PathUtils.filename(candidate.path));
      if (pages === null || BabelHost.cancelled || this.stopped) { this.setStatus('已取消翻译'); return; }
      config.pages = BabelCore.validatePages(pages);
      directory = await this.directory();
      this.openSettings(win);
      this.setStatus('正在启动 BabelDOC；首次运行可能需要下载模型和字体…', 0);
      const result = await BabelHost.run({python: config.python, rootURI: this.rootURI, directory,
        request: {...config, apiKey: config.backend === 'codex' ? '' : this.key(config.baseURL), input: candidate.path, output: directory},
        onProgress: ({stage, percent}) => this.setStatus(stage, percent)});
      if (BabelHost.cancelled || this.stopped) throw new Error('任务已取消。');
      this.importing = true; this.setStatus('正在将译文和双语 PDF 添加到原条目…', 100);
      try {
        const prefix = directory + (Zotero.isWin ? '\\' : '/');
        await BabelCore.commit({parentID, sourceID, paths: result, langOut: config.langOut, warnings: result.warnings}, {
          validatePDF: async path => {
            if (!path.startsWith(prefix) || path.slice(prefix.length).split(/[\\/]/).includes('..')) throw new Error('输出路径不在当前任务目录内。');
            const bytes = await IOUtils.read(path, {maxBytes: 5});
            if (String.fromCharCode(...bytes) !== '%PDF-') throw new Error('输出文件不是 PDF。');
          },
          assertParent: async id => {
            const parent = await Zotero.Items.getAsync(id);
            if (!parent || parent.deleted || !parent.isRegularItem() || !this.itemAPI().editable(parent.libraryID)) {
              throw new Error('原文献条目已删除或文库不再允许添加附件。');
            }
          },
          importFile: (file, parentItemID, title) => Zotero.Attachments.importFromFile({file, parentItemID, title, contentType: 'application/pdf'}),
          erase: item => item.eraseTx()
        });
      } catch (error) { keepFiles = true; throw new Error(`${error.message}\n已生成的 PDF 保留在：${directory}`); }
      const performance = result.warnings?.performance;
      if (performance) {
        await IOUtils.writeJSON(PathUtils.join(Zotero.Profile.dir, 'babeldoc-local', 'last-translation-performance.json'), {
          completedAt: new Date().toISOString(), backend: config.backend,
          model: config.backend === 'codex' ? config.codexModel : config.model,
          ...(config.backend === 'codex' ? {effort: config.codexEffort, concurrency: config.codexConcurrency} : {}),
          performance
        }).catch(() => {});
      }
      this.setStatus(result.warnings?.hasWarnings
        ? `翻译已保存：译文和双语 PDF 已添加到原条目（需检查；检测到 ${Number(result.warnings.untranslatedParagraphs) || 0} 个未译段落，保留原文）。请查看附件备注和 PDF 批注。`
        : '翻译完成：译文和双语 PDF 已添加到原条目。', 100);
      if (performance) {
        const seconds = Math.max(0, Math.round(Number(performance.elapsedSeconds) || 0));
        this.setStatus(`${this.status} 用时 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒；模型调用 ${Number(performance.modelCalls) || 0} 次。`, 100);
      }
    } catch (error) {
      this.setStatus(error.message || String(error));
      if (directory && !keepFiles && !BabelHost.cancelled) {
        const files = await IOUtils.getChildren(directory).catch(() => []);
        if (files.some(path => /\.(mono|dual)\.pdf$/i.test(path))) {
          keepFiles = true;
          this.setStatus(`${this.status}\n已生成的 PDF 保留在：${directory}`);
        }
      }
      if (!this.stopped && !BabelHost.cancelled) alertMessage = this.status;
    } finally {
      this.importing = false; this.busy = false;
      if (directory && !keepFiles) await IOUtils.remove(directory, {recursive: true, ignoreAbsent: true}).catch(() => {});
      this.setStatus(this.status);
    }
    if (alertMessage) this.alert(win, alertMessage);
  },
  cancel() {
    if (this.importing) return;
    this.translationWindow?.close();
    BabelHost.cancel(); this.setStatus('正在取消任务…');
  },
  async shutdown() {
    this.stopped = true; BabelHost.cancel();
    if (BabelHost.process) await BabelHost.process.wait().catch(() => {});
    this.translationWindow?.close(); this.settingsWindow?.close(); this.listeners.clear();
    for (const win of this.windows.keys()) this.detachWindow(win);
  }
};
