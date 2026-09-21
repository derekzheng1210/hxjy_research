// 数据目录集中定义：环境变量 BOND_DASHBOARD_DATA_DIR 可整体外置
// （门户生产部署统一指向 PORTAL_DATA_ROOT/bond_dashboard，与门户其他模块
// 的数据外置约定一致）；未设置时回退项目内 ./data（独立部署包行为不变）。
import path from "node:path";

export const DATA_DIR = process.env.BOND_DASHBOARD_DATA_DIR
  ? path.resolve(process.env.BOND_DASHBOARD_DATA_DIR)
  : path.join(process.cwd(), "data");
