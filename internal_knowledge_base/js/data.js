/* 内部知识库：前端配置常量

说明：用户、报告、评分、密码等业务数据由服务端 SQLite 数据库统一管理，
前端通过模块 API 接口读写，不再在本地硬编码或使用 localStorage/IndexedDB。
本文件仅保留与展示相关的配置常量。
*/

const APP_CONFIG = Object.freeze({
  name: '内部知识库',
  defaultPassword: '123456',
  scoreDimensions: [
    { key: 'inspiration', label: '投资启发性', description: '是否能形成新的投资判断、线索或策略思路' },
    { key: 'depth', label: '研究深度', description: '论证是否充分，数据与逻辑是否扎实、深入' },
    { key: 'utility', label: '实用性', description: '结论是否清晰，并能有效支持实际投资工作' }
  ]
});

const REPORT_CATEGORIES = Object.freeze({
  weekly: { label: '周报', short: '周报', scored: false, tone: 'blue' },
  monthly: { label: '月报', short: '月报', scored: true, tone: 'indigo' },
  deep: { label: '深度报告', short: '深度', scored: true, tone: 'violet' },
  other: { label: '其他报告', short: '其他', scored: false, tone: 'slate' }
});

// 报告主题分类：宏观利率、信用、多资产、量化、固收+、权益、其他
const REPORT_THEMES = Object.freeze({
  macro_rate: { label: '宏观利率', short: '宏观', tone: 'sky' },
  credit: { label: '信用', short: '信用', tone: 'amber' },
  multi_asset: { label: '多资产', short: '多资产', tone: 'emerald' },
  quant: { label: '量化', short: '量化', tone: 'cyan' },
  fixed_income_plus: { label: '固收+', short: '固收+', tone: 'rose' },
  equity: { label: '权益', short: '权益', tone: 'orange' },
  other: { label: '其他', short: '其他', tone: 'slate' }
});

window.InternalLibraryData = Object.freeze({
  config: APP_CONFIG,
  categories: REPORT_CATEGORIES,
  themes: REPORT_THEMES
});
