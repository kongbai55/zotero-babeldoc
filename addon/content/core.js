/* Host-independent item selection and attachment commit logic. */
var BabelCore = {
  defaults: {python: '', backend: 'api', codexBinary: '', codexModel: '', codexEffort: 'low', codexConcurrency: 2, baseURL: 'http://127.0.0.1:11434/v1', model: '',
    langIn: 'en', langOut: 'zh', qps: 2, alternating: false, sendTemperature: false},

  validatePages(pages) {
    const value = String(pages || '').trim();
    if (value && !/^\s*(?:\d+|\d+-\d*|-\d+)(?:\s*,\s*(?:\d+|\d+-\d*|-\d+))*\s*$/.test(value)) {
      throw new Error('页码格式例如 1-5,8；留空翻译全文。');
    }
    if (value && value.split(',').some(part => {
      const [a, b] = part.trim().split('-');
      return (a !== '' && (!Number.isSafeInteger(Number(a)) || Number(a) < 1))
        || (b !== undefined && b !== '' && (!Number.isSafeInteger(Number(b)) || Number(b) < 1))
        || (a && b && Number(a) > Number(b));
    })) throw new Error('页码必须从 1 开始，范围的结束页不能小于起始页。');
    return value;
  },

  validate(config) {
    const c = {...this.defaults, ...config};
    for (const key of ['python', 'baseURL', 'model', 'langIn', 'langOut', 'pages']) c[key] = String(c[key] || '').trim();
    if (!c.python) throw new Error('请选择已安装 BabelDOC 的 Python 可执行文件。');
    if (!['api', 'codex'].includes(c.backend)) throw new Error('请选择 API 或 Codex 翻译方式。');
    c.codexBinary = String(c.codexBinary || '').trim();
    c.codexModel = String(c.codexModel || '').trim();
    if (c.backend === 'codex' && !c.codexModel) throw new Error('请先读取并选择一个 Codex 模型。');
    if (!['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'].includes(c.codexEffort)) throw new Error('请选择有效的 Codex 推理强度。');
    c.codexConcurrency = Number(c.codexConcurrency);
    if (!Number.isInteger(c.codexConcurrency) || c.codexConcurrency < 1 || c.codexConcurrency > 4) throw new Error('Codex 并行批次数需要是 1 到 4 的整数。');
    if (c.backend === 'api' && !c.model) throw new Error('请填写模型名称。');
    let url;
    try { url = new URL(c.baseURL); } catch (_) { throw new Error('模型接口地址无效。'); }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new Error('模型接口需要 HTTP(S) 地址，密钥请填在独立的密钥输入框。');
    }
    c.baseURL = c.baseURL.replace(/\/+$/, '');
    for (const key of ['langIn', 'langOut']) {
      if (!/^[a-zA-Z][a-zA-Z0-9-]{1,19}$/.test(c[key])) throw new Error('请填写有效的语言代码，例如 en、zh、ja。');
    }
    c.pages = this.validatePages(c.pages);
    c.qps = Number(c.qps);
    if (!Number.isInteger(c.qps) || c.qps < 1 || c.qps > 20) throw new Error('每秒请求数需要是 1 到 20 的整数。');
    return c;
  },

  async target(items, api) {
    if (items.length !== 1) throw new Error('请选中一篇论文或它的一个 PDF 附件。');
    const selected = items[0];
    let parent, attachments;
    if (selected.isAttachment()) {
      if (!selected.parentItemID) throw new Error('请先将独立 PDF 放到一个文献条目下。');
      parent = api.get(selected.parentItemID);
      attachments = [selected];
    } else if (selected.isRegularItem()) {
      parent = selected;
      attachments = selected.getAttachments().map(id => api.get(id)).filter(item => !item.deleted);
    } else throw new Error('请选择文献条目或 PDF 附件。');
    if (!parent || parent.deleted || !parent.isRegularItem()) throw new Error('原文献条目不存在。');
    if (!api.editable(parent.libraryID)) throw new Error('该文库不允许编辑条目或添加文件。');
    const candidates = [];
    for (const item of attachments) {
      if (item.attachmentContentType !== 'application/pdf') continue;
      // Generated files are excluded when a paper is selected. Explicit PDF selection remains possible.
      if (selected === parent && item.getTags().some(tag => tag.tag === 'babeldoc:generated')) continue;
      const path = await item.getFilePathAsync();
      if (path && await api.exists(path)) candidates.push({item, path});
    }
    if (!candidates.length) throw new Error('没有找到本机可用的原文 PDF；请先下载附件。');
    return {parent, candidates};
  },

  async commit({parentID, sourceID, paths, langOut, warnings}, api) {
    // Validate the complete pair before touching the library.
    if (!paths.mono || !paths.dual || paths.mono === paths.dual) throw new Error('BabelDOC 没有返回完整的译文和双语 PDF。');
    for (const path of [paths.mono, paths.dual]) await api.validatePDF(path);
    const created = [];
    try {
      for (const [kind, title] of [['mono', `译文 · ${langOut} · BabelDOC`], ['dual', `双语对照 · ${langOut} · BabelDOC`]]) {
        const hasWarnings = !!warnings?.hasWarnings;
        const markedTitle = title + (hasWarnings ? (warnings.untranslatedParagraphs ? ' · 含未译原文' : ' · 需检查') : '');
        await api.assertParent(parentID);
        const item = await api.importFile(paths[kind], parentID, markedTitle);
        created.push(item);
        item.setField('title', markedTitle);
        item.addTag('babeldoc:generated');
        item.addTag(`babeldoc:source:${sourceID}`);
        item.addTag(`babeldoc:language:${langOut}`);
        if (hasWarnings) {
          item.addTag('babeldoc:needs-review');
          if (warnings.untranslatedParagraphs) item.addTag('babeldoc:partial');
          if (typeof item.setNote === 'function') {
            const escape = value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
            const pages = Object.entries(warnings.pages || {}).map(([page, count]) => `${page}（${count} 段）`).join('、');
            const reasons = (warnings.reasons || []).map(reason => escape(reason.message) + `（${Number(reason.count) || 0} 次）`).join('；');
            item.setNote(`<p>BabelDOC：翻译结果需检查。未完成翻译的段落保留原文。</p><p>检测到 ${Number(warnings.untranslatedParagraphs) || 0} 个未译段落；原文页码：${escape(pages || '未能定位，请检查全文')}。</p><p>${reasons}</p>`);
          }
        }
        await item.saveTx();
      }
      return created;
    } catch (error) {
      const rollbackErrors = [];
      for (const item of created.reverse()) {
        try { await api.erase(item); } catch (rollbackError) { rollbackErrors.push(String(rollbackError)); }
      }
      if (rollbackErrors.length) throw new Error(`${error.message}；部分附件回滚失败，请检查原条目下的 BabelDOC 附件。`);
      throw error;
    }
  }
};
if (typeof module !== 'undefined') module.exports = BabelCore;
