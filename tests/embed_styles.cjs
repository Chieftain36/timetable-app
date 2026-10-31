const fs = require('fs');
const path = 'index.html';
const css = fs.readFileSync('styles.generated.css','utf8');
const block = `<!-- compiled-styles:start -->\n<style>${css}</style>\n<!-- compiled-styles:end -->`;
let html = fs.readFileSync(path,'utf8');
if (html.includes('<!-- compiled-styles:start -->')) {
  html = html.replace(/<!-- compiled-styles:start -->[\s\S]*?<!-- compiled-styles:end -->/,()=>block);
} else {
  html = html.replace(/<script src="https:\/\/cdn.jsdelivr.net\/npm\/@tailwindcss\/browser@4"><\/script>/,()=>block);
}
fs.writeFileSync(path,html);