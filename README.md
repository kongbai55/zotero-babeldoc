# BabelDOC for Zotero

English | [简体中文](README.zh-CN.md)

## 0.2.7: Concurrent Codex translation and timing

Codex now defaults to two concurrent batches. Set **同时翻译批次数** (Concurrent batches) to 1–4; use 1 for sequential processing when connections or rate limits are troublesome. This overlaps waiting but still uses a separate official ephemeral CLI invocation for each batch. It does not reuse a conversation or background process, reduce the text to translate, guarantee proportional speedup, or guarantee lower subscription usage. Valid translation caches remain reusable.

The requests-per-second setting applies only to API mode and is disabled in Codex mode. On completion, the UI shows elapsed time and actual model calls. Reports include resource and engine time, cumulative/max call time, and numeric CLI token usage when available. Overlapping call durations may sum to more than elapsed time; token figures cannot be converted directly to a weekly allowance percentage. These performance reports contain no prompts, translations, API keys, or login tokens. The latest successfully saved task is retained at `babeldoc-local/last-translation-performance.json` in the Zotero profile after job cleanup; job directories still contain `translation-report.json`.

## 0.2.6: Choose pages for each paper

Click Translate, enter pages for this task (for example `3-8` or `1-5,8`), then click **开始翻译** (Start translation). Leave the field blank to translate the entire paper. Pages are no longer stored in global settings or carried over to the next paper. Legacy saved page ranges are ignored. Cancelling or closing the page dialog starts no translation.

## 0.2.5: Windows long-path attachment import

Long paper titles could produce an existing PDF whose complete job path exceeded Windows' legacy 260-character limit. Zotero's file-reading and attachment-copy code could report `NS_ERROR_FILE_NOT_FOUND` even though BabelDOC had generated the file. Before passing paths to Zotero, the Python runner now validates the outputs and normalizes generated files to `export/translated.pdf` and `export/bilingual.pdf` inside the same job. It verifies both copies before removing the old generated filenames. Attachment display titles and review notes are unchanged; the original paper PDF is unaffected.

This is an import failure, so repeating translation is unnecessary when the error reports a retained folder. The existing PDFs can be copied to a short folder and added as child attachments of the original item. Failed imports continue to retain the generated PDFs for recovery.

## 0.2.4: save partial translations and retain original text

An isolated empty, truncated, or failed response no longer blocks the rest of the paper or discards the generated PDFs. BabelDOC keeps the original text where translation fails; valid translated paragraphs continue to be used. A partial batch retains its valid results and retries blank items through BabelDOC's per-paragraph fallback. Invalid responses and partial batches are not saved as successful translation-cache entries.

Both PDFs are still attached to the original item. Attachment titles show **含未译原文** (contains untranslated source) or **需检查** (needs review); tags and attachment notes identify detected untranslated paragraphs, source pages, and safe error categories. PDF comments mark affected pages without changing the body layout. Counts come from BabelDOC's translation tracking, not a guarantee that every semantic omission is detected.

Quota/authentication/model-access failures pause further remote calls. Three consecutive connection, timeout, or unclassified call failures also pause them; existing cache entries remain usable and remaining paragraphs retain their original text. This still produces partial PDFs rather than silently switching to a paid API. Recoverable CLI reconnection notices followed by a successful final answer are accepted. Progress shows completed model calls and current wait time.

Only genuine PDF-generation or file-import failures prevent normal attachment import. Already generated PDF files are retained and their folder is shown for recovery. Error dialogs are displayed after releasing the busy state. Install the update and restart Zotero; an already running task continues with its original version. A new translation reuses valid cached results, but retrying missing content can consume model allowance.

## 0.2.3: choose models from the current Codex catalog

Select **Codex login account** to load the model list from the installed official Codex CLI and its current ChatGPT login. Click a model card instead of typing a name. Use **Refresh model list** (`刷新模型列表`) after switching accounts or updating the CLI. The list is not hardcoded; it reflects the catalog the current CLI provides, and service availability can still change at translation time.

Only the selected model's supported reasoning efforts are shown. If the previous effort is unsupported, the plugin chooses low when available, otherwise the model's reported default. A new configuration starts with the catalog's default model; an unavailable saved model requires a new selection. Click **Save settings** to apply both choices. The plugin has its own selections; changing the Codex desktop app's model does not update the plugin.

