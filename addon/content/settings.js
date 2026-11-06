/* Settings window receives only its own controller, never page content. */
window.addEventListener('DOMContentLoaded', () => {
  const controller = window.arguments[0].controller;
  const $ = id => document.getElementById(id);
  const fields = ['python', 'backend', 'codexBinary', 'codexModel', 'codexEffort', 'codexConcurrency', 'baseURL', 'model', 'langIn', 'langOut', 'qps', 'alternating', 'sendTemperature'];
  const config = controller.config();
  let models = [], loadingModels = false;
  for (const name of fields) {
    const input = $(name);
    if (input.type === 'checkbox') input.checked = !!config[name];
    else input.value = config[name];
  }
  const loadKey = () => {
    try { $('apiKey').value = controller.key($('baseURL').value.trim()); }
    catch (_) { $('apiKey').value = ''; }
  };
  const destination = () => {
    const selected = models.find(model => model.id === $('codexModel').value);
    for (const input of document.querySelectorAll('input[name="codexReasoningEffort"]')) {
      const supported = !!selected && selected.efforts.includes(input.value);
      input.disabled = !supported;
      input.parentElement.hidden = !supported;
      input.checked = input.value === $('codexEffort').value;
      input.parentElement.dataset.selected = String(input.checked);
    }
    for (const input of document.querySelectorAll('input[name="codexModelChoice"]')) {
      input.checked = input.value === $('codexModel').value;
      input.parentElement.dataset.selected = String(input.checked);
    }
    for (const [id, value] of [['backendApi', 'api'], ['backendCodex', 'codex']]) {
      $(id).checked = $('backend').value === value;
      $(id + 'Choice').dataset.selected = String($(id).checked);
    }
    $('apiSettings').hidden = $('backend').value === 'codex';
    $('codexSettings').hidden = $('backend').value !== 'codex';
    $('qps').disabled = $('backend').value === 'codex';
    $('qpsHint').textContent = $('backend').value === 'codex'
      ? 'Codex 使用上方“同时翻译批次数”；本项只用于 API 方式。' : '每秒请求数用于 API 方式。';
    for (const name of ['baseURL', 'model', 'apiKey']) $(name).disabled = $('backend').value === 'codex';
    try {
      const url = new URL($('baseURL').value);
      $('destination').textContent = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
        ? '当前指向本机模型接口。'
        : `待翻译文本将发送到：${url.hostname}`;
    } catch (_) { $('destination').textContent = '请填写模型服务的完整接口地址（通常以 /v1 结尾）。'; }
  };
  loadKey(); destination();
  $('baseURL').addEventListener('change', () => { loadKey(); destination(); });
  $('backend').addEventListener('change', destination);
  for (const [id, value] of [['backendApi', 'api'], ['backendCodex', 'codex']]) {
    $(id).addEventListener('change', () => {
      if ($(id).checked) {
        $('backend').value = value; destination();
        if (value === 'codex' && !models.length) void loadModels();
      }
    });
  }
  for (const input of document.querySelectorAll('input[name="codexReasoningEffort"]')) {
    input.addEventListener('change', () => {
      if (input.checked) { $('codexEffort').value = input.value; destination(); }
    });
  }
  const update = () => {
    $('status').textContent = controller.status;
    $('progress').value = controller.percent;
    $('cancel').disabled = !controller.busy || controller.importing;
    $('check').disabled = controller.busy;
    $('prepare').disabled = controller.busy;
    $('checkCodex').disabled = controller.busy;
    $('loginCodex').disabled = controller.busy;
    $('refreshModels').disabled = controller.busy || loadingModels;
  };
  controller.listeners.add(update); update();
  window.addEventListener('unload', () => controller.listeners.delete(update), {once: true});
  $('cancel').addEventListener('click', () => controller.cancel());
  $('close').addEventListener('click', () => window.close());
  const codexConfig = () => ({python: $('python').value.trim(), codexBinary: $('codexBinary').value.trim()});
  const selectModel = model => {
    $('codexModel').value = model.id;
    if (!model.efforts.includes($('codexEffort').value)) {
      $('codexEffort').value = model.efforts.includes('low') ? 'low' : model.defaultEffort;
    }
    destination();
  };
  async function loadModels() {
    if (loadingModels) return;
    const requested = codexConfig();
    let reload = false;
    loadingModels = true;
    $('codexModels').setAttribute('aria-busy', 'true');
    $('codexModelsStatus').dataset.error = 'false';
    $('codexModelsStatus').textContent = '正在读取当前 Codex 的模型列表…';
    update();
    try {
      const catalog = await controller.listCodexModels(requested, true);
      if (window.closed) return;
      if (JSON.stringify(requested) !== JSON.stringify(codexConfig())) { reload = true; return; }
      models = catalog;
      const previous = $('codexModel').value;
      $('codexModels').replaceChildren();
      const element = tag => document.createElementNS('http://www.w3.org/1999/xhtml', tag);
      for (const model of models) {
        const label = element('label'); label.className = 'backend-choice';
        const radio = element('input');
        radio.type = 'radio'; radio.name = 'codexModelChoice'; radio.value = model.id;
        const text = element('span');
        const title = element('strong');
        title.textContent = model.name + (model.isDefault ? ' · Codex 默认' : '');
        const id = element('small'); id.className = 'model-id'; id.textContent = model.id;
        text.append(title, id);
        if (model.description) {
          const description = element('small'); description.textContent = model.description;
          text.append(description);
        }
        label.append(radio, text); $('codexModels').append(label);
        radio.addEventListener('change', () => { if (radio.checked) selectModel(model); });
      }
      const selected = models.find(model => model.id === previous);
      if (selected) selectModel(selected);
      else if (!previous) selectModel(models.find(model => model.isDefault) || models[0]);
      else { $('codexModel').value = ''; destination(); }
      $('codexModelsStatus').textContent = `${models.length} 个模型可选。` +
        (previous && !selected ? '原选模型已不在当前列表中，请重新选择。' : '选择后点击“保存设置”。');
    } catch (error) {
      if (!window.closed) {
        models = []; $('codexModels').replaceChildren(); destination();
        $('codexModelsStatus').dataset.error = 'true';
        $('codexModelsStatus').textContent = error.message + ' 请完成 Codex 登录后刷新列表。';
      }
    } finally {
      loadingModels = false;
      if (!window.closed) { $('codexModels').setAttribute('aria-busy', 'false'); update(); }
      if (reload && !window.closed) void loadModels();
    }
  }
  $('refreshModels').addEventListener('click', () => void loadModels());
  for (const name of ['python', 'codexBinary']) {
    $(name).addEventListener('change', () => {
      models = []; $('codexModels').replaceChildren(); destination();
      if ($('backend').value === 'codex') void loadModels();
    });
  }
  const save = async () => {
    if ($('backend').value === 'codex') {
      const selected = models.find(model => model.id === $('codexModel').value);
      if (loadingModels) throw new Error('请等待模型列表读取完成。');
      if (!selected || !selected.efforts.includes($('codexEffort').value)) throw new Error('请刷新模型列表并选择模型和推理强度。');
    }
    const values = {};
    for (const name of fields) values[name] = $(name).type === 'checkbox' ? $(name).checked : $(name).value;
    await controller.save(values, $('apiKey').value.trim());
    $('feedback').textContent = '已保存';
  };
  $('settings').addEventListener('submit', async event => {
    event.preventDefault();
    try { await save(); } catch (error) { $('feedback').textContent = error.message; }
  });
  $('check').addEventListener('click', async () => {
    try { await controller.checkEnvironment($('python').value.trim()); }
    catch (error) { $('feedback').textContent = error.message; }
  });
  $('prepare').addEventListener('click', async () => {
    try { await controller.prepareResources($('python').value.trim()); }
    catch (error) { $('feedback').textContent = error.message; }
  });
  $('checkCodex').addEventListener('click', async () => {
    try { await controller.checkCodex(codexConfig()); await loadModels(); }
    catch (error) { $('feedback').textContent = error.message; }
  });
  $('loginCodex').addEventListener('click', async () => {
    try { await controller.checkCodex(codexConfig(), true); await loadModels(); }
    catch (error) { $('feedback').textContent = error.message; }
  });
  $('browse').addEventListener('click', async () => {
    try {
      const {FilePicker} = ChromeUtils.importESModule('chrome://zotero/content/modules/filePicker.mjs');
      const picker = new FilePicker();
      picker.init(window, '选择 Python 可执行文件', picker.modeOpen);
      picker.appendFilters(picker.filterApps);
      if (await picker.show() === picker.returnOK) {
        $('python').value = picker.file;
        $('python').dispatchEvent(new Event('change'));
      }
    } catch (error) { $('feedback').textContent = '请直接填写 Python 可执行文件的完整路径。'; }
  });
  if ($('backend').value === 'codex') void loadModels();
});
