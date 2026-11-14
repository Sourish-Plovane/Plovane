const chooseButton = document.querySelector('#choose-project');
const openButton = document.querySelector('#open-whiteboard');
const pathLabel = document.querySelector('#project-path');
const status = document.querySelector('#status');
const statusText = document.querySelector('#status-text');
let whiteboardUrl;

function showStatus(text, kind = '') {
  statusText.textContent = text;
  status.className = `status ${kind}`;
}

chooseButton.addEventListener('click', async () => {
  const project = await window.plovaneDesktop.selectProject();
  if (!project) return;
  pathLabel.textContent = project;
  chooseButton.disabled = true;
  openButton.hidden = true;
  showStatus('Starting whiteboard server…');
  try {
    const connection = await window.plovaneDesktop.connectProject(project);
    whiteboardUrl = connection.url;
    showStatus('Connected', 'connected');
    openButton.hidden = false;
  } catch (error) {
    showStatus(error.message, 'error');
  } finally {
    chooseButton.disabled = false;
  }
});

openButton.addEventListener('click', () => {
  if (whiteboardUrl) window.plovaneDesktop.openWhiteboard(whiteboardUrl);
});