The catalog is read through the official [app-server `model/list` interface](https://learn.chatgpt.com/docs/app-server), without starting a chat or requesting a translation. If loading fails, complete ChatGPT login, check connectivity and the CLI version, then refresh. No manual model entry or bundled fallback list is used. Existing API settings and keys are preserved.

Suggested starting points for paper translation:

| Priority | Model | Effort |
| --- | --- | --- |
| Technical terminology, wording, and fidelity | `gpt-6.1-sol` | `low` |
| Speed and lower allowance usage | `gpt-6-luna` | `low` |
| Difficult sentences with poor results at low effort | `gpt-6.1-sol` | `medium` |

These are workflow recommendations, not a completed translation benchmark. Compare the same two representative pages for terminology, omissions, formula markers, and meaning before increasing effort. Higher effort generally increases latency and reasoning usage; it does not fix PDF layout or background-layer defects. Starting whole-paper translation at xhigh or max is usually unnecessary.

Availability depends on your account, CLI, and service. Changing the model or raising effort can require new model calls rather than reuse results from different settings. Existing low-effort cache entries remain compatible. Calls consume your Codex allowance and never automatically fall back to a paid API.

References: [official model guide](https://learn.chatgpt.com/docs/models), [official model/effort guidance](https://developers.openai.com/api/docs/guides/deployment-checklist).

## 0.2.1: backend selector display fix

The translation-method dropdown is now two directly clickable radio options: **API / local model endpoint** and **Codex login account**. This avoids a native popup rendering problem in Zotero that could overlap labels and prevent selection. Existing API settings and credentials are preserved. Switching, saving, and reopening settings have been verified in an isolated Zotero window. After installing the update, restart Zotero and reopen the settings window.

Run BabelDOC locally and save both a **translated PDF** and a **bilingual PDF** as child attachments of the original Zotero item. English-to-Chinese translation and side-by-side bilingual pages are the defaults; alternating pages are also available.

## 0.2.0: API and signed-in Codex backends

The translation-method selector keeps the original API/local-model endpoint option and adds **Codex with a signed-in ChatGPT account**. Switching preserves the API endpoint, model, and saved key. There is no automatic fallback between providers.

To use Codex:

1. Install the official CLI if needed: `npm install -g @openai/codex`.
2. Select `Codex 登录账户`. Leave the executable path blank for discovery, or enter the native `codex.exe` path.
3. Click `登录 Codex（官方浏览器）` and complete the official ChatGPT sign-in yourself in the browser. If already signed in, click `检查 Codex 登录`. Running `codex login` yourself also works.
4. Select a model from the loaded catalog and a supported reasoning effort, then save settings and translate. If needed, click `刷新模型列表`. An OpenAI API key is not needed. The plugin checks that authentication uses ChatGPT and rejects API-key authentication.

Background translation uses official `codex exec --ephemeral`, without creating a persistent local chat session. Prompts and responses travel through pipes. Calls **still consume your Codex allowance** and are neither free nor unlimited; long papers can use substantial allowance. Ephemeral local execution **does not imply zero server-side retention**. Paper text is sent to OpenAI under your account/workspace data policies.

Codex mode processes two batches concurrently by default (configurable from 1–4) and disables automatic glossary extraction to reduce extra calls. Insufficient allowance, missing login, network failures, or unavailable models stop the task rather than silently switching to a paid API. The plugin does not read, copy, or export login tokens. Child Codex processes are stopped with cancelled jobs; Windows has been verified, while other platforms remain untested.

The existing API mode remains available. For the official DeepSeek endpoint, the plugin disables thinking mode and raises the translation output limit to 8192 tokens. This is a maximum, not a fixed per-request consumption. Other compatible endpoints do not receive DeepSeek-specific options.

### Empty translations and covered text

Older versions could cache an empty model response and treat it as successful, producing blank paragraphs. Version 0.2.4 ignores invalid cache entries, keeps original text where translation fails, and saves both PDFs with review labels and page comments. It replaces the earlier all-or-nothing export guard. Valid cache entries for the same backend remain reusable.

Pastel background shapes near text lines are painted before text, addressing reproduced cases where highlight blocks covered translated glyphs. This targets the observed bug; it does not guarantee perfect layout or semantic translation for every complex PDF.

**Existing incomplete PDFs are not automatically repaired: retranslate them with the new version.** Test affected pages first before running the full paper. Upgrading does not automatically send more model requests, charge an old job, or overwrite the original PDF.

References: [official non-interactive Codex usage](https://learn.chatgpt.com/docs/non-interactive-mode), [official authentication guide](https://learn.chatgpt.com/docs/auth).

## What's new in 0.1.1

- Separate engine loading, resource preparation, and paper translation. Resource preparation shows cached file counts, size, and elapsed time instead of appearing stuck at 0%.
- Add **Prepare resources** (`准备资源`), which needs no paper, model name, or API key and does not call a translation API.
- Stop startup/resource preparation after ten minutes with a network/proxy troubleshooting message. Downloaded resources remain cached for retries.

The current plugin interface is in Simplified Chinese. Chinese button labels are included below.

## Where processing happens

PDF parsing, layout analysis, typesetting, and PDF generation run in a local Python process. The plugin does not use BabelDOC's online translation server.

Text translation uses your configured OpenAI-compatible Chat Completions endpoint. A cloud endpoint receives the text to translate. A local translation model can also keep text processing on your computer; the model service's own network behavior depends on its configuration. Offline use requires cached resources and a separately installed local translation model.

The default endpoint is `http://127.0.0.1:11434/v1`; enter your actual model name. The plugin does not bundle or install an LLM or purchase API access. API keys are stored in Zotero's password manager, separately for each endpoint, and passed to Python through standard input rather than process arguments, ordinary settings files, or plugin logs.

Output files use Zotero's standard attachment import API and follow your Zotero file-sync settings. The original PDF is not modified.

## Why download a layout model and fonts?

BabelDOC needs local resources to understand PDF structure and rebuild a readable translated document. These resources are separate from the LLM that translates the text.

| Resource | Purpose | Translates text? |
| --- | --- | --- |
| Layout-analysis model | Detects body text, headings, formulas, figures, and other regions to help preserve layout | No |
| Font library | Provides glyphs needed by the target language, including Chinese, to reduce missing characters and boxes | No |
| Character maps (CMap) | Help decode text stored inside PDF files | No |
| Tokenizer cache | Supports internal tokenization and text-length handling | No |

**Changing papers does not require downloading a new model and font set.** Papers share the same resources. BabelDOC uses the general layout model to analyze each paper independently and selects fonts according to the target language and style. It does not train or download a paper-specific model.

Resources normally download once and are reused. Further downloads are needed only if the cache is cleared, files are damaged, or an upgrade requires new resources. Cancelling a task or closing Zotero does not remove this cache. Our BabelDOC 0.6.4 verification used 182 resource files totaling approximately 336 MB, excluding the Python environment and translation LLM. Other versions may differ.

The default cache is `.cache/babeldoc` under the current operating-system user's home directory. Windows example: `C:\Users\your-name\.cache\babeldoc`. Its subdirectories include `models`, `fonts`, `cmap`, and `tiktoken`. This shared engine cache is separate from each paper's attachments.

### Why can the first run look stuck on loading?

First use downloads fonts for multiple languages and checks the model and other assets. Its duration depends on network connectivity and proxy settings. The older plugin displayed 0% while waiting for paper-translation events even when downloads were progressing in the background.

Version 0.1.1 shows cached file counts, size, and elapsed time. You can also use **Prepare resources** (`准备资源`) before translating. If preparation fails or exceeds ten minutes, check the network/proxy and retry; complete cached resources are reused. Resource progress reaching 100% completes that stage, after which PDF parsing and text translation begin. The progress bar can restart when stages change.

### What stays local with DeepSeek?

With a DeepSeek cloud endpoint, PDF parsing, layout analysis, and typesetting still run locally, while the text to translate is sent to DeepSeek. Local layout/font resources do not make cloud translation local. Requests do not go through BabelDOC's online translation server. Offline processing requires a separately running local translation model and previously prepared resources.

## Install

Targets Zotero 7–10; verified on Windows with Zotero 10.0.6. Requires Python 3.10–3.13, preferably 3.12. macOS/Linux setup scripts are provided but have not been tested on those platforms.

1. Extract `zotero-babeldoc-0.2.7-install.zip`.
2. In Zotero, choose **Tools → Plugins → gear menu → Install Plugin From File** and select `zotero-babeldoc-0.2.7.xpi`.
3. Install the BabelDOC environment from the extracted directory.

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
# Optional: prepare local resources too
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Warmup
```

macOS / Linux:

```sh
sh scripts/setup.sh
# Optional: prepare local resources too
sh scripts/setup.sh --warmup
```

The scripts create an isolated `.venv` and install `BabelDOC==0.6.4` with compatibility constraints from `requirements.txt`, without changing system Python packages. Linux needs Python's venv support. Keep this environment in a permanent location and update the configured Python path if you move it. Initial installation and resource downloads need access to the package index and BabelDOC asset sources.

4. Open **Tools → BabelDOC · 论文翻译 → 设置与任务进度**. Enter the Python path printed by the script, endpoint, model name, and API key if needed. Click **Save settings** (`保存设置`) and **Check environment** (`检查环境`). The environment check imports Python/BabelDOC; it does not call or validate your model endpoint.
5. For first use, click **Prepare resources** (`准备资源`). This downloads and verifies local assets without sending a paper or calling the translation API. Later papers reuse the cache.

### Local translation models

Download and run a translation-capable model in a service with an OpenAI-compatible endpoint, then enter its actual model name. For example, a compatible Ollama service can use `http://127.0.0.1:11434/v1`, with the API key left blank.

BabelDOC uses structured prompts and formula markers. Model quality, context capacity, and instruction following affect results; small models may not preserve those markers. Installing BabelDOC does not install the translation LLM.

### Cloud translation models

Enter the provider's OpenAI-compatible endpoint, model name, and API key. Text is sent to that provider. The plugin does not automatically fall back to another service.

## Translate a paper

1. Select **one paper** or **one PDF attachment under a paper**, or open its PDF reader tab in Zotero's main window.
2. Choose **Tools → BabelDOC → 翻译当前论文**, or the library context-menu entry **BabelDOC：翻译 PDF 并添加双语附件**.
3. Enter the page range in the translation dialog, or leave it blank for the whole paper, then click **开始翻译** (Start translation).
4. View progress or cancel in the settings/progress window. Closing this window does not stop the task; reopen it from Tools.
5. On success, the original item receives two child attachments, named `译文 · zh · BabelDOC` and `双语对照 · zh · BabelDOC` for Chinese output.

Selecting a paper excludes its previously generated BabelDOC attachments from source selection. With multiple source PDFs, you can choose one. The PDF must exist on disk; download missing Zotero attachments first. Place standalone PDFs under a regular bibliographic item before translating. Each run adds a new pair without overwriting previous translations.

The task captures its source and parent at startup, so switching papers does not change the destination. Only one task runs at a time. Cancellation is disabled during attachment import to keep the pair together. If the second import fails, the plugin attempts to remove attachments created by that run and keeps generated PDFs in the reported task directory for recovery. Successful or cancelled tasks remove temporary working files; shared BabelDOC resources and translation caches remain.

Choose pages in the dialog that opens each time you click Translate. Pages use the PDF file order, starting at 1 and counting the cover; ranges include both endpoints. Enter ranges such as `1-5,8`, or leave blank to translate the whole paper. The selection applies only to that task and is never saved in global settings. Unselected pages follow BabelDOC's default retention behavior. Scanned documents, encrypted PDFs, and complex layouts may fail; errors appear in the plugin.

## Develop

From the source repository or source archive:

```sh
npm test
npm run build
```

There are no npm dependencies. `addon/` contains the XPI sources. `worker.py` uses BabelDOC's asynchronous Python API. `scripts/build.py` produces the XPI, source archive, installation bundle, and checksums. See [TESTING.md](TESTING.md) for details. Automated tests do not call paid models or access a personal library. Model-specific translation quality still needs validation with your chosen endpoint.

References: [BabelDOC](https://github.com/funstory-ai/BabelDOC), [Zotero plugin development](https://www.zotero.org/support/dev/zotero_7_for_developers). Plugin wrapper code is MIT-licensed. BabelDOC is installed separately under AGPL-3.0; its code is not bundled in the XPI. See the upstream project for its license.
#   z o t e r o - b a b e l d o c  
 