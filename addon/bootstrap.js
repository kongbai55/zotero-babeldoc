var chromeHandle, babelScope;
const PLUGIN_ID = 'babeldoc-local@zotero-babeldoc.local';
function install() {}
function uninstall() {}
async function startup({rootURI}) {
  await Zotero.initializationPromise;
  const {AddonManager} = ChromeUtils.importESModule('resource://gre/modules/AddonManager.sys.mjs');
  const addon = await AddonManager.getAddonByID(PLUGIN_ID);
  if (addon) addon.applyBackgroundUpdates = AddonManager.AUTOUPDATE_DISABLE;
  const aom = Components.classes['@mozilla.org/addons/addon-manager-startup;1']
    .getService(Components.interfaces.amIAddonManagerStartup);
  chromeHandle = aom.registerChrome(Services.io.newURI(rootURI + 'manifest.json'), [
    ['content', 'babeldoc-local', rootURI + 'content/']
  ]);
  babelScope = {Zotero, Services, ChromeUtils, Components, IOUtils, PathUtils, URL, setTimeout, clearTimeout};
  for (const file of ['core.js', 'host.js', 'controller.js']) {
    Services.scriptloader.loadSubScript(rootURI + 'content/' + file, babelScope, 'UTF-8');
  }
  babelScope.BabelDOC.rootURI = rootURI;
  for (const window of Zotero.getMainWindows()) onMainWindowLoad({window});
}
function onMainWindowLoad({window}) { babelScope?.BabelDOC.attachWindow(window); }
function onMainWindowUnload({window}) { babelScope?.BabelDOC.detachWindow(window); }
async function shutdown() {
  await babelScope?.BabelDOC.shutdown();
  chromeHandle?.destruct();
  chromeHandle = null;
  babelScope = null;
}
