const assert = require('assert');
const fs = require('fs');

const source = fs.readFileSync('internal_knowledge_base/js/app.js', 'utf8');
const start = source.indexOf('function renderKnowledgeInline');
const end = source.indexOf('async function submitKnowledgeQuestion', start);
assert(start >= 0 && end > start, 'knowledge answer renderer is missing');

const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));
eval(source.slice(start, end));

const html = renderKnowledgeAnswer(`## 核心判断

结论来自《AI产业趋势报告》，正文不应拥挤。

### 报告依据

1. **盈利趋势**：仍在延续。

* 估值压力正在上升。`);

assert(html.includes('<h3>核心判断</h3>'), 'level-two heading should render as a visual section');
assert(html.includes('<h4>报告依据</h4>'), 'level-three heading should render as a subsection');
assert(html.includes('<span class="knowledge-citation">《AI产业趋势报告》</span>'), 'real report title should be highlighted');
assert(html.includes('<div class="knowledge-numbered">'), 'numbered points should use structured layout');
assert(html.includes('<div class="knowledge-bullet">'), 'asterisk bullets should render without raw markdown');
assert(!html.includes('<br>'), 'paragraph spacing should be controlled by semantic blocks, not hard breaks');
assert(source.includes('id="knowledgeModeSelect"'), 'answer mode should use the compact composer dropdown');
assert(source.includes('knowledge-thinking-compact'), 'thinking should use a compact composer control');
assert(!source.includes('knowledge-mode-switch'), 'large top-level mode cards should be removed');

console.log('internal knowledge-base frontend renderer tests passed');
