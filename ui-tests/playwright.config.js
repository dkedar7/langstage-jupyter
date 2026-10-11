/**
 * Galata/Playwright config for langstage-jupyter UI tests.
 *
 * Inherits Galata's base config and starts two JupyterLab servers wired to the
 * model-free stub agent: the Galata one at the root (`start`), and one under a
 * non-root base_url (`start:base-url`, gh #170) for tests/base-url.spec.ts.
 * DEEPAGENT_AGENT_SPEC is also set here so it reaches the server process regardless
 * of how it's spawned.
 */
const path = require('path');
const baseConfig = require('@jupyterlab/galata/lib/playwright-config');

const server = (command, url) => ({
  command,
  url,
  timeout: 120 * 1000,
  reuseExistingServer: !process.env.CI,
  env: {
    ...process.env,
    DEEPAGENT_AGENT_SPEC: `${path.resolve(__dirname, 'stub_agent.py')}:graph`
  }
});

module.exports = {
  ...baseConfig,
  timeout: 120 * 1000,
  webServer: [
    server('jlpm start', 'http://localhost:8888/lab'),
    server('jlpm start:base-url', 'http://localhost:8889/user/alice/lab')
  ]
};
