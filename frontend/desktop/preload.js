/*
Input: Desktop IPC requests from the page
Output: Safe project and whiteboard methods in the page
Processing: Exposes limited Electron actions through the context bridge
Changed by: Aarav
Date: 2026-11-14
*/

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('plovaneDesktop', {
  selectProject: () => ipcRenderer.invoke('select-project'),
  connectProject: project => ipcRenderer.invoke('connect-project', project),
  openWhiteboard: url => ipcRenderer.invoke('open-whiteboard', url),
  goHome: () => ipcRenderer.invoke('go-home'),
});
