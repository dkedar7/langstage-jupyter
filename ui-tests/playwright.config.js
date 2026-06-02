/**
 * Galata/Playwright config for deepagent-lab UI tests.
 *
 * Inherits Galata's base config and starts a JupyterLab server (via the
 * `start` script) wired to the model-free stub agent. DEEPAGENT_AGENT_SPEC is
 * also set here so it reaches the server process regardless of how it's spawned.
 */
const path = require('path');
const baseConfig = require('@jupyterlab/galata/lib/playwright-config');

module.exports = {
  ...baseConfig,
  timeout: 120 * 1000,
  webServer: {
    command: 'jlpm start',
    url: 'http://localhost:8888/lab',
    timeout: 120 * 1000,
    reuseExistingServer: !process.env.CI,
    env: {
      ...process.env,
      DEEPAGENT_AGENT_SPEC: `${path.resolve(__dirname, 'stub_agent.py')}:graph`
    }
  }
};
