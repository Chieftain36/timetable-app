const fs = require('fs');
const script = fs.readFileSync('index.html', 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
fs.writeFileSync('tests/frontend.js', script);
