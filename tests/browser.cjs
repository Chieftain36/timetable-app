const {chromium} = require('playwright');

// CI may use a system Chromium; otherwise use Playwright's managed browser.
module.exports = {
  chromium: {
    launch(options = {}) {
      const executablePath = process.env.BROWSER_EXECUTABLE_PATH;
      return chromium.launch({...options, ...(executablePath ? {executablePath} : {})});
    },
  },
};
