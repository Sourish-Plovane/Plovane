const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('plovaneDesktop', {
  selectProject: () => ipcRenderer.invoke('select-project'),
  connectProject: project => ipcRenderer.invoke('connect-project', project),
  openWhiteboard: url => ipcRenderer.invoke('open-whiteboard', url),
  goHome: () => ipcRenderer.invoke('go-home'),
});
