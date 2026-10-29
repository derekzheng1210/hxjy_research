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

// ---- 管理后台：删除用户保留历史数据（软删除 + 恢复） ----
const adminSource = fs.readFileSync('internal_knowledge_base/js/admin.js', 'utf8');

assert(adminSource.includes('/api/admin/users/${id}/restore'), 'restore endpoint must be wired into admin API');
assert(adminSource.includes('state.deletedUsers = data.deletedUsers'), 'admin state must track deleted users');
assert(adminSource.includes('deletedUserRow'), 'deleted users must render as their own table rows');
assert(adminSource.includes("data-action=\"restore-user\""), 'deleted user rows must offer a restore action');
assert(adminSource.includes('已删除用户'), 'admin user view must include a deleted-users section');
assert(adminSource.includes('confirm-restore-user'), 'restore must go through a confirmation modal');
assert(adminSource.includes('历史报告、评分等记录保留并显示原姓名'), 'deleted section must explain history retention with names');
assert(adminSource.includes('其历史上传报告、评分、点赞等记录会全部保留'), 'delete confirmation must state full retention');
assert(adminSource.includes('登录密码将重置为默认密码'), 'restore confirmation must disclose the password reset');
assert(!adminSource.includes('显示为"已删除用户"'), 'stale copy claiming ratings degrade to 已删除用户 must be gone');
const deleteModalStart = adminSource.indexOf('function showUserDeleteModal');
const deleteModalEnd = adminSource.indexOf('async function confirmDeleteUser', deleteModalStart);
assert(deleteModalStart >= 0 && deleteModalEnd > deleteModalStart, 'delete user modal renderer is missing');
const deleteModalSource = adminSource.slice(deleteModalStart, deleteModalEnd);
assert(!deleteModalSource.includes('不可撤销'), 'user deletion is reversible via restore; modal must not claim otherwise');

console.log('internal knowledge-base frontend renderer tests passed');
