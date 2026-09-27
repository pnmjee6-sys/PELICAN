/**
 * Context Passport Background Service Worker (Manifest V3)
 */

// Enable opening the side panel when the user clicks the toolbar icon
if (typeof chrome !== 'undefined' && chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((error) => console.error('Error configuring side panel behavior:', error));
}

// Background event listener for installation / update
if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onInstalled) {
  chrome.runtime.onInstalled.addListener((details) => {
    console.log('Context Passport V3 installed successfully.');
    if (details.reason === 'install') {
      chrome.tabs.create({ url: chrome.runtime.getURL('onboarding.html') });
    }
  });
}

// A signed-out in-page action should take the user straight to recovery.
if (typeof chrome !== 'undefined' && chrome.runtime?.onMessage) {
  chrome.runtime.onMessage.addListener((message, sender) => {
    if (message?.type !== 'open-pelican-sign-in') return;
    const openDashboard = () => chrome.tabs.create({ url: chrome.runtime.getURL('dashboard.html') });
    if (sender.tab?.id != null && chrome.sidePanel?.open) {
      chrome.sidePanel.open({ tabId: sender.tab.id }).catch(openDashboard);
    } else {
      openDashboard();
    }
  });
}
