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
  chrome.runtime.onInstalled.addListener(() => {
    console.log('Context Passport V2 installed successfully.');
  });
}
